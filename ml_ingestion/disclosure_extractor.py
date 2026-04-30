"""BaseDisclosureExtractor — abstract class for extracting findings from
public disclosure documents (Path 5).

Status (v5.86.94): SCAFFOLD. No concrete subclasses yet.

Background:
  When a disclosure source is verified viable (see disclosure_investigator.py),
  we need to turn the source's documents into ml_finding_labels rows. Each
  source has its own document format, but the extraction flow is the same:

    1. Fetch raw document(s) for a property
    2. Extract plain text (maybe OCR if scanned)
    3. Parse structured sections where possible
    4. Identify finding-like free-text passages
    5. Classify via Claude (Batch API) into category/severity
    6. Write to ml_finding_labels

  This class provides the skeleton. Each source subclass overrides the parts
  that differ (fetching, OCR needs, section structure).

Contract:
  Subclasses implement:
    get_property_ids() -> list[str]
        Yield the source-specific property identifiers we want to process.
        E.g. docket numbers, parcel IDs, case IDs.

    fetch_document(property_id: str) -> Optional[bytes]
        Download the raw document for one property. Returns PDF/HTML bytes
        or None if unavailable.

    extract_text(property_id: str, raw_bytes: bytes) -> str
        Convert raw bytes to plain text. Default implementation handles PDFs
        via pypdf; subclasses override for OCR or HTML parsing.

    identify_findings(property_id: str, plain_text: str) -> list[str]
        Given full document text, extract finding-like passages as a list of
        free-text strings. Each string is one candidate finding. Categorization
        happens downstream via the re-labeler.

  Base class handles:
    - Orchestration (fetch → extract → identify loop)
    - Rate limiting between fetches
    - Staging as unlabeled rows (category_v2=NULL) for re-labeler to finalize
    - Job tracking

Naming convention:
  Concrete subclasses: {StateCode}{SourceType}Extractor
    ChicagoDisclosureExtractor
    FLSunshineExtractor
    PACERDisclosureExtractor

Status enum same as SocrataCrawler:
  'scaffold' → blocks run_job()
  'active'   → allowed to run
"""
from __future__ import annotations

import time
from typing import Optional

from ml_ingestion.base import BaseIngestionJob


class BaseDisclosureExtractor(BaseIngestionJob):
    """Abstract base for extracting findings from public disclosure documents."""

    JOB_TYPE = 'crawl'  # shared job_type with crawlers for now; can split later

    # Required on subclasses
    SOURCE_NAME: str = ''        # e.g. 'chicago_disclosure_v1'
    STATE_CODE: str = ''         # e.g. 'IL'

    # Optional with defaults
    REQUEST_DELAY_SECONDS: float = 2.0
    MAX_PROPERTIES_PER_RUN: Optional[int] = None
    STATUS: str = 'scaffold'     # must be 'active' to actually execute

    # ── Subclass hooks ────────────────────────────────────────────────
    def get_property_ids(self) -> list[str]:
        raise NotImplementedError('subclass must implement get_property_ids()')

    def fetch_document(self, property_id: str) -> Optional[bytes]:
        raise NotImplementedError('subclass must implement fetch_document()')

    def extract_text(self, property_id: str, raw_bytes: bytes) -> str:
        """Default: attempt PDF text extraction. Override for scanned PDFs or HTML."""
        try:
            from pypdf import PdfReader
            import io
            reader = PdfReader(io.BytesIO(raw_bytes))
            return '\n\n'.join(page.extract_text() or '' for page in reader.pages)
        except Exception as e:
            self._log(f'PDF extraction failed: {e}', 'warn')
            return ''

    def identify_findings(self, property_id: str, plain_text: str) -> list[str]:
        raise NotImplementedError('subclass must implement identify_findings()')

    # ── Base orchestration ────────────────────────────────────────────
    def run_job(self) -> None:
        if self.STATUS != 'active':
            self._log(f'{type(self).__name__} is status={self.STATUS!r} — refusing to run.', 'error')
            return
        if not self.SOURCE_NAME or not self.STATE_CODE:
            self._log(f'{type(self).__name__} missing SOURCE_NAME or STATE_CODE', 'error')
            return

        property_ids = self.get_property_ids()
        if self.MAX_PROPERTIES_PER_RUN:
            property_ids = property_ids[:self.MAX_PROPERTIES_PER_RUN]
        self._log(f'Processing {len(property_ids)} properties')

        for i, pid in enumerate(property_ids, 1):
            try:
                raw = self.fetch_document(pid)
                if not raw:
                    self._rows_rejected += 1
                    continue
                text = self.extract_text(pid, raw)
                if not text or len(text) < 100:
                    self._rows_rejected += 1
                    continue
                findings = self.identify_findings(pid, text)
                for finding_text in findings:
                    if self._add_unlabeled_finding(finding_text, external_id=pid):
                        pass  # counter incremented inside
            except Exception as e:
                self._log(f'Error processing {pid}: {e}', 'warn')
                self._rows_rejected += 1
            finally:
                if i % 25 == 0:
                    self._log(f'Processed {i}/{len(property_ids)} properties, {self._rows_added} findings staged')
                time.sleep(self.REQUEST_DELAY_SECONDS)

        self._finalize()
        self._log(f'Extraction complete: processed={len(property_ids)} '
                  f'findings_added={self._rows_added} rejected={self._rows_rejected}')

    def _add_unlabeled_finding(self, text: str, external_id: Optional[str] = None) -> bool:
        """Stage a finding as unlabeled (category_v2=NULL).

        Same pattern as SocrataCrawler. Re-labeler picks it up on next run.
        """
        from models import db, MLFindingLabel

        self._rows_processed += 1
        text = (text or '').strip()
        if len(text) < 20:
            self._rows_rejected += 1
            return False
        text = text[:500]

        existing = MLFindingLabel.query.filter_by(
            finding_text=text,
            source_version=self.SOURCE_NAME,
        ).first()
        if existing:
            self._rows_rejected += 1
            return False

        row = MLFindingLabel(
            finding_text=text,
            category='general',
            severity='moderate',
            source=self.SOURCE_NAME,
            source_version=self.SOURCE_NAME,
            confidence=0.5,
            geographic_region=self.STATE_CODE,
            labeling_notes=f'disclosure extract id={external_id}' if external_id else None,
        )
        db.session.add(row)
        self._rows_added += 1

        if self._rows_added % 50 == 0:
            db.session.commit()
            self._log(f'Progress: {self._rows_added} staged')
        return True
