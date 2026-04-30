"""Socrata API crawler base class (Path 2 of geographic coverage roadmap).

Background (v5.86.94):
  Many US cities publish building violation / code enforcement data on
  Socrata-powered open data portals (data.cityofchicago.org, data.lacity.org,
  data.phila.gov, etc). These are NOT home inspection findings per se — they're
  municipal code enforcement records — but they contain finding-like text at
  geographic diversity we don't otherwise have.

  Training data today is 96% NYC. This class powers crawlers for Chicago, LA,
  Philadelphia, and similar cities, all of which use the same underlying
  Socrata API (SODA 2.0/2.1).

Architecture:
  - Inherits BaseIngestionJob (NOT BaseCrawler — Socrata has its own pagination
    and API conventions that don't map well to URL-list crawling)
  - Subclasses specify: DATASET_ID, DOMAIN, TEXT_FIELD, RELEVANT_WHERE_CLAUSE,
    STATE_CODE, MAX_ROWS
  - Fetches paginated JSON, writes RAW rows to ml_finding_labels with
    category_v2=NULL so the re-labeler picks them up on next run
  - Dedup by violation ID → violation text

  The pattern: crawl → relabel → train. Crawlers stage data; they don't try
  to label it. Labeling is the re-labeler's job.

API reference:
  https://dev.socrata.com/docs/queries/

Pagination:
  SODA uses $limit + $offset. We cap at 1000 rows/page (Socrata's max) and
  walk until either:
    - API returns empty page (reached end of dataset)
    - We hit MAX_ROWS ceiling
    - Fetch fails repeatedly

Rate limiting:
  Socrata recommends registering an app token for higher limits. Without a
  token, shared-IP rate limiting kicks in quickly (symptom: repeated 503
  responses). Register a free app token at:
    https://<domain>/profile/edit/developer_settings
  (e.g. https://data.cityofchicago.org/profile/edit/developer_settings)
  Then set SOCRATA_APP_TOKEN env var on Render. Tokens are lightweight —
  same token works across all Socrata domains.

  Without token: ~1000 requests/day shared-bucket, 503s common.
  With token:    ~10,000/hr per-token, reliable.

Data quality note:
  These are violation codes, not findings. "Failure to maintain railings on
  second-story porch" is a real finding; "Outstanding tax lien" is not. We
  include a RELEVANT_WHERE_CLAUSE hook in subclasses to filter at API level
  to construction/maintenance violations, not regulatory/tax ones.

Status:
  v5.86.94: SCAFFOLD ONLY. Chicago subclass stub present but inactive.
  No UI wiring, no background-job integration yet. Concrete run() paths
  will be validated in a future session after we've reviewed one city's
  actual API response.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import requests

from ml_ingestion.base import BaseIngestionJob


class SocrataCrawler(BaseIngestionJob):
    """Base class for Socrata-backed municipal open-data crawlers.

    Required class attrs:
      SOURCE_NAME: str    — e.g. 'chicago_violations_v1'. Used as source_version.
      DOMAIN: str         — e.g. 'data.cityofchicago.org' (no scheme)
      DATASET_ID: str     — 4x4 identifier, e.g. 'uupf-x98q'
      TEXT_FIELD: str     — the field containing the violation description,
                            e.g. 'violation_description'
      STATE_CODE: str     — 2-letter US state, e.g. 'IL'. Stored on each row
                            as geographic_region.

    Optional class attrs:
      RELEVANT_WHERE_CLAUSE: Optional[str] — SODA $where filter, e.g.
        "violation_status = 'OPEN' AND inspection_date > '2020-01-01'"
      MAX_ROWS: int = 10000          — hard ceiling per run
      PAGE_SIZE: int = 1000          — Socrata's max
      REQUEST_DELAY_SECONDS: float = 1.0
      STATUS: str = 'scaffold'       — 'scaffold' | 'active'. Scaffold
                                       subclasses refuse to run_job.
    """

    JOB_TYPE = 'crawl'
    USER_AGENT = 'OfferWiseBot/1.0 (+https://getofferwise.ai/about; contact: francis@getofferwise.ai)'

    # Subclasses must override these
    DOMAIN: str = ''
    DATASET_ID: str = ''
    TEXT_FIELD: str = ''
    STATE_CODE: str = ''

    # Subclasses may override
    RELEVANT_WHERE_CLAUSE: Optional[str] = None
    MAX_ROWS: int = 10000
    PAGE_SIZE: int = 1000
    REQUEST_DELAY_SECONDS: float = 1.0
    MAX_RETRIES: int = 3
    TIMEOUT_SECONDS: int = 30

    # Safety flag: subclasses must explicitly mark themselves ACTIVE before
    # being allowed to run. Prevents accidental invocation of scaffolding.
    STATUS: str = 'scaffold'

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self._session = requests.Session()
        self._session.headers.update({'User-Agent': self.USER_AGENT})
        token = os.environ.get('SOCRATA_APP_TOKEN')
        if token:
            self._session.headers.update({'X-App-Token': token})

    # ── Subclass hooks ────────────────────────────────────────────────
    def normalize_row(self, row: dict) -> Optional[dict]:
        """Transform a raw Socrata row into our storage shape.

        Default implementation extracts TEXT_FIELD and wraps in the expected
        output dict. Subclasses can override to do more (e.g. combine multiple
        fields, strip boilerplate, map violation codes).

        Returns a dict or None (to skip this row).
        Output dict keys:
          text: str — the finding text
          external_id: str (optional) — source-specific ID for dedup/traceability
        """
        text = row.get(self.TEXT_FIELD)
        if not text or not isinstance(text, str):
            return None
        text = text.strip()
        if len(text) < 15:
            return None
        return {'text': text}

    # ── Base orchestration ────────────────────────────────────────────
    def run_job(self) -> None:
        if self.STATUS != 'active':
            self._log(f'{type(self).__name__} is status={self.STATUS!r} — refusing to run. '
                      f'Set STATUS = "active" on the subclass once the integration is validated.', 'error')
            return

        if not (self.DOMAIN and self.DATASET_ID and self.TEXT_FIELD and self.STATE_CODE):
            self._log(f'{type(self).__name__} missing required config '
                      f'(DOMAIN/DATASET_ID/TEXT_FIELD/STATE_CODE)', 'error')
            return

        base_url = f'https://{self.DOMAIN}/resource/{self.DATASET_ID}.json'
        # v5.87.14: surface progress signal to the admin UI.
        self._expected_total = self.MAX_ROWS
        self._set_phase(f'Crawling {self.DOMAIN}/{self.DATASET_ID}')

        offset = 0
        total_added = 0
        total_pages = 0
        while total_added < self.MAX_ROWS:
            params = {
                '$limit': min(self.PAGE_SIZE, self.MAX_ROWS - total_added),
                '$offset': offset,
            }
            if self.RELEVANT_WHERE_CLAUSE:
                params['$where'] = self.RELEVANT_WHERE_CLAUSE

            page = self._fetch_page(base_url, params)
            if page is None:
                self._log('Fetch failure — stopping', 'error')
                break
            if not page:
                self._log(f'Empty page at offset {offset} — end of dataset reached')
                break

            total_pages += 1
            for raw_row in page:
                normalized = self.normalize_row(raw_row)
                if not normalized:
                    self._rows_rejected += 1
                    continue
                if self._add_unlabeled_finding(normalized['text'], normalized.get('external_id')):
                    total_added += 1

            offset += len(page)
            self._log(f'Page {total_pages}: offset={offset} added={total_added}')
            time.sleep(self.REQUEST_DELAY_SECONDS)

        self._set_phase(f'Done — {total_added} rows added')
        self._finalize()
        self._log(f'Socrata crawl complete: pages={total_pages} added={total_added} rejected={self._rows_rejected}')

    def _fetch_page(self, url: str, params: dict) -> Optional[list]:
        """Fetch one Socrata page with retries. Returns list of row dicts."""
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = self._session.get(url, params=params, timeout=self.TIMEOUT_SECONDS)
                if resp.status_code == 200:
                    data = resp.json()
                    if not isinstance(data, list):
                        self._log(f'Unexpected response shape: {type(data)}', 'warn')
                        return None
                    return data
                if resp.status_code in (429, 503):
                    delay = (2 ** attempt) * self.REQUEST_DELAY_SECONDS
                    self._log(f'Got {resp.status_code}, waiting {delay}s', 'warn')
                    time.sleep(delay)
                    continue
                if 400 <= resp.status_code < 500:
                    self._log(f'Hard error {resp.status_code}: {resp.text[:200]}', 'error')
                    return None
                time.sleep((2 ** attempt) * self.REQUEST_DELAY_SECONDS)
            except requests.RequestException as e:
                self._log(f'Fetch error: {e}', 'warn')
                time.sleep((2 ** attempt) * self.REQUEST_DELAY_SECONDS)
        return None

    def _add_unlabeled_finding(self, text: str, external_id: Optional[str] = None) -> bool:
        """Insert a raw violation as an UNLABELED row.

        category_v2=NULL signals to the re-labeler that this row needs
        labeling. We store category='general', severity='moderate' as v1
        placeholders (required non-null by the model — historic constraint)
        but the training pipeline will PREFER v2 when present, so these
        placeholders only matter if re-labeling never happens.

        Dedup: text-based, per SOURCE_NAME. Same mechanic as _add_finding.
        """
        from models import db, MLFindingLabel

        self._rows_processed += 1
        text = (text or '').strip()
        if len(text) < 15:
            self._rows_rejected += 1
            return False
        text = text[:500]  # cap

        # Dedup
        existing = MLFindingLabel.query.filter_by(
            finding_text=text,
            source_version=self.SOURCE_NAME,
        ).first()
        if existing:
            self._rows_rejected += 1
            return False

        row = MLFindingLabel(
            finding_text=text,
            category='general',        # placeholder — re-labeler will set category_v2
            severity='moderate',       # placeholder — re-labeler will set severity_v2
            source=self.SOURCE_NAME,
            source_version=self.SOURCE_NAME,
            confidence=0.5,            # low confidence — these need re-labeling
            geographic_region=self.STATE_CODE,
        )
        db.session.add(row)
        self._rows_added += 1

        if self._rows_added % 100 == 0:
            db.session.commit()
            self._log(f'Progress: {self._rows_added} rows staged')

        return True


class ChicagoBuildingViolationsCrawler(SocrataCrawler):
    """Chicago Department of Buildings violations crawler.

    Dataset: Building Violations (data.cityofchicago.org/dataset/22u3-xenr)
    Coverage: 2006-present, updated daily. Includes real inspector comments,
    not just violation code text.

    Field details verified against live API (v5.86.97):
      violation_description:         short category label ("REPAIR EXTERIOR WALL")
      violation_inspector_comments:  detailed finding text (the gold — actual
                                     free-text inspector observations)
      violation_status:              OPEN, COMPLIED, NO ENTRY, DISMISSED, etc.
      violation_date, violation_code, location_zip, latitude, longitude

    Training text strategy (`normalize_row` override below):
      Combine description + inspector_comments into one finding string. The
      description tells us the category context; the inspector comments give
      us the specific observation with measurements and locations. Training
      data quality benefits from both.

    Filter rationale:
      Default filter pulls recent 'COMPLIED' + 'OPEN' rows. We exclude
      'DISMISSED' and 'NO ENTRY' because those don't represent confirmed
      issues. Date floor prevents pulling ancient records — practice changes
      over time and we want current norms.
    """

    SOURCE_NAME = 'chicago_violations_v1'
    DOMAIN = 'data.cityofchicago.org'
    DATASET_ID = '22u3-xenr'  # Building Violations (verified 2026-04-23)
    TEXT_FIELD = 'violation_inspector_comments'  # richer than violation_description
    STATE_CODE = 'IL'

    # Pull confirmed-issue rows from last 4 years. OPEN = active violation,
    # COMPLIED = was a real issue, now fixed. Exclude DISMISSED/NO ENTRY.
    RELEVANT_WHERE_CLAUSE = (
        "(violation_status = 'COMPLIED' OR violation_status = 'OPEN') "
        "AND violation_date > '2022-01-01' "
        "AND violation_inspector_comments IS NOT NULL"
    )
    MAX_ROWS = 10000

    STATUS = 'active'  # v5.86.97: verified dataset + fields, ready to run

    def normalize_row(self, row: dict) -> Optional[dict]:
        """Combine description + inspector comments into one finding string.

        The description alone is a short code category label ("REPAIR EXTERIOR
        WALL") that's not useful on its own. The inspector comments contain
        the actual observation. We prefix with description where it adds
        context, otherwise use comments directly.
        """
        desc = (row.get('violation_description') or '').strip()
        comments = (row.get('violation_inspector_comments') or '').strip()

        # Skip rows missing inspector comments (filter should exclude these
        # but double-check — dataset is large and edge cases exist).
        if not comments or len(comments) < 15:
            return None

        # Build the training text
        if desc and desc.lower() not in comments.lower():
            text = f'{desc}: {comments}'
        else:
            text = comments

        # Skip extreme boilerplate (some violations have comments like
        # "SEE ATTACHED" or "SAME AS ABOVE")
        lower = text.lower()
        if any(bp in lower for bp in ['see attached', 'same as above', 'see previous', 'n/a']):
            if len(text) < 40:
                return None

        # External ID for dedup traceability
        external_id = str(row.get('id', '')).strip() or None
        return {'text': text, 'external_id': external_id}


# ═══════════════════════════════════════════════════════════════════════════
# v5.87.2: Additional US city crawlers.
# ═══════════════════════════════════════════════════════════════════════════
# STATUS pattern:
#   'active'    — dataset + text field verified against live API, safe to run
#   'scaffold'  — best-known dataset IDs but NOT verified from this environment
#                 (network blocked during scaffolding). Each scaffold subclass
#                 lists an activation checklist in its docstring that the
#                 operator must walk through before flipping STATUS='active'.
#
# Activation workflow (per city):
#   1. Open Render shell or curl the dataset URL to verify it returns JSON rows
#   2. Inspect actual field names — update TEXT_FIELD if needed
#   3. Confirm at least 1000 rows match RELEVANT_WHERE_CLAUSE
#   4. Flip STATUS='active' and redeploy
#   5. Run 100-row smoke test via admin UI with MAX_ROWS temporarily set to 100
#   6. Eyeball results in the DB — confirm text looks like finding content
#   7. Reset MAX_ROWS to 10000 and run the full crawl
#   8. Run Re-label All with filter source=<SOURCE_NAME> to classify new rows
# ═══════════════════════════════════════════════════════════════════════════


class PhiladelphiaLIViolationsCrawler(SocrataCrawler):
    """Philadelphia Licenses & Inspections violations.

    DIAGNOSED v5.87.15 — Phila migrated off Socrata to Carto SQL endpoint.
    The Carto endpoint at phl.carto.com/api/v2/sql?q=SELECT+*+FROM+li_violations
    is reachable and has 30 fields, BUT data appears frozen — count of rows
    where caseaddeddate > 2022-01-01 returns 0. Text quality (where present)
    is short codes like "HIGH WEEDS-CUT" similar to LA/Austin.

    Path forward (none ideal):
      a) Build a CartoCrawler base class to query phl.carto.com via SQL,
         accept the historical-data-only limitation
      b) Find Philadelphia's current ArcGIS Hub if migrated again (possible)
      c) Wait for Philly to publish a current Socrata dataset (no ETA)

    For now: scaffolded, not activatable. PA representation gap continues.


    Dataset: data.phila.gov L&I Violations (best-known ID: 9zep-xyrb)
    State: PA

    UNVERIFIED from scaffolding environment. Dataset ID and field names
    sourced from Philadelphia Open Data documentation circa 2024. The
    data.phila.gov Socrata endpoint appears to use non-standard auth on
    some requests — may need to be hit from Render itself rather than
    containerized scaffolding.

    Expected fields per Philly L&I docs:
      violationdescription   — free-text violation description (~TEXT_FIELD)
      violationcodetitle     — short category label
      violationstatus        — OPEN / IN COMPLIANCE / CLOSED
      opa_account_num, address, zip_code

    Activation checklist:
      [ ] curl https://data.phila.gov/resource/9zep-xyrb.json?$limit=1
      [ ] Confirm `violationdescription` field exists with free-text content
      [ ] If field name differs (some years use `vio_description`), update TEXT_FIELD
      [ ] Confirm filter returns ~10K+ rows (PA data.phila.gov may need auth header)
      [ ] Flip STATUS='active'
    """
    SOURCE_NAME = 'philadelphia_li_v1'
    DOMAIN = 'data.phila.gov'
    DATASET_ID = '9zep-xyrb'
    TEXT_FIELD = 'violationdescription'
    STATE_CODE = 'PA'
    RELEVANT_WHERE_CLAUSE = (
        "violationstatus IN ('IN COMPLIANCE', 'OPEN', 'CLOSED') "
        "AND violationdate > '2022-01-01' "
        "AND violationdescription IS NOT NULL"
    )
    MAX_ROWS = 10000
    STATUS = 'scaffold'


class SanFranciscoDBIComplaintsCrawler(SocrataCrawler):
    """San Francisco Department of Building Inspection complaints.

    DIAGNOSED v5.87.15 — Catalog search confirms eabe-havv "DOB Complaints
    Received" exists as a current dataset, BUT the resource API at
    /resource/eabe-havv.json returns HTTP 404. Same pattern for ipef-acke
    "Building Inspections" and 6v9u-ndjg "Complaint Disposition Codes".
    All datasets show in catalog but are unreachable via the standard
    Socrata resource API from this network.

    Likely explanation: SF migrated to a different API surface (sfdata.sfgov.org
    or services.arcgis.com) but kept the catalog listing for backwards-compat.

    Path forward: try from production network — the 404 may be container/IP
    specific. If still 404 from prod, search SF DataSF portal manually for
    the actual current API URL. The fact that ALL three building-related
    datasets 404 suggests a systematic migration, not individual dataset moves.


    Dataset: data.sfgov.org DBI Complaints (CURRENT ID: needs research)
    State: CA

    BAD COORDINATES — v5.87.9 verification revealed the previously-scaffolded
    ID `nuek-vuh3` is actually SF Fire Department call records (battalion,
    dispatch_dttm, hospital_dttm), NOT building inspections. Wrong dataset
    entirely.

    Correct candidates from data.sfgov.org catalog search for 'building'
    (need verification before activating):
      - eabe-havv  "DOB Complaints Received"  ← most likely correct
      - ipef-acke  "Building Inspections"
      - 6v9u-ndjg  "Building Complaint Disposition Codes" (lookup table)

    Both eabe-havv and ipef-acke returned HTTP 404 from the scaffolding
    container despite being in the catalog — likely they've been migrated
    to a v2 endpoint or different host (sfdata.sfgov.org). Verify from
    Render shell with the new /api/admin/ml-crawl-verify endpoint.

    Activation checklist:
      [ ] Try DATASET_ID = 'eabe-havv' first; verify with admin UI button
      [ ] If 404, try ipef-acke
      [ ] If both fail, search data.sfgov.org/browse?q=DBI manually
      [ ] Confirm the schema has a free-text complaint/violation field
      [ ] Update TEXT_FIELD to match the actual field name
      [ ] Flip STATUS='active'
    """
    SOURCE_NAME = 'sf_dbi_complaints_v1'
    DOMAIN = 'data.sfgov.org'
    DATASET_ID = 'eabe-havv'  # v5.87.9: was nuek-vuh3 (wrong — fire dept). Try this next.
    TEXT_FIELD = 'description'  # placeholder — confirm against real schema
    STATE_CODE = 'CA'
    RELEVANT_WHERE_CLAUSE = (
        "received_date > '2022-01-01'"
    )
    MAX_ROWS = 10000
    STATUS = 'scaffold'


class LosAngelesCodeEnforcementCrawler(SocrataCrawler):
    """Los Angeles Department of Building & Safety code enforcement cases.

    Dataset: data.lacity.org cr8f-uc4j (LAHD Property Violations).
    State: CA

    VERIFIED + ACTIVATED in v5.87.13 with operator approval. The schema is
    minimal — only 5 fields (address, apn, violations_cited, violations_cleared,
    violationtype) — and the text is short codes like "RECEPTACLE N/G" rather
    than free-text inspector narratives.

    Why activate anyway? Two reasons:
    1. California geographic representation. NYC + Chicago + Miami-Dade
       leaves the entire west coast unrepresented. Even thin LA data adds
       a real geographic signal.
    2. The codes are interpretable in aggregate. "RECEPTACLE N/G" might be
       opaque alone but the labeler sees enough of them across the corpus
       to learn the mapping (RECEPTACLE = electrical, N/G = not grounded =
       moderate severity).

    To squeeze more training signal out of each row, normalize_row combines
    all three short fields into a single phrase. Example:
      Raw fields:
        violationtype:      "RECEPTACLE N/G"
        violations_cited:   "4"
        violations_cleared: "4"
      Combined:
        "RECEPTACLE N/G (4 cited, 4 cleared)"
    The cited/cleared counts give the labeler weak severity signal — many
    citations means a recurring or serious issue.

    Future improvement: research LAHD richer-narrative datasets or LADBS
    inspection comments if they're exposed publicly. The current cr8f-uc4j
    is the activatable floor, not the ceiling.
    """
    SOURCE_NAME = 'la_code_enforcement_v1'
    DOMAIN = 'data.lacity.org'
    DATASET_ID = 'cr8f-uc4j'
    TEXT_FIELD = 'violationtype'
    STATE_CODE = 'CA'
    RELEVANT_WHERE_CLAUSE = (
        "violationtype IS NOT NULL"
    )
    MAX_ROWS = 10000
    STATUS = 'active'  # v5.87.13: activated with operator approval, accepting low-signal codes for CA coverage

    def normalize_row(self, row):
        """Combine violationtype + cited/cleared counts so the labeler gets
        more context than just the bare short code.

        Returns None for rows where violationtype is empty (those are
        unusable regardless of how we frame them).
        """
        vtype = (row.get('violationtype') or '').strip()
        if not vtype or len(vtype) < 3:
            return None
        cited = (row.get('violations_cited') or '').strip()
        cleared = (row.get('violations_cleared') or '').strip()
        # Build a phrase that gives the labeler more to work with than the
        # bare 2-word code. "RECEPTACLE N/G (4 cited, 4 cleared)"
        suffix = ''
        if cited or cleared:
            parts = []
            if cited: parts.append(f'{cited} cited')
            if cleared: parts.append(f'{cleared} cleared')
            suffix = f' ({", ".join(parts)})'
        return {
            'text': f'{vtype}{suffix}',
            'external_id': row.get('apn') or None,
        }


class SeattleCodeComplianceCrawler(SocrataCrawler):
    """Seattle Department of Construction & Inspections code complaints.

    Dataset: data.seattle.gov ez4a-iug7 'Code Complaints and Violations'.
    State: WA.

    VERIFIED + ACTIVATED in v5.87.15. Methodical city-by-city diagnosis
    found the correct dataset coordinates (the scaffold's original
    j3tc-7rh7 was wrong — returned 404). The current dataset has 17 fields
    including a real `description` field with rich inspector narrative
    text, e.g.: "Elevator gets stuck at least 3 times a week, feels it is
    a danger. Wants to keep a low profile, but will leave name and..."

    This is the highest-quality find from the v5.87.15 diagnosis pass —
    Seattle's data is more like Chicago's narrative quality than LA's
    short codes. WA + Pacific Northwest geographic representation
    (moisture, seismic, distinct construction era) genuinely
    underrepresented in the corpus.

    Filter excludes pre-2022 records to keep the corpus modern.
    """
    SOURCE_NAME = 'seattle_sdci_v1'
    DOMAIN = 'data.seattle.gov'
    DATASET_ID = 'ez4a-iug7'  # v5.87.15: verified live, real narrative text
    TEXT_FIELD = 'description'
    STATE_CODE = 'WA'
    RELEVANT_WHERE_CLAUSE = (
        "description IS NOT NULL "
        "AND opendate > '2022-01-01'"
    )
    MAX_ROWS = 10000
    STATUS = 'active'  # v5.87.15: activated, narrative quality comparable to Chicago

    def normalize_row(self, row):
        """Combine description with recordtypedesc for richer signal.

        Seattle's `description` field contains real inspector narrative text
        already; recordtypedesc gives helpful category context (e.g.
        "Construction" vs "Property Maintenance"). Keep description primary,
        prepend the type as a tag for the labeler.
        """
        desc = (row.get('description') or '').strip()
        if not desc or len(desc) < 15:
            return None
        rtype = (row.get('recordtypedesc') or '').strip()
        record_num = (row.get('recordnum') or '').strip()
        if rtype:
            text = f'[{rtype}] {desc}'
        else:
            text = desc
        return {
            'text': text[:500],
            'external_id': record_num or None,
        }


class AustinCodeCasesCrawler(SocrataCrawler):
    """Austin Code Department complaint cases.

    Dataset: data.austintexas.gov 6wtj-zbtb 'Austin Code Complaint Cases'.
    State: TX.

    VERIFIED + ACTIVATED in v5.87.15. The scaffold's original e6c2-9h7m
    was wrong (404). Catalog search surfaced the correct dataset; the
    descriptions are short/categorical (e.g. "Land Use Violation(s)",
    "Property Maintenance") rather than rich inspector narratives, so
    quality is comparable to LA's short codes — usable for TX geographic
    coverage but not high-signal training text.

    Texas representation matters: foundation issues from clay soil,
    extreme heat AC failures, hurricane retrofit (gulf coast). None of
    that pattern is well represented in NYC + Chicago + Seattle.

    To extract more signal per row, normalize_row combines `description`
    with `case_type` to give the labeler an additional context tag.
    """
    SOURCE_NAME = 'austin_code_cases_v1'
    DOMAIN = 'data.austintexas.gov'
    DATASET_ID = '6wtj-zbtb'  # v5.87.15: verified live via catalog search
    TEXT_FIELD = 'description'
    STATE_CODE = 'TX'
    RELEVANT_WHERE_CLAUSE = (
        "description IS NOT NULL"
    )
    MAX_ROWS = 10000
    STATUS = 'active'  # v5.87.15: activated with operator approval, accepting low-signal categories for TX coverage

    def normalize_row(self, row):
        """Combine description + case_type for richer context.

        Austin's text is short (categorical) so we add the case_type tag
        as a prefix. Example: "[Complaints] Land Use Violation(s)".
        """
        desc = (row.get('description') or '').strip()
        if not desc or len(desc) < 5:
            return None
        case_type = (row.get('case_type') or '').strip()
        if case_type and case_type.lower() != desc.lower():
            text = f'[{case_type}] {desc}'
        else:
            text = desc
        return {
            'text': text[:500],
            'external_id': (row.get('case_number') or '').strip() or None,
        }


class BaltimoreHousingViolationsCrawler(SocrataCrawler):
    """Baltimore Department of Housing & Community Development violations.

    DIAGNOSED v5.87.15 — Baltimore migrated off Socrata to ArcGIS Hub.
    Hitting data.baltimorecity.gov/resource/87ir-fdk4.json returns HTTP 302
    redirect to https://hub.arcgis.com/legacy. Same pattern as Detroit.

    Path forward: build a Baltimore-specific ArcGIS subclass once the new
    hub.arcgis.com URL for Baltimore housing violations is identified.
    Same fix pattern as MiamiDadeCodeViolationsCrawler. NOT activatable
    via Socrata at all — this whole class is the wrong shape for Baltimore.


    Dataset: data.baltimorecity.gov Housing Violations (best-known ID: 87ir-fdk4)
    State: MD

    UNVERIFIED — got HTTP 302 redirect from scaffolding environment.
    Baltimore's Socrata portal may have moved — check if dataset is now
    on data.baltimorecity.gov/... or a sub-portal.

    Activation checklist:
      [ ] Resolve the 302 redirect target
      [ ] Confirm dataset ID (may have new one)
      [ ] Baltimore data typically uses `violation_description` or similar
      [ ] Flip STATUS='active'
    """
    SOURCE_NAME = 'baltimore_housing_v1'
    DOMAIN = 'data.baltimorecity.gov'
    DATASET_ID = '87ir-fdk4'
    TEXT_FIELD = 'violation_description'
    STATE_CODE = 'MD'
    RELEVANT_WHERE_CLAUSE = (
        "violation_description IS NOT NULL"
    )
    MAX_ROWS = 10000
    STATUS = 'scaffold'


class DCHousingViolationsCrawler(SocrataCrawler):
    """DC Department of Buildings / DCRA housing code violations.

    DIAGNOSED v5.87.15 — DC's open data hub (opendata.dc.gov) has 1876
    datasets, NONE of which appear to be housing/building violations.
    Search results for "housing", "violations", "inspections" return only
    traffic moving-violations, sidewalk condition assessments, and similar
    non-residential data.

    DC Department of Buildings publishes inspection narratives via DCRA
    case files, but they're not exposed as bulk data — likely FOIA-only.

    Path forward: drop. DC representation is a real gap but no public API
    path exists that would yield trainable inspector narrative text.


    Dataset: opendata.dc.gov housing code violations (ID varies)
    State: DC

    UNVERIFIED — opendata.dc.gov uses ArcGIS/ESRI stack, NOT Socrata.
    This class is WRONG base class for DC. Needs ArcGISCrawler instead.
    Left here as a breadcrumb for future work.

    Activation checklist:
      [ ] DO NOT ACTIVATE THIS CLASS — wrong platform
      [ ] When ArcGISCrawler base class is built, create DCArcGISHousingCrawler
          and point STATUS='active' there instead
    """
    SOURCE_NAME = 'dc_housing_v1'
    DOMAIN = 'opendata.dc.gov'
    DATASET_ID = 'housing-code-enforcement'  # placeholder — not a Socrata 4x4
    TEXT_FIELD = 'description'
    STATE_CODE = 'DC'
    RELEVANT_WHERE_CLAUSE = None
    MAX_ROWS = 10000
    STATUS = 'scaffold'  # wrong platform — see docstring


class DallasCodeViolationsCrawler(SocrataCrawler):
    """Dallas Code Compliance Services violations.

    DIAGNOSED v5.87.15 — Dallas Socrata catalog search returned 6 candidates;
    the configured yrui-jyi2 returns 404 and the obvious "Housing Maintenance
    Code Violations" wvxf-dwi5 also returns 404 (catalog-listed but gone).

    Working candidates:
      - xrzj-c8ez "Code Violations" — 20 fields, code_viol/nuisance fields are
        short codes ("Other", "Illegal VendingCitation"), data from 2015 era.
      - hqes-3ct4 "Code Violation Activities" — 11 fields, but content is
        activity log entries ("Contacted Customer by Telephone") not violations.

    Neither yields good training text. Comparable quality to LA's short codes
    but with stale (2015-ish) data and no clear narrative field.

    Path forward: hold. Texas coverage available via Austin which has slightly
    better text quality. Dallas may need a different data source entirely
    (Tarrant County, Dallas County records, or commercial provider).


    Dataset: www.dallasopendata.com Code Violations (ID unverified)
    State: TX

    UNVERIFIED. Dallas publishes on www.dallasopendata.com. Dataset ID
    guess was yrui-jyi2 (404'd from scaffolding env). Search the portal
    for "Code Violations" or "311 Code Enforcement" to find current ID.

    Activation checklist:
      [ ] Search www.dallasopendata.com for violations datasets
      [ ] Get current 4x4 dataset ID
      [ ] Verify field names
      [ ] Flip STATUS='active'
    """
    SOURCE_NAME = 'dallas_code_v1'
    DOMAIN = 'www.dallasopendata.com'
    DATASET_ID = 'yrui-jyi2'  # VERIFY — likely wrong
    TEXT_FIELD = 'description'
    STATE_CODE = 'TX'
    RELEVANT_WHERE_CLAUSE = (
        "description IS NOT NULL"
    )
    MAX_ROWS = 10000
    STATUS = 'scaffold'


class DetroitBlightViolationsCrawler(SocrataCrawler):
    """Detroit BSEED (Building, Safety Engineering & Environmental Department)

    DIAGNOSED v5.87.15 — Detroit migrated off Socrata to ArcGIS Hub.
    Hitting data.detroitmi.gov/resource/tfdk-ticn.json returns HTTP 302
    redirect to https://hub.arcgis.com/legacy. Same pattern as Baltimore.

    Path forward: build a Detroit-specific ArcGIS subclass once the new
    hub.arcgis.com URL for Detroit blight violations is identified. Same
    fix pattern as MiamiDadeCodeViolationsCrawler. NOT activatable via
    Socrata at all — this whole class is the wrong shape for Detroit.

    blight violations.

    Dataset: data.detroitmi.gov Blight Violations (best-known ID: tfdk-ticn)
    State: MI

    UNVERIFIED — got HTTP 302 redirect. Detroit's open data portal has
    changed structure a few times; verify current platform.

    Activation checklist:
      [ ] Resolve redirect to current portal
      [ ] Confirm dataset exists and has free-text narrative
      [ ] Blight violations are often terse ("FAILURE TO MAINTAIN") — may
          need to combine violation_code + description for useful training text
      [ ] Flip STATUS='active'
    """
    SOURCE_NAME = 'detroit_bseed_v1'
    DOMAIN = 'data.detroitmi.gov'
    DATASET_ID = 'tfdk-ticn'
    TEXT_FIELD = 'violation_description'
    STATE_CODE = 'MI'
    RELEVANT_WHERE_CLAUSE = (
        "violation_description IS NOT NULL "
        "AND ticket_issued_date > '2022-01-01'"
    )
    MAX_ROWS = 10000
    STATUS = 'scaffold'


# ═══════════════════════════════════════════════════════════════════════════
# v5.87.16 additions — discovered during systematic city scout pass.
# Both verified live with real data; activated immediately.
# ═══════════════════════════════════════════════════════════════════════════


class NewOrleansCodeViolationsCrawler(SocrataCrawler):
    """New Orleans Department of Code Enforcement violations.

    Dataset: data.nola.gov 3ehi-je3s.
    State: LA.

    VERIFIED + ACTIVATED v5.87.16 during systematic city scout. The data
    has 8 fields and the `description` column contains real building code
    citation language, e.g. "Structural members including floor joists
    must be maintained structurally sound." This is the highest-quality
    text find of the v5.87.16 scout — comparable to Chicago narrative
    quality, full sentences with code language.

    61,426 rows since 2022-01-01 (out of 263,807 total). Geographic value:
    Gulf coast hurricane zone, humid subtropical, balloon-frame and Creole
    cottage construction style — distinct from any current corpus city.

    normalize_row combines `violation` (short code+title) with `description`
    (full narrative) for richest possible labeler signal.
    """
    SOURCE_NAME = 'new_orleans_code_v1'
    DOMAIN = 'data.nola.gov'
    DATASET_ID = '3ehi-je3s'
    TEXT_FIELD = 'description'
    STATE_CODE = 'LA'
    RELEVANT_WHERE_CLAUSE = (
        "description IS NOT NULL "
        "AND violationdate > '2022-01-01'"
    )
    MAX_ROWS = 10000
    STATUS = 'active'

    def normalize_row(self, row):
        """Combine the short `violation` tag with the full `description`.

        violation field is like "28-11 Floor Joists" (code + brief title).
        description is the full narrative ("Structural members including
        floor joists must be maintained structurally sound.").
        Combined gives the labeler both the code reference and the
        natural-language explanation.
        """
        desc = (row.get('description') or '').strip()
        if not desc or len(desc) < 15:
            return None
        violation = (row.get('violation') or '').strip()
        case_no = (row.get('caseno') or '').strip()
        if violation and violation.lower() not in desc.lower():
            text = f'[{violation}] {desc}'
        else:
            text = desc
        return {
            'text': text[:500],
            'external_id': case_no or None,
        }


class CincinnatiCodeEnforcementCrawler(SocrataCrawler):
    """Cincinnati Code Enforcement complaints.

    Dataset: data.cincinnati-oh.gov cncm-znd6.
    State: OH.

    VERIFIED + ACTIVATED v5.87.16. 23-field schema with `comp_type_desc`
    and `sub_type_desc` providing two-level categorical text (e.g.
    "Trash/Litter/Tall Grass Complaint" / "Tall grass/weeds, private prop").
    Quality similar to Austin and LA: short categorical, not narrative.
    Usable for Midwest geographic representation gap (Ohio Valley humid
    continental climate, freeze-thaw cycle, distinct construction styles).

    Filter excludes pre-2022 rows via `entered_date` (NOT date_filed —
    Cincinnati uses a different field name than other Socrata cities).
    """
    SOURCE_NAME = 'cincinnati_code_v1'
    DOMAIN = 'data.cincinnati-oh.gov'
    DATASET_ID = 'cncm-znd6'
    TEXT_FIELD = 'comp_type_desc'
    STATE_CODE = 'OH'
    RELEVANT_WHERE_CLAUSE = (
        "comp_type_desc IS NOT NULL "
        "AND entered_date > '2022-01-01'"
    )
    MAX_ROWS = 10000
    STATUS = 'active'

    def normalize_row(self, row):
        """Combine comp_type_desc + sub_type_desc for two-level signal.

        Example output: "[Trash/Litter/Tall Grass Complaint] Tall grass/weeds,
        private prop". The category-then-detail structure helps the labeler
        derive both category and severity.
        """
        comp = (row.get('comp_type_desc') or '').strip()
        if not comp or len(comp) < 5:
            return None
        sub = (row.get('sub_type_desc') or '').strip()
        case_id = (row.get('number_key') or '').strip()
        if sub and sub.lower() != comp.lower():
            text = f'[{comp}] {sub}'
        else:
            text = comp
        return {
            'text': text[:500],
            'external_id': case_id or None,
        }


# ═══════════════════════════════════════════════════════════════════════════
# v5.87.7: Federal & national data crawler.
# ═══════════════════════════════════════════════════════════════════════════
# This is a thin registry wrapper around the older cost_data_crawler module,
# which predates the registry pattern. The underlying function still runs as
# a daily cron (in app.py) and from the admin agent endpoint; this class only
# exists so the federal sources show up alongside the city crawlers in the
# admin UI dropdown.
#
# Unlike SocrataCrawler subclasses, this one writes to BOTH MLFindingLabel
# (for finding rows) AND MLCostData (for cost rows). It cannot use the
# base class's _add_finding helper because of the dual-table writes.
# ═══════════════════════════════════════════════════════════════════════════

from ml_ingestion.base import BaseIngestionJob


class FederalDataCrawler(BaseIngestionJob):
    """Federal/national data: HomeAdvisor, FEMA IA, NFIP flood claims, HUD
    inspection scores, III insurance, plus permit/violation data from a
    handful of cities (NYC HPD/DOB/311, Philadelphia via Carto, etc).

    Output goes into two tables:
      - MLCostData   for rows with cost_mid > 0 (RSMeans-style references)
      - MLFindingLabel for rows tagged as findings (violation text)

    Wraps cost_data_crawler.collect_all_external_data() rather than
    reimplementing the fetchers — that function is also called by the daily
    ML agent cron and we want one source of truth.
    """
    JOB_TYPE = 'crawl'
    SOURCE_NAME = 'federal_data_v1'
    STATUS = 'active'  # has been running in cron since v5.x; safe to enable
    # v5.87.8: lowered from 10000 → 2000 so a full run completes in ~10-15 min
    # rather than 25-30 min. The legacy admin route still passes 10000 to its
    # own call site, which is fine — that one is intentionally bigger and
    # runs from cron, not from the user-facing UI.
    PERMIT_LIMIT = 2000

    def run_job(self):
        """Run the legacy collect_all_external_data() and persist results.

        Mirrors the body of admin_routes.admin_ml_crawl_costs — same fetchers,
        same persistence semantics — but reports progress through the
        BaseIngestionJob log/metrics pipeline so the UI shows it like any
        other ingestion job. Field names (finding_text, source, analysis_id)
        match what the legacy admin route writes; do not change without
        also updating the daily ML agent cron in app.py.
        """
        from cost_data_crawler import collect_all_external_data
        from models import db, MLCostData, MLFindingLabel
        import hashlib
        import json as _json

        # v5.87.14: surface progress to the admin UI. Fetch phase is bounded
        # by total source count (~7), persistence phase is bounded by row count.
        self._set_phase('Fetching from federal/national sources...')
        self._log('Starting federal/national data fetch (HomeAdvisor, FEMA, '
                  'NFIP, HUD, III, plus city permits/violations)...')

        # Progress callback — funnel each source completion into log + phase.
        def _progress(source_name, status, count):
            self._set_phase(f'{source_name}: {status} ({count} rows)')

        try:
            rows, stats = collect_all_external_data(
                permit_limit=self.PERMIT_LIMIT,
                progress_callback=_progress,
            )
        except Exception as e:
            self._log(f'Fetch failed: {e}', level='error')
            raise

        self._log(f'Fetch complete: {len(rows)} total rows. Persisting...')
        # v5.87.14: now we know how many rows we'll process; tell the UI.
        total_rows = len(rows)
        self._expected_total = total_rows
        self._set_phase(f'Persisting {total_rows} rows to database...')

        cost_added = 0
        finding_added = 0
        skipped = 0
        batch_size = 500
        batch_count = 0

        while rows:
            row = rows.pop(0)
            try:
                cost_mid = row.get('cost_mid', 0) or 0
                is_finding = row.get('_type') == 'finding'

                # Hash key matches what admin_ml_crawl_costs uses so we share
                # a dedup namespace with the legacy code path.
                h = hashlib.sha256(
                    (row.get('finding_text', '')[:200] + '|' +
                     row.get('source', '') + '|' +
                     str(int(cost_mid))).encode()
                ).hexdigest()

                if cost_mid > 0:
                    existing = MLCostData.query.filter_by(content_hash=h).first()
                    if existing:
                        skipped += 1
                    else:
                        db.session.add(MLCostData(
                            finding_text=row.get('finding_text', '')[:500],
                            category=row.get('category', 'general'),
                            severity=row.get('severity', 'moderate'),
                            cost_low=row.get('cost_low'),
                            cost_high=row.get('cost_high'),
                            cost_mid=cost_mid,
                            zip_code=(row.get('zip_code') or '')[:10],
                            source=(row.get('source') or 'unknown')[:50],
                            source_meta=_json.dumps(row.get('metadata', {})),
                            content_hash=h,
                        ))
                        cost_added += 1

                if is_finding and row.get('finding_text') and row.get('category') and row.get('severity'):
                    # MLFindingLabel has no content_hash — dedup by finding_text
                    # like the legacy route does. Not perfect (different sources
                    # with identical text collapse to one) but matches existing
                    # behaviour so we don't introduce drift.
                    exists = MLFindingLabel.query.filter_by(
                        finding_text=row['finding_text'][:500]
                    ).first()
                    if exists:
                        skipped += 1
                    else:
                        db.session.add(MLFindingLabel(
                            analysis_id=0,
                            finding_text=row['finding_text'][:500],
                            category=row['category'],
                            severity=row['severity'],
                            source=(row.get('source') or 'crawled')[:50],
                        ))
                        finding_added += 1

                self._rows_processed += 1
                batch_count += 1
                if batch_count >= batch_size:
                    try:
                        db.session.commit()
                    except Exception as ce:
                        db.session.rollback()
                        logger.warning(f'FederalDataCrawler batch commit failed: {ce}')
                    batch_count = 0
            except Exception as row_err:
                logger.warning(f'FederalDataCrawler row error: {row_err}')
                skipped += 1

        try:
            db.session.commit()
        except Exception as ce:
            db.session.rollback()
            logger.warning(f'FederalDataCrawler final commit failed: {ce}')

        self._rows_added = cost_added + finding_added
        self._rows_rejected = skipped
        self._log(f'Done: {cost_added} cost rows + {finding_added} finding rows added, '
                  f'{skipped} skipped (duplicates / row errors). '
                  f'Total scanned: {total_rows}')


# Registry used by the UI dropdown and admin route. Each entry is
# (source_key, display_name, class_ref). Order is "feature order" in the UI.
# Registry used by the UI dropdown and admin route. Each entry is
# (source_key, display_name, class_ref). Order is "feature order" in the UI.
# v5.87.9: ArcGIS crawlers join the registry alongside Socrata ones — both
# inherit BaseIngestionJob, both work with the unified Crawl Selected /
# Crawl All Active backend routes.
from ml_ingestion.arcgis_crawler import (
    MiamiDadeCodeViolationsCrawler,
    PhoenixCodeEnforcementCrawler,
    AtlantaCodeEnforcementCrawler,
    DetroitBlightTicketsCrawler,
    ColumbusCodeEnforcementCrawler,
    AdamsCountyCodeEnforcementCrawler,
    AugustaGACodeViolationsCrawler,
    IndianapolisCodeEnforcementCrawler,
)

CRAWLER_REGISTRY = [
    ('chicago',       'Chicago Building Violations (IL · narrative) · active', ChicagoBuildingViolationsCrawler),
    ('federal',       'Federal & National (FEMA, NFIP, HomeAdvisor, HUD) · active',   FederalDataCrawler),
    ('seattle',       'Seattle SDCI Code Complaints (WA · narrative) · active', SeattleCodeComplianceCrawler),
    ('new_orleans',   'New Orleans Code Enforcement (LA · narrative) · active', NewOrleansCodeViolationsCrawler),
    ('cincinnati',    'Cincinnati Code Enforcement (OH · short codes) · active', CincinnatiCodeEnforcementCrawler),
    ('austin',        'Austin Code Cases (TX · short codes) · active',         AustinCodeCasesCrawler),
    ('la',            'LA Code Enforcement (CA · short codes) · active',       LosAngelesCodeEnforcementCrawler),
    # ArcGIS-shaped crawlers — active
    ('miamidade',     'Miami-Dade Code Violations (FL · ArcGIS) · active',     MiamiDadeCodeViolationsCrawler),
    ('detroit',       'Detroit Blight Tickets (MI · ArcGIS · short codes) · active',   DetroitBlightTicketsCrawler),
    ('columbus',      'Columbus Code Enforcement (OH · ArcGIS · structured) · active', ColumbusCodeEnforcementCrawler),
    ('adams_county',  'Adams County Code Enforcement (CO · ArcGIS · narrative) · active', AdamsCountyCodeEnforcementCrawler),
    ('augusta_ga',    'Augusta-Richmond County (GA · ArcGIS · narrative · small) · active', AugustaGACodeViolationsCrawler),
    ('indianapolis',  'Indianapolis Code Enforcement (IN · ArcGIS · 4-level codes · 106K rows) · active', IndianapolisCodeEnforcementCrawler),
    # Scaffolds with specific blockers (see docstring per class)
    ('philadelphia',  'Philadelphia (PA · Carto, data frozen) · scaffold',     PhiladelphiaLIViolationsCrawler),
    ('sf',            'San Francisco (CA · API 404 from prod) · scaffold',     SanFranciscoDBIComplaintsCrawler),
    ('baltimore',     'Baltimore (MD · only permits avail, not violations) · scaffold', BaltimoreHousingViolationsCrawler),
    ('dc',            'DC (no public housing dataset) · scaffold',             DCHousingViolationsCrawler),
    ('dallas',        'Dallas (TX · only stale short codes) · scaffold',       DallasCodeViolationsCrawler),
    ('phoenix',       'Phoenix Code Enforcement (AZ · ArcGIS endpoint not found) · scaffold', PhoenixCodeEnforcementCrawler),
    ('atlanta',       'Atlanta Code Enforcement (GA · ArcGIS · catalog noisy) · scaffold',    AtlantaCodeEnforcementCrawler),
]


def get_crawler_class(source_key: str):
    """Look up crawler class by UI source key."""
    for key, _label, cls in CRAWLER_REGISTRY:
        if key == source_key:
            return cls
    return None


def list_active_crawlers() -> list[tuple[str, str, type]]:
    """Return (key, label, class) tuples only for crawlers with STATUS='active'.
    Used by the 'Crawl All Active' bulk operation so scaffold-status classes
    are correctly skipped without per-city handling in the admin route.
    """
    return [(k, lbl, cls) for k, lbl, cls in CRAWLER_REGISTRY
            if getattr(cls, 'STATUS', 'scaffold') == 'active']
