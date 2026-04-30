"""Base class for all data ingestion jobs.

An "ingestion job" is any background task that adds rows to ml_finding_labels:
  - Re-extraction (Stream 1): parse user-uploaded PDFs into findings
  - Crawling (Stream 2): fetch findings from public web sources
  - Relabeling (Stream 3): improve labels on existing rows via Claude

This base class handles the plumbing that every ingestion job needs:
  - Creating an MLIngestionJob row and updating its status
  - Timing / elapsed seconds tracking
  - Structured logging (rows_added, rows_rejected, log entries)
  - Error capture (status='failed' + traceback)
  - Deduplication against existing data

Subclasses implement `run_job()` — the actual work. They should call
`self._log()` for progress updates and `self._add_finding()` or
`self._update_finding()` to write data.
"""
from __future__ import annotations

import json
import logging
import time
import traceback
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


class BaseIngestionJob:
    """Base class for re-extraction, crawl, and relabel jobs.

    Concrete subclasses must set:
      JOB_TYPE: str     — 'reextract' | 'crawl' | 'relabel'
      SOURCE_NAME: str  — version tag, e.g. 'ai_parse_v2', 'zillow_v1'

    And implement:
      run_job(self) -> None
        Does the actual work. Called from within run(). Should call
        self._log(), self._add_finding(), etc.
    """

    JOB_TYPE: str = 'unknown'
    SOURCE_NAME: str = 'unknown'

    def __init__(self, config: Optional[dict] = None):
        """config: optional dict of job parameters; serialized to config_json."""
        self.config = config or {}
        self._log_entries: list[dict] = []
        self._job_id: Optional[int] = None
        self._t_start: float = 0.0
        self._rows_processed: int = 0
        self._rows_added: int = 0
        self._rows_rejected: int = 0
        # v5.87.14: incremental progress commits. _last_flush is the
        # monotonic time of the last persistence to the DB. Without this
        # debounce, log_json was only written on job completion, so the
        # admin UI was blind to mid-flight state.
        self._last_flush: float = 0.0
        self._FLUSH_INTERVAL_SECONDS: float = 5.0
        # Subclasses can populate this so the UI can compute "X of N" bars.
        # Optional — None means progress is shown as indeterminate.
        self._expected_total: Optional[int] = None
        # Subclasses can populate a short human-readable phase string
        # (e.g. "Fetching FEMA disaster declarations") — surfaced in UI.
        self._current_phase: str = ''

    # ── Subclass hooks ────────────────────────────────────────────────
    def run_job(self) -> None:
        """Implement in subclasses. Do the actual data work here."""
        raise NotImplementedError('subclass must implement run_job()')

    # ── Orchestration (call this from callers) ────────────────────────
    def run(self) -> int:
        """Run the job end-to-end. Returns the MLIngestionJob id.

        Safe for any exception — captures to the DB instead of propagating.
        Guarantees: a job row exists with status=succeeded|failed when this
        returns.
        """
        from models import db, MLIngestionJob

        job = MLIngestionJob(
            job_type=self.JOB_TYPE,
            source=self.SOURCE_NAME,
            status='running',
            started_at=datetime.utcnow(),
            config_json=json.dumps(self.config) if self.config else None,
        )
        db.session.add(job)
        db.session.commit()
        self._job_id = job.id
        self._t_start = time.time()

        self._log(f'Starting {self.JOB_TYPE} job: {self.SOURCE_NAME}')

        try:
            self.run_job()
            job.status = 'succeeded'
            self._log(f'Job succeeded: added={self._rows_added}, rejected={self._rows_rejected}')
        except Exception as e:
            job.status = 'failed'
            job.error = f'{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}'
            self._log(f'Job FAILED: {e}', level='error')
            logger.exception(f'Ingestion job {self.SOURCE_NAME} failed')

        job.completed_at = datetime.utcnow()
        job.elapsed_seconds = round(time.time() - self._t_start, 2)
        job.rows_processed = self._rows_processed
        job.rows_added = self._rows_added
        job.rows_rejected = self._rows_rejected
        # v5.87.14: write the same envelope shape that mid-flight flushes use.
        # Readers should always see {entries, phase, expected_total}, never
        # a bare list (which would be ambiguous to UI parsing).
        final_payload = {
            'entries': self._log_entries[-200:] if len(self._log_entries) > 200 else self._log_entries,
            'phase': self._current_phase,
            'expected_total': self._expected_total,
        }
        job.log_json = json.dumps(final_payload)
        db.session.commit()

        return job.id

    # ── Helpers for subclasses ────────────────────────────────────────
    def _log(self, msg: str, level: str = 'info') -> None:
        """Append a log entry. Persisted to the job row on completion AND
        debounced every ~5s mid-flight so the admin UI can poll progress.
        """
        elapsed = round(time.time() - self._t_start, 1) if self._t_start else 0.0
        self._log_entries.append({'t': elapsed, 'msg': msg, 'level': level})
        # Also log to stdout so it shows in Render logs
        if level == 'error':
            logger.error(f'[{self.SOURCE_NAME}] {msg}')
        elif level == 'warn':
            logger.warning(f'[{self.SOURCE_NAME}] {msg}')
        else:
            logger.info(f'[{self.SOURCE_NAME}] {msg}')
        # Debounced flush
        self._maybe_flush_progress()

    def _set_phase(self, phase: str) -> None:
        """Set a short human-readable phase string ("Fetching FEMA...",
        "Encoding text...") that surfaces in the admin UI as a status hint.
        Triggers a progress flush so the operator sees the new phase quickly.
        """
        self._current_phase = phase[:200]  # cap so it fits cleanly in UI
        self._log(f'Phase: {phase}')
        # Phase changes are rare relative to log lines — force a flush
        # so the UI updates promptly when the operator changes views.
        self._flush_progress()

    def _maybe_flush_progress(self) -> None:
        """Persist mid-flight progress to the DB if enough time has passed
        since the last flush. Cheap no-op if recently flushed.
        """
        now = time.time()
        if now - self._last_flush < self._FLUSH_INTERVAL_SECONDS:
            return
        self._flush_progress()

    def _flush_progress(self) -> None:
        """Force-commit the current job state to the DB. Safe to call from
        anywhere mid-job. If anything goes wrong (DB lock, etc.) it logs
        and continues — progress visibility is nice-to-have, not critical.
        """
        if not self._job_id:
            return
        try:
            from models import db, MLIngestionJob
            job = MLIngestionJob.query.get(self._job_id)
            if not job:
                return
            job.rows_processed = self._rows_processed
            job.rows_added = self._rows_added
            job.rows_rejected = self._rows_rejected
            # Cap log_json size — last 200 entries is plenty for UI tail.
            tail = self._log_entries[-200:] if len(self._log_entries) > 200 else self._log_entries
            payload = {
                'entries': tail,
                'phase': self._current_phase,
                'expected_total': self._expected_total,
            }
            job.log_json = json.dumps(payload)
            db.session.commit()
            self._last_flush = time.time()
        except Exception as e:
            # Don't let a flush failure crash the job. Roll back any partial.
            try:
                from models import db
                db.session.rollback()
            except Exception:
                pass
            logger.warning(f'progress flush failed (non-fatal): {e}')

    def _add_finding(
        self,
        finding_text: str,
        category: str,
        severity: str,
        *,
        confidence: float = 0.85,
        geographic_region: Optional[str] = None,
        property_age_bucket: Optional[str] = None,
        skip_dedup: bool = False,
    ) -> bool:
        """Add a new finding to ml_finding_labels.

        Returns True if added, False if rejected (dedup hit or invalid data).
        Increments self._rows_added or self._rows_rejected accordingly.

        Deduplication: skips if the exact finding_text already exists for this
        source_version. Pass skip_dedup=True to bypass (rarely needed).
        """
        from models import db, MLFindingLabel

        self._rows_processed += 1

        # Basic validation
        text = (finding_text or '').strip()
        if len(text) < 10:
            self._rows_rejected += 1
            return False
        if not category or not severity:
            self._rows_rejected += 1
            return False
        if severity.lower() not in ('critical', 'major', 'moderate', 'minor'):
            self._rows_rejected += 1
            return False

        # Dedup check (same text + same source_version)
        if not skip_dedup:
            existing = MLFindingLabel.query.filter_by(
                finding_text=text,
                source_version=self.SOURCE_NAME,
            ).first()
            if existing:
                self._rows_rejected += 1
                return False

        row = MLFindingLabel(
            finding_text=text,
            category=category.lower().strip(),
            severity=severity.lower().strip(),
            source=self.SOURCE_NAME,  # keep source aligned with source_version for now
            source_version=self.SOURCE_NAME,
            confidence=confidence,
            geographic_region=geographic_region,
            property_age_bucket=property_age_bucket,
        )
        db.session.add(row)

        self._rows_added += 1

        # Commit in batches of 100 to keep transactions small and enable progress
        if self._rows_added % 100 == 0:
            db.session.commit()
            self._log(f'Progress: added {self._rows_added}, rejected {self._rows_rejected}')

        return True

    def _update_labels(
        self,
        row_id: int,
        *,
        category_v2: Optional[str] = None,
        severity_v2: Optional[str] = None,
        is_real_finding: Optional[bool] = None,
        labeling_confidence: Optional[float] = None,
        labeling_notes: Optional[str] = None,
    ) -> bool:
        """Update v2 labels on an existing MLFindingLabel row (for Stream 3).

        Returns True if updated, False if the row wasn't found.
        """
        from models import db, MLFindingLabel

        self._rows_processed += 1
        row = db.session.get(MLFindingLabel, row_id)
        if not row:
            self._rows_rejected += 1
            return False

        if category_v2 is not None:
            row.category_v2 = category_v2.lower().strip()
        if severity_v2 is not None:
            row.severity_v2 = severity_v2.lower().strip()
        if is_real_finding is not None:
            row.is_real_finding = is_real_finding
        if labeling_confidence is not None:
            row.labeling_confidence = labeling_confidence
        if labeling_notes is not None:
            row.labeling_notes = labeling_notes[:500]  # cap to prevent huge rows

        self._rows_added += 1  # "added" in the sense of "labels successfully applied"

        if self._rows_added % 100 == 0:
            db.session.commit()
            self._log(f'Progress: labeled {self._rows_added}')

        return True

    def _finalize(self) -> None:
        """Called automatically at end of run(), but subclasses can call early
        to ensure the last batch is committed before they exit run_job()."""
        from models import db
        db.session.commit()
