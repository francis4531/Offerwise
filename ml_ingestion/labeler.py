"""Base class for Claude-powered labeling/extraction (Streams 1 & 3).

A labeler uses the Anthropic API to either:
  - Extract structured findings from unstructured text (Stream 1: re-extraction)
  - Re-classify existing findings with better labels (Stream 3: relabeling)

Subclasses implement:
  get_batch() -> list[dict]
      Return the next batch of items to label. Each dict has 'id' plus
      whatever context the labeler needs (e.g. 'text', 'pdf_path').
      Return empty list when done.

  build_prompt(batch: list[dict]) -> str
      Render the prompt to send to Claude.

  parse_response(response_text: str, batch: list[dict]) -> list[dict]
      Parse Claude's JSON response back into structured results. Each result
      dict needs the fields required by save_result().

  save_result(item_id: int, result: dict) -> None
      Persist one result — typically calls self._add_finding() or
      self._update_labels() on the base.

Base class handles:
  - Batching with configurable size
  - JSON truncation detection (lesson from the augmentation bug)
  - Rate limit handling with retries
  - API cost tracking / logging
  - Graceful degradation when API key is missing

Default batch size 20 keeps responses well under max_tokens so we don't
repeat the v5.86.73 truncation mistake.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from ml_ingestion.base import BaseIngestionJob


class BaseLabeler(BaseIngestionJob):
    """Base class for Claude-based labelers and extractors.

    Subclasses set:
      JOB_TYPE: str  — 'reextract' or 'relabel'
      SOURCE_NAME: str  — version tag
      BATCH_SIZE: int = 20
      MODEL: str = 'claude-sonnet-4-20250514'
      MAX_TOKENS: int = 8000

    And implement: get_batch(), build_prompt(), parse_response(), save_result()
    """

    BATCH_SIZE = 20
    MODEL = 'claude-sonnet-4-20250514'
    MAX_TOKENS = 8000
    MAX_BATCHES_PER_RUN: Optional[int] = None  # None = no cap

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self._client = None
        self._api_calls = 0
        self._api_errors = 0

    # ── Subclass hooks ────────────────────────────────────────────────
    def get_batch(self) -> list[dict]:
        """Return the next batch of items to label. Empty = done."""
        raise NotImplementedError('subclass must implement get_batch()')

    def build_prompt(self, batch: list[dict]) -> str:
        """Render the prompt for this batch."""
        raise NotImplementedError('subclass must implement build_prompt()')

    def parse_response(self, response_text: str, batch: list[dict]) -> list[dict]:
        """Parse Claude's response into structured results.

        Default implementation assumes JSON array response; subclasses can
        override for different formats. Each result should include an 'id'
        that maps back to the batch input.
        """
        raw = response_text.strip()
        # Strip common markdown code fences
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[-1] if '\n' in raw else raw[3:]
        if raw.endswith('```'):
            raw = raw[:-3]
        raw = raw.strip()
        if raw.startswith('json'):
            raw = raw[4:].strip()
        return json.loads(raw)

    def save_result(self, item_id: int, result: dict) -> None:
        """Persist one result. Subclasses implement the DB write."""
        raise NotImplementedError('subclass must implement save_result()')

    # ── Base orchestration ────────────────────────────────────────────
    def run_job(self) -> None:
        self._client = self._init_client()
        if not self._client:
            self._log('No ANTHROPIC_API_KEY — skipping labeler run', 'warn')
            return

        batch_count = 0
        while True:
            if self.MAX_BATCHES_PER_RUN and batch_count >= self.MAX_BATCHES_PER_RUN:
                self._log(f'Hit MAX_BATCHES_PER_RUN ({self.MAX_BATCHES_PER_RUN}), stopping')
                break

            batch = self.get_batch()
            if not batch:
                self._log('No more batches, done')
                break

            batch_count += 1
            self._log(f'Batch {batch_count}: {len(batch)} items')
            self._process_batch(batch)
            self._finalize()

        self._log(f'Labeling complete: {batch_count} batches, {self._api_calls} API calls, '
                  f'{self._api_errors} API errors')

    # ── Internal helpers ──────────────────────────────────────────────
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

    def _process_batch(self, batch: list[dict]) -> None:
        """Send batch to Claude, parse response, save results."""
        prompt = self.build_prompt(batch)

        try:
            response = self._call_api(prompt)
            if response is None:
                return
            results = self.parse_response(response, batch)
        except json.JSONDecodeError as e:
            # Dedicated handling for the truncation bug we hit in v5.86.73
            self._log(f'JSON parse failed (likely truncation at {self.MAX_TOKENS} tokens): {e}', 'error')
            self._api_errors += 1
            return
        except Exception as e:
            self._log(f'Batch processing failed: {e}', 'error')
            self._api_errors += 1
            return

        # Map results back to items
        by_id = {r.get('id'): r for r in results if isinstance(r, dict) and 'id' in r}
        for item in batch:
            item_id = item.get('id')
            result = by_id.get(item_id)
            if result:
                try:
                    self.save_result(item_id, result)
                except Exception as save_err:
                    self._log(f'Save error for item {item_id}: {save_err}', 'warn')

    def _call_api(self, prompt: str) -> Optional[str]:
        """Call Claude API with retry on rate limits."""
        for attempt in range(3):
            try:
                resp = self._client.messages.create(
                    model=self.MODEL,
                    max_tokens=self.MAX_TOKENS,
                    messages=[{'role': 'user', 'content': prompt}],
                )
                self._api_calls += 1

                # Detect truncation — warn so we know to reduce BATCH_SIZE
                if hasattr(resp, 'stop_reason') and resp.stop_reason == 'max_tokens':
                    self._log(f'Response hit max_tokens={self.MAX_TOKENS}, '
                              f'consider reducing BATCH_SIZE (current={self.BATCH_SIZE})', 'warn')

                return resp.content[0].text if resp.content else None
            except Exception as e:
                err_msg = str(e).lower()
                if 'rate_limit' in err_msg or '429' in err_msg:
                    delay = 2 ** attempt * 10
                    self._log(f'Rate limited, waiting {delay}s', 'warn')
                    time.sleep(delay)
                    continue
                self._log(f'API call failed: {e}', 'error')
                self._api_errors += 1
                return None
        return None
