"""ArcGIS REST crawler base class + scaffolded city subclasses (v5.87.9).

ArcGIS REST endpoints are the second-most-common municipal data platform in
the US (after Socrata). Many large jurisdictions that DON'T use Socrata
DO use Esri's ArcGIS Server / FeatureServer / MapServer:
  - Miami-Dade County (FL)
  - Maricopa County / Phoenix (AZ)
  - DC (opendata.dc.gov runs ArcGIS Hub)
  - Atlanta (GA)
  - LA County (vs LA City which is Socrata)

API shape differences from Socrata:
  - URL: https://services.arcgis.com/<orgId>/arcgis/rest/services/<layer>/FeatureServer/<id>/query
  - Pagination: resultOffset + resultRecordCount (not $offset + $limit)
  - Query: where=<SQL clause> + outFields=* + f=json (not $where, $select, $limit)
  - Response: { features: [ { attributes: {...} } ], exceededTransferLimit: bool }
  - Field name: usually camelCase or snake_case depending on origin
  - Dataset ID: numeric layer index, not 4x4 alphanumeric

Activation status follows the same convention as SocrataCrawler:
  - 'scaffold' (default) — refuses to run_job, exists for documentation
  - 'active' — has been verified against the live endpoint, safe to run

The /api/admin/ml-crawl-verify endpoint (v5.87.9) supports Socrata-shaped
crawlers but NOT ArcGIS — verification needs separate handling. For now,
verify ArcGIS subclasses manually:

    curl 'https://services.arcgis.com/<orgId>/.../FeatureServer/0/query?where=1=1&outFields=*&resultRecordCount=1&f=json'

Confirm:
  - Response is JSON with a 'features' array
  - features[0].attributes contains your TEXT_FIELD with real text
  - resultRecordCount + resultOffset paginate cleanly
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

from ml_ingestion.base import BaseIngestionJob

logger = logging.getLogger(__name__)


class ArcGISCrawler(BaseIngestionJob):
    """Base class for ArcGIS REST FeatureServer / MapServer crawlers.

    Required class attrs:
      SOURCE_NAME: str    — e.g. 'miamidade_violations_v1'
      SERVICE_URL: str    — full REST URL up to and including /FeatureServer/<n>
                            (or /MapServer/<n>). Example:
        'https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/CodeViolations/FeatureServer/0'
      TEXT_FIELD: str     — attribute name with violation/inspection narrative
      STATE_CODE: str     — 2-letter US state, e.g. 'FL'

    Optional class attrs:
      RELEVANT_WHERE_CLAUSE: Optional[str] — SQL WHERE clause, e.g.
        "VIOLATION_DATE > date '2022-01-01' AND DESCRIPTION IS NOT NULL"
      MAX_ROWS: int = 10000
      PAGE_SIZE: int = 1000      — ArcGIS hard cap is usually 1000-2000
      REQUEST_DELAY_SECONDS: float = 1.0
      STATUS: str = 'scaffold'   — must flip to 'active' before run_job works

    Subclasses can override normalize_row(row.attributes) to transform fields
    before staging. Default extracts TEXT_FIELD and trims.
    """

    JOB_TYPE = 'crawl'
    USER_AGENT = 'OfferWiseBot/1.0 (+https://getofferwise.ai/about; contact: francis@getofferwise.ai)'

    # Subclasses MUST override
    SERVICE_URL: str = ''
    TEXT_FIELD: str = ''
    STATE_CODE: str = ''

    # Subclasses MAY override
    RELEVANT_WHERE_CLAUSE: Optional[str] = None
    MAX_ROWS: int = 10000
    PAGE_SIZE: int = 1000
    REQUEST_DELAY_SECONDS: float = 1.0
    MAX_RETRIES: int = 3
    TIMEOUT_SECONDS: int = 30

    # Safety gate: scaffold subclasses refuse to run.
    STATUS: str = 'scaffold'

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        self._session = requests.Session()
        self._session.headers.update({'User-Agent': self.USER_AGENT})
        # ArcGIS public endpoints generally don't require auth, but some
        # orgs front them with an api token via a query param. If we ever
        # need that, surface it via an ARCGIS_TOKEN env var.
        self._api_token = os.environ.get('ARCGIS_TOKEN')

    # ── Subclass hooks ────────────────────────────────────────────────
    def normalize_row(self, attrs: dict) -> Optional[dict]:
        """Transform a raw ArcGIS attributes dict into our storage shape.

        Default extracts TEXT_FIELD. Subclasses can override to combine
        multiple fields, strip boilerplate, or map codes to descriptions.

        Returns dict with keys:
          text: str   — finding text (required)
          external_id: str (optional) — source-specific row ID for traceability
        """
        text = attrs.get(self.TEXT_FIELD)
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

        if not (self.SERVICE_URL and self.TEXT_FIELD and self.STATE_CODE):
            self._log(f'{type(self).__name__} missing required config '
                      f'(SERVICE_URL/TEXT_FIELD/STATE_CODE)', 'error')
            return

        query_url = f'{self.SERVICE_URL}/query'
        # v5.87.14: surface progress signal to the admin UI.
        self._expected_total = self.MAX_ROWS
        self._set_phase(f'Crawling ArcGIS {self.SOURCE_NAME}')

        offset = 0
        total_added = 0
        total_pages = 0
        while total_added < self.MAX_ROWS:
            page_size = min(self.PAGE_SIZE, self.MAX_ROWS - total_added)
            params = {
                'where': self.RELEVANT_WHERE_CLAUSE or '1=1',
                'outFields': '*',
                'returnGeometry': 'false',
                'resultOffset': offset,
                'resultRecordCount': page_size,
                'f': 'json',
            }
            if self._api_token:
                params['token'] = self._api_token

            response = self._fetch_page(query_url, params)
            if response is None:
                self._log('Fetch failure — stopping', 'error')
                break

            features = response.get('features') or []
            if not features:
                self._log(f'Empty page at offset {offset} — end of dataset reached')
                break

            total_pages += 1
            for feature in features:
                attrs = feature.get('attributes') or {}
                normalized = self.normalize_row(attrs)
                if not normalized:
                    self._rows_rejected += 1
                    continue
                if self._add_unlabeled_finding(normalized['text'], normalized.get('external_id')):
                    total_added += 1

            offset += len(features)
            self._log(f'Page {total_pages}: offset={offset} added={total_added}')

            # ArcGIS tells us if more pages exist via this flag (some endpoints).
            # If absent or False, assume we've drained.
            if not response.get('exceededTransferLimit'):
                # Older endpoints don't set this flag; fall back to checking
                # whether we got a full page back.
                if len(features) < page_size:
                    self._log('Last page (returned fewer rows than requested)')
                    break

            time.sleep(self.REQUEST_DELAY_SECONDS)

        self._set_phase(f'Done — {total_added} rows added')
        self._finalize()
        self._log(f'ArcGIS crawl complete: pages={total_pages} added={total_added} rejected={self._rows_rejected}')

    def _fetch_page(self, url: str, params: dict) -> Optional[dict]:
        """Fetch one ArcGIS page with retries. Returns the full response dict
        (with 'features', 'exceededTransferLimit' keys) or None on failure.
        """
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = self._session.get(url, params=params, timeout=self.TIMEOUT_SECONDS)
                if resp.status_code == 200:
                    data = resp.json()
                    # ArcGIS error responses are 200 with {error: {...}} body
                    if isinstance(data, dict) and data.get('error'):
                        err = data['error']
                        self._log(f'ArcGIS error: {err.get("message", err)}', 'error')
                        return None
                    return data if isinstance(data, dict) else None
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
        """Same shape as SocrataCrawler._add_unlabeled_finding — stages a
        row with placeholder labels for the re-labeler to overwrite."""
        from models import db, MLFindingLabel

        self._rows_processed += 1
        text = (text or '').strip()
        if len(text) < 15:
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
        )
        db.session.add(row)
        self._rows_added += 1

        if self._rows_added % 100 == 0:
            db.session.commit()
            self._log(f'Progress: {self._rows_added} rows staged')

        return True


# ═══════════════════════════════════════════════════════════════════════════
# Concrete scaffolded subclasses — verify SERVICE_URL + TEXT_FIELD before
# flipping STATUS='active'. None of these have been verified from this
# scaffolding container; activation must happen from Render shell or local
# terminal with proper outbound network access.
# ═══════════════════════════════════════════════════════════════════════════


class MiamiDadeCodeViolationsCrawler(ArcGISCrawler):
    """Miami-Dade County (FL) code enforcement violations.

    Source: Miami-Dade EnerGov LandMgt Viewer, layer 86 'Code Violations'.
    Verified live in v5.87.10:
      - 23-field schema with CASE_NUM, CASE_DATE, PROBLEM, PROBLEM_DESC, STAT_DESC, LAST_ACTV
      - Returned real cases dating from 2024-2025
      - Text is short categorical descriptions (~50 char), not rich narratives
      - Data quality is comparable to LA's short violation codes — usable
        but won't produce as much training signal per row as Chicago.

    Hurricane-zone state, distinct climate (humid subtropical), construction
    style (CBS/concrete block), and soil (limestone over sand) — geographic
    diversity that NYC/Chicago doesn't cover.

    To produce more useful training text, normalize_row combines PROBLEM_DESC
    with LAST_ACTV when both are present (e.g. "Junk/Trash on Property —
    Case closed"). This gives richer context to the labeler.
    """
    SOURCE_NAME = 'miamidade_violations_v1'
    SERVICE_URL = 'https://gisweb.miamidade.gov/arcgis/rest/services/EnerGov/MD_LandMgtViewer/MapServer/86'
    TEXT_FIELD = 'PROBLEM_DESC'
    STATE_CODE = 'FL'
    # Filter: drop empty/null PROBLEM_DESC. Date filter is loose (2022+) since
    # the field is epoch-millis Date — easier to filter post-fetch by year.
    RELEVANT_WHERE_CLAUSE = "PROBLEM_DESC IS NOT NULL AND PROBLEM_DESC <> ''"
    MAX_ROWS = 10000
    PAGE_SIZE = 1000
    STATUS = 'active'  # v5.87.10: verified, ready to run

    def normalize_row(self, attrs: dict):
        """Combine PROBLEM_DESC with LAST_ACTV for richer training text.

        Most rows have only ~50-char categorical descriptions in PROBLEM_DESC
        (e.g. "Junk/Trash/Overgrowth on Unimproved/Improved Prop"). Adding
        LAST_ACTV ("Case closed", "Notice of Violation issued", etc.) gives
        the labeler more context to classify category + severity.

        Both fields have trailing whitespace from the source — strip carefully.
        """
        problem = (attrs.get('PROBLEM_DESC') or '').strip()
        if not problem or len(problem) < 10:
            return None
        last_act = (attrs.get('LAST_ACTV') or '').strip()
        case_num = (attrs.get('CASE_NUM') or '').strip()
        # Combine when LAST_ACTV adds new info (not a duplicate of PROBLEM_DESC)
        if last_act and last_act.lower() not in problem.lower():
            text = f'{problem} — {last_act}'
        else:
            text = problem
        return {
            'text': text[:500],
            'external_id': case_num or None,
        }


class PhoenixCodeEnforcementCrawler(ArcGISCrawler):
    """Phoenix, AZ Neighborhood Services Department code enforcement cases.

    Phoenix publishes through mapping.phoenix.gov / data.phoenix.gov which
    runs an Esri stack. Desert climate findings (caliche soil, AC failures,
    extreme heat damage) under-represented in NYC/Chicago corpus.

    Activation checklist:
      [ ] Browse https://www.phoenixopendata.com/ for code cases
      [ ] May redirect to https://mapping.phoenix.gov/arcgis/rest/services/
      [ ] Find Code Compliance / Neighborhood Preservation layer
      [ ] curl with resultRecordCount=1 to inspect schema
      [ ] Update SERVICE_URL + TEXT_FIELD
      [ ] Flip STATUS='active'
    """
    SOURCE_NAME = 'phoenix_violations_v1'
    SERVICE_URL = ''  # placeholder
    TEXT_FIELD = 'description'  # placeholder
    STATE_CODE = 'AZ'
    RELEVANT_WHERE_CLAUSE = "description IS NOT NULL"
    MAX_ROWS = 10000
    STATUS = 'scaffold'


class AtlantaCodeEnforcementCrawler(ArcGISCrawler):
    """Atlanta, GA Department of City Planning code enforcement.

    Southeast US representation — humid subtropical climate findings (mold,
    foundation issues from clay soil, termite damage) that other corpus
    cities don't capture.

    Activation checklist:
      [ ] Browse https://opendata.atlantaregional.com/ — multi-county
      [ ] Or https://gis.atlantaga.gov/ — city specific
      [ ] Find Code Enforcement layer
      [ ] Inspect schema, update SERVICE_URL + TEXT_FIELD
      [ ] Flip STATUS='active'
    """
    SOURCE_NAME = 'atlanta_violations_v1'
    SERVICE_URL = ''  # placeholder
    TEXT_FIELD = 'description'  # placeholder
    STATE_CODE = 'GA'
    RELEVANT_WHERE_CLAUSE = "description IS NOT NULL"
    MAX_ROWS = 10000
    STATUS = 'scaffold'


# ═══════════════════════════════════════════════════════════════════════════
# v5.87.17 additions — discovered during ArcGIS Hub batch session.
# Both Detroit and Columbus migrated off Socrata to ArcGIS Hub. Found their
# violation/enforcement layers via DCAT-US catalog feeds and verified live.
# ═══════════════════════════════════════════════════════════════════════════


class DetroitBlightTicketsCrawler(ArcGISCrawler):
    """Detroit Blight Tickets — civil code violation tickets issued by city.

    Source: Detroit ArcGIS Hub, blight_tickets FeatureServer/0.
    Verified live in v5.87.17:
      - 47-field schema with ordinance_description, ticket_issued_date, etc.
      - 880,271 total tickets / 333,325 since 2022-01-01
      - Text quality: short categorical (e.g. "Failure of owner to obtain
        certificate of compliance", "Bulk solid waste deposited more than 24
        hours before designated time"). Comparable to Austin/LA short codes
        but covers the Michigan/Midwest cold-climate gap.

    Detroit's ticketing system is broader than typical "code violations" —
    includes bulk waste, parking, vacant property maintenance — but every
    row is an inspector-issued citation tied to a real property issue.

    Filter: ticket_issued_date > 2022-01-01 and ordinance_description IS NOT NULL
    so we don't ingest 880K rows of which most are old or empty.
    """
    SOURCE_NAME = 'detroit_blight_v1'
    SERVICE_URL = 'https://services2.arcgis.com/qvkbeam7Wirps6zC/arcgis/rest/services/blight_tickets/FeatureServer/0'
    TEXT_FIELD = 'ordinance_description'
    STATE_CODE = 'MI'
    # ArcGIS WHERE syntax differs from Socrata — uses SQL-92 against the layer.
    # Date comparisons use DATE 'YYYY-MM-DD' format.
    RELEVANT_WHERE_CLAUSE = (
        "ordinance_description IS NOT NULL "
        "AND ticket_issued_date > DATE '2022-01-01'"
    )
    MAX_ROWS = 10000
    STATUS = 'active'  # v5.87.17: verified live, 333K rows since 2022

    def normalize_row(self, attrs):
        """Combine ordinance_description with ordinance_law for richer signal.

        Example: '[9-1-36(a)] Failure of owner to obtain certificate of
        compliance'. The ordinance_law gives a code reference; the description
        gives the human-readable summary. Together they help the labeler
        distinguish similar-sounding violations across categories.
        """
        desc = (attrs.get('ordinance_description') or '').strip()
        if not desc or len(desc) < 10:
            return None
        law = (attrs.get('ordinance_law') or '').strip()
        ticket = (attrs.get('ticket_number') or '').strip()
        if law and law not in desc:
            text = f'[{law}] {desc}'
        else:
            text = desc
        return {
            'text': text[:500],
            'external_id': ticket or None,
        }


class ColumbusCodeEnforcementCrawler(ArcGISCrawler):
    """Columbus, OH Code Enforcement Cases.

    Source: maps2.columbus.gov BuildingZoning MapServer/23 'Code Enforcement Cases'.
    Verified live in v5.87.17:
      - 17-field schema with B1_PER_TYPE, B1_PER_SUB_TYPE, INSP_LAST_RESULT,
        and other case-tracking fields
      - 289,759 total / 179,623 since 2022-01-01
      - Text quality: structured fields (no narrative description). Combining
        B1_PER_TYPE ("Zoning Code Inspection") + B1_PER_SUB_TYPE ("Parking
        Boat RV Trailer") + INSP_LAST_RESULT ("In Compliance" / "Issue Notice")
        gives a useful labeler-friendly phrase.

    Ohio Valley humid continental climate, freeze-thaw cycle, distinct from
    coastal/southern climates already in the corpus.

    Note: layer is a MapServer (not FeatureServer). The ArcGISCrawler base
    class handles both — query path is the same.
    """
    SOURCE_NAME = 'columbus_code_v1'
    SERVICE_URL = 'https://maps2.columbus.gov/arcgis/rest/services/Schemas/BuildingZoning/MapServer/23'
    TEXT_FIELD = 'B1_PER_SUB_TYPE'
    STATE_CODE = 'OH'
    RELEVANT_WHERE_CLAUSE = (
        "B1_PER_SUB_TYPE IS NOT NULL "
        "AND B1_FILE_DD > DATE '2022-01-01'"
    )
    MAX_ROWS = 10000
    STATUS = 'active'  # v5.87.17: verified live, 179K rows since 2022

    def normalize_row(self, attrs):
        """Combine B1_PER_TYPE + B1_PER_SUB_TYPE + INSP_LAST_RESULT into a
        descriptive phrase the labeler can interpret.

        Example: '[Zoning Code Inspection] Parking Boat RV Trailer — Issue Notice'
        """
        sub = (attrs.get('B1_PER_SUB_TYPE') or '').strip()
        if not sub or len(sub) < 5:
            return None
        per_type = (attrs.get('B1_PER_TYPE') or '').strip()
        result = (attrs.get('INSP_LAST_RESULT') or '').strip()
        case_id = (attrs.get('B1_ALT_ID') or '').strip()
        # Build phrase
        parts = []
        if per_type and per_type.lower() not in sub.lower():
            parts.append(f'[{per_type}]')
        parts.append(sub)
        text = ' '.join(parts)
        if result and result.lower() not in ('open', 'closed', 'pending', '', 'na'):
            # Only add result if it's informative (e.g. "Issue Notice" or "In Compliance")
            text = f'{text} — {result}'
        return {
            'text': text[:500],
            'external_id': case_id or None,
        }


# ═══════════════════════════════════════════════════════════════════════════
# v5.87.18 additions — discovered via ArcGIS Hub federated search.
# ═══════════════════════════════════════════════════════════════════════════


class AdamsCountyCodeEnforcementCrawler(ArcGISCrawler):
    """Adams County, CO Code Enforcement Cases.

    Source: services3.arcgis.com/4PNQOtAivErR7nbT/.../CodeEnforcementCases/FeatureServer/0
    Discovered v5.87.18 via ArcGIS Hub federated search at
    hub.arcgis.com/api/search/v1/collections/dataset/items?q=code+enforcement+cases.

    Verified live in v5.87.18:
      - 24-field schema with Description, Type, CaseOpened, CaseClosed, etc.
      - 13,869 total / 7,443 since 2022-01-01
      - Description field has REAL inspector narrative text:
          "Long weeds, brush, and grass.  Junk and rubbish in the driveway."
          "Landlord moving campers, trailers, and trash into backyard."
          "Bringing fill dirt onto property without a permit. Parking cars
           on the dirt that was brought in."
      - Quality: narrative (full sentences), comparable to NOLA / Chicago

    Adams County is the suburban-Denver area (Aurora, Brighton, Commerce City).
    Colorado mountain-west representation: high-altitude, freeze-thaw extreme,
    snow load, semi-arid. None of that pattern is in any other corpus city.
    Modest size (7K rows since 2022) but very high text quality per row.

    normalize_row combines Type tag with Description for category context
    (Type is "Blight" or "Zoning" — useful for the labeler).
    """
    SOURCE_NAME = 'adams_county_co_v1'
    SERVICE_URL = 'https://services3.arcgis.com/4PNQOtAivErR7nbT/arcgis/rest/services/CodeEnforcementCases/FeatureServer/0'
    TEXT_FIELD = 'Description'
    STATE_CODE = 'CO'
    RELEVANT_WHERE_CLAUSE = (
        "Description IS NOT NULL "
        "AND CaseOpened > DATE '2022-01-01'"
    )
    MAX_ROWS = 10000
    STATUS = 'active'  # v5.87.18: verified live, narrative quality

    def normalize_row(self, attrs):
        """Combine Type tag with Description for richer signal.

        Example: '[Blight] Long weeds, brush, and grass. Junk and rubbish in
        the driveway.'
        """
        desc = (attrs.get('Description') or '').strip()
        if not desc or len(desc) < 10:
            return None
        type_tag = (attrs.get('Type') or '').strip()
        record_id = (attrs.get('Record_ID') or '').strip()
        if type_tag and type_tag.lower() not in desc.lower():
            text = f'[{type_tag}] {desc}'
        else:
            text = desc
        return {
            'text': text[:500],
            'external_id': record_id or None,
        }


class AugustaGACodeViolationsCrawler(ArcGISCrawler):
    """Augusta-Richmond County, GA Code Enforcement service requests.

    Source: augcw.augustaga.gov Cityworks SR layer (FeatureServer/1).
    Discovered v5.87.19 via ArcGIS Hub federated search for property
    maintenance violations, then verified live.

    Verified:
      - 94-field schema (Cityworks-standard service request layout)
      - 857 total rows with non-null Comments
      - Comments field has REAL inspector field-note narrative:
          "Vacant property. BPadget with ES was at location on 10/21/2020.
           Took photographs for them (ES) to address."
          "Spoke to Frank (Kim's husband/Danielle's dad). He FaceTimed me
           and showed all cars are running. Also sent me copy of insurance."
          "Property in compliance. Vehicles run. He was to get tires off
           lot 420. Moved tires while Marshals on site."
      - ProblemCode field provides category tag ("Nuisance Property",
        "Abandoned Vehicle")
      - Sample dates are 2020-era — older data, not current; but text
        quality is exceptional per row.

    HONEST CAVEATS:
      - Small dataset (~857 rows) compared to ~10K from Detroit/Adams
      - Older data (2020) — may not reflect current enforcement patterns
      - Activated for Georgia/Southeast representation in spite of size,
        because text quality per row is among the highest in the corpus.

    normalize_row prepends ProblemCode tag to Comments.
    """
    SOURCE_NAME = 'augusta_ga_v1'
    SERVICE_URL = 'https://augcw.augustaga.gov/CityworksForms/gis/2/8093/rest/services/cw/FeatureServer/1'
    TEXT_FIELD = 'Comments'
    STATE_CODE = 'GA'
    RELEVANT_WHERE_CLAUSE = "Comments IS NOT NULL"
    MAX_ROWS = 2000  # Capped at expected dataset size + buffer
    STATUS = 'active'  # v5.87.19: verified live, narrative quality

    def normalize_row(self, attrs):
        """Combine ProblemCode tag with Comments field-notes.

        Example: '[Nuisance Property] Vacant property. BPadget with ES was
        at location on 10/21/2020. Took photographs for them (ES) to address.'
        """
        comments = (attrs.get('Comments') or '').strip()
        if not comments or len(comments) < 15:
            return None
        problem = (attrs.get('ProblemCode') or '').strip()
        request_id = attrs.get('REQUESTID')
        request_id = str(request_id) if request_id is not None else None
        if problem and problem.lower() not in comments.lower():
            text = f'[{problem}] {comments}'
        else:
            text = comments
        return {
            'text': text[:500],
            'external_id': request_id,
        }


class IndianapolisCodeEnforcementCrawler(ArcGISCrawler):
    """Indianapolis Code Enforcement Violations and Investigations.

    Source: gis.indy.gov/server/rest/services/OpenData/OpenData_NonSpatial/MapServer/1
    Discovered v5.87.21 via ArcGIS Hub federated search "Indianapolis code violation".

    Verified live in v5.87.21:
      - 12-field schema (CASE_NUMBER, CASE_TYPE, CASE_STATUS, OPEN_DATE, address, owner)
      - 910,483 total rows / 106,188 since 2022-01-01
      - CASE_TYPE field has structured 4-level slash-separated categorical
        hierarchy: action / phase / category / penalty. Examples:
          "Enforcement/Investigation/Zoning/NA"          (19,447 rows)
          "Enforcement/Investigation/High Weeds & Grass/NA" (17,154 rows)
          "Enforcement/Violation/Building/NA"             (6,928 rows)
          "Enforcement/Investigation/Unsafe Buildings/NA" (4,607 rows)
          "Enforcement/Violation/Vacant Board Order/NA"   (3,935 rows)
          "Enforcement/Violation/Demolition/NA"           (516 rows)

    Quality: structured short codes, NOT narrative — but the 4-level hierarchy
    encodes more signal than typical short codes. Distribution is genuinely
    diverse across categories (weeds, trash, building, unsafe, vacant, zoning,
    demolition, infrastructure, air quality), unlike LA where 80%+ of rows are
    "RECEPTACLE N/G" type repeats.

    Indiana representation matters: midwest humid continental climate, freeze-
    thaw cycle, tornado country (different from Cleveland/Cincinnati patterns).
    Indianapolis is the 14th most populous metro and previously had zero
    representation in our corpus.

    normalize_row parses the CASE_TYPE hierarchy into a labeler-friendly
    descriptive phrase like "[Building Violation] Unsafe Building" rather
    than dumping the raw "Enforcement/Violation/Building/NA" string.
    """
    SOURCE_NAME = 'indianapolis_code_v1'
    SERVICE_URL = 'https://gis.indy.gov/server/rest/services/OpenData/OpenData_NonSpatial/MapServer/1'
    TEXT_FIELD = 'CASE_TYPE'
    STATE_CODE = 'IN'
    RELEVANT_WHERE_CLAUSE = (
        "CASE_TYPE IS NOT NULL "
        "AND OPEN_DATE > DATE '2022-01-01'"
    )
    MAX_ROWS = 10000
    STATUS = 'active'  # v5.87.21: verified live, 106K post-2022 rows

    def normalize_row(self, attrs):
        """Parse Indianapolis's 4-level slash-hierarchy CASE_TYPE into a
        more semantic phrase.

        Raw: "Enforcement/Investigation/High Weeds & Grass/NA"
        Out: "[High Weeds & Grass] Investigation"

        Raw: "Enforcement/Violation/Building/NA"
        Out: "[Building] Violation"

        The CATEGORY (slot 2) becomes the [tag], and PHASE (slot 1) becomes
        the descriptor. Penalty (slot 3) is appended only when not "NA".
        """
        case_type = (attrs.get('CASE_TYPE') or '').strip()
        if not case_type or len(case_type) < 5:
            return None

        # Parse slash hierarchy
        parts = case_type.split('/')
        if len(parts) >= 3:
            phase = parts[1].strip()       # Investigation / Violation / Citation / Vehicle / Legal
            category = parts[2].strip()    # Building / Zoning / High Weeds & Grass / Trash / etc
            penalty = parts[3].strip() if len(parts) >= 4 else ''
            text = f'[{category}] {phase}'
            if penalty and penalty.upper() != 'NA':
                text += f' (penalty {penalty})'
        else:
            # Fallback for malformed entries
            text = case_type

        case_no = (attrs.get('CASE_NUMBER') or '').strip()
        return {
            'text': text[:500],
            'external_id': case_no or None,
        }
