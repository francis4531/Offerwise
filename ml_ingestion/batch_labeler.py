"""Batch API version of BaseLabeler.

Uses Anthropic's Batch API (50% discount on all tokens) instead of synchronous
per-request calls. Appropriate for jobs that process thousands of items where
end-to-end latency doesn't matter but cost does.

Architecture (different from BaseLabeler):
  1. submit_batch():    build all requests, POST to /v1/messages/batches
                        store batch_id in MLIngestionJob.config_json
  2. check_status():    poll /v1/messages/batches/{id}
                        when 'ended', fetch results_url
  3. process_results(): stream JSONL results, call save_result() per item

The background thread that runs this job spends most of its time sleeping
(polling every 60s). Anthropic guarantees batch completion within 24 hours;
typical runtime for a few thousand requests is 5-30 minutes.

Subclass contract matches BaseLabeler where possible, but the critical
differences are:

  - BATCH_SIZE: how many findings per individual request sent to Claude
    (e.g. 20 findings per Claude call, so Claude labels 20 at once)

  - BATCH_POLL_INTERVAL_SECONDS: how often to check batch status (default 60)

  - MAX_BATCH_WAIT_SECONDS: safety ceiling to give up if batch stalls
    (default 3 hours — well below Anthropic's 24h guarantee)

  - MODEL: default 'claude-haiku-4-5'

Pricing note: cache_control is NOT used in this class. Haiku 4.5 requires
4,096 tokens minimum for caching to activate, and our relabel prompt is ~1,500.
Batch API alone gets us the 50% discount we care about.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Optional

from ml_ingestion.base import BaseIngestionJob


class BaseBatchLabeler(BaseIngestionJob):
    """Base class for Batch API based labelers / extractors.

    Subclasses implement the same hooks as BaseLabeler:
      get_batch() -> list[dict]
      build_prompt(batch) -> str
      parse_response(text, batch) -> list[dict]  (default JSON array)
      save_result(item_id, result) -> None
    """

    BATCH_SIZE = 20
    MODEL = 'claude-haiku-4-5'
    MAX_TOKENS = 4096  # per individual request — output only
    MAX_BATCHES_PER_RUN: Optional[int] = None

    # Batch API specific config
    BATCH_POLL_INTERVAL_SECONDS = 60
    MAX_BATCH_WAIT_SECONDS = 3 * 3600  # 3 hours

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self._client = None
        self._api_batch_id: Optional[str] = None
        self._item_id_by_custom_id: dict[str, int] = {}

    # ── Subclass hooks (same contract as BaseLabeler) ─────────────────
    def get_batch(self) -> list[dict]:
        raise NotImplementedError('subclass must implement get_batch()')

    def build_prompt(self, batch: list[dict]) -> str:
        raise NotImplementedError('subclass must implement build_prompt()')

    def parse_response(self, response_text: str, batch: list[dict]) -> list[dict]:
        """Default: parse as JSON array. Subclasses can override."""
        raw = response_text.strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[-1] if '\n' in raw else raw[3:]
        if raw.endswith('```'):
            raw = raw[:-3]
        raw = raw.strip()
        if raw.startswith('json'):
            raw = raw[4:].strip()
        return json.loads(raw)

    def save_result(self, item_id: int, result: dict) -> None:
        raise NotImplementedError('subclass must implement save_result()')

    # ── Base orchestration ────────────────────────────────────────────
    def run_job(self) -> None:
        """Full lifecycle: collect → submit → poll → process.

        This runs in a background thread and may take 5-60 minutes depending
        on Anthropic's batch queue depth. Most of the time is spent sleeping
        during the poll loop.
        """
        self._client = self._init_client()
        if not self._client:
            self._log('No ANTHROPIC_API_KEY — skipping batch labeler run', 'warn')
            return

        # Step 1: collect all batches into a single API submission
        requests = self._collect_all_requests()
        if not requests:
            self._log('No items to process, done')
            return

        self._log(f'Submitting {len(requests)} requests to Batch API')

        # Step 2: submit to Batch API
        batch_id = self._submit_batch(requests)
        if not batch_id:
            raise RuntimeError('Batch submission failed')
        self._api_batch_id = batch_id
        self._log(f'Batch submitted: {batch_id}')

        # Step 3: poll until complete (or timeout)
        results_url = self._wait_for_batch(batch_id)
        if not results_url:
            raise RuntimeError('Batch timed out or errored')

        # Step 4: fetch + process results
        self._log(f'Downloading results from {results_url}')
        self._process_results(results_url)

        self._finalize()

    def _collect_all_requests(self) -> list[dict]:
        """Walk through get_batch() until empty, building the full list of
        requests to submit to the Batch API as a single call."""
        all_requests = []
        batch_index = 0
        while True:
            if self.MAX_BATCHES_PER_RUN and batch_index >= self.MAX_BATCHES_PER_RUN:
                self._log(f'Hit MAX_BATCHES_PER_RUN ({self.MAX_BATCHES_PER_RUN})')
                break

            batch = self.get_batch()
            if not batch:
                break

            batch_index += 1
            prompt = self.build_prompt(batch)

            # Anthropic's Batch API uses 'custom_id' to correlate request → result
            # We use the format 'b{batch_index}_i{first_item_id}' so we can later
            # map each result back to its batch items.
            first_id = batch[0]['id'] if batch else 0
            custom_id = f'b{batch_index}_i{first_id}'

            # Remember which item IDs are in this batch — needed when we get the
            # response back (it only has custom_id, not the item list).
            self._item_id_by_custom_id[custom_id] = [item['id'] for item in batch]
            # Also preserve the batch dicts for parse_response to consume
            self._item_id_by_custom_id[f'{custom_id}_items'] = batch

            all_requests.append({
                'custom_id': custom_id,
                'params': {
                    'model': self.MODEL,
                    'max_tokens': self.MAX_TOKENS,
                    'messages': [{'role': 'user', 'content': prompt}],
                },
            })

            if batch_index % 100 == 0:
                self._log(f'  Prepared {batch_index} batches so far')

        self._log(f'Total batches prepared: {len(all_requests)}')
        return all_requests

    def _submit_batch(self, requests: list[dict]) -> Optional[str]:
        """Submit a list of requests as a single Batch API call.

        Returns the batch_id (e.g. 'msgbatch_abc123') for polling.
        """
        try:
            response = self._client.messages.batches.create(requests=requests)
            return response.id
        except Exception as e:
            self._log(f'Batch submit failed: {e}', 'error')
            return None

    def _wait_for_batch(self, batch_id: str) -> Optional[str]:
        """Poll batch status every BATCH_POLL_INTERVAL_SECONDS until it's done.

        Returns the results_url when processing_status='ended', or None on
        timeout / error.
        """
        deadline = time.time() + self.MAX_BATCH_WAIT_SECONDS
        poll_count = 0
        while time.time() < deadline:
            try:
                status = self._client.messages.batches.retrieve(batch_id)
            except Exception as e:
                self._log(f'Status poll error (will retry): {e}', 'warn')
                time.sleep(self.BATCH_POLL_INTERVAL_SECONDS)
                continue

            poll_count += 1
            processing = getattr(status, 'processing_status', 'unknown')
            counts = getattr(status, 'request_counts', None)

            if poll_count == 1 or poll_count % 5 == 0:
                # Log progress: total / succeeded / errored / expired / canceled
                summary = processing
                if counts:
                    summary += f' (processing={counts.processing}, succeeded={counts.succeeded}, errored={counts.errored})'
                self._log(f'Poll {poll_count}: {summary}')

            if processing == 'ended':
                results_url = getattr(status, 'results_url', None)
                if not results_url:
                    self._log('Batch ended but no results_url', 'error')
                    return None
                return results_url

            time.sleep(self.BATCH_POLL_INTERVAL_SECONDS)

        self._log(f'Batch timed out after {self.MAX_BATCH_WAIT_SECONDS}s', 'error')
        return None

    def _process_results(self, results_url: str) -> None:
        """Fetch the JSONL results file and save each item.

        The SDK's results() helper handles the URL fetch + parse, but we
        want to stream rather than load everything into memory for big jobs.
        """
        try:
            # SDK method: returns iterator of MessageBatchIndividualResponse
            result_iter = self._client.messages.batches.results(self._api_batch_id)
        except Exception as e:
            self._log(f'Failed to fetch results: {e}', 'error')
            raise

        items_saved = 0
        items_failed = 0
        items_skipped = 0

        for item_response in result_iter:
            custom_id = getattr(item_response, 'custom_id', None)
            result_type = getattr(item_response.result, 'type', None) if item_response.result else None

            if result_type != 'succeeded':
                items_failed += 1
                # Log first few errors so we can diagnose systemic issues
                if items_failed <= 5:
                    err = getattr(item_response.result, 'error', None)
                    self._log(f'Batch item {custom_id} failed: {err}', 'warn')
                continue

            # Get the batch items that this response corresponds to
            batch_items = self._item_id_by_custom_id.get(f'{custom_id}_items', [])
            if not batch_items:
                items_skipped += 1
                continue

            # Extract Claude's response text from the succeeded message
            message = item_response.result.message
            response_text = message.content[0].text if message.content else ''

            try:
                results = self.parse_response(response_text, batch_items)
            except Exception as e:
                self._log(f'Parse failed for {custom_id}: {e}', 'warn')
                items_failed += len(batch_items)
                continue

            # Results come back as an array; map them to items by id
            results_by_id = {r.get('id'): r for r in results if isinstance(r, dict) and 'id' in r}
            for item in batch_items:
                item_id = item['id']
                result = results_by_id.get(item_id)
                if result:
                    try:
                        self.save_result(item_id, result)
                        items_saved += 1
                    except Exception as save_err:
                        self._log(f'Save error for item {item_id}: {save_err}', 'warn')
                        items_failed += 1
                else:
                    items_skipped += 1

            # Commit every 500 items saved to keep transactions tractable
            if items_saved and items_saved % 500 == 0:
                self._finalize()
                self._log(f'Progress: {items_saved} saved, {items_failed} failed, {items_skipped} skipped')

        self._log(f'Results processed: saved={items_saved}, failed={items_failed}, skipped={items_skipped}')

    def _init_client(self):
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            return None
        try:
            from anthropic import Anthropic
            return Anthropic(api_key=api_key)
        except Exception as e:
            self._log(f'Failed to init Anthropic client: {e}', 'error')
            return None
