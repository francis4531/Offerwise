"""Synthesize state-diverse home inspection findings to close geographic coverage gap.

Background (v5.86.93):
  Training data is 96% NYC municipal code violations. When a California or
  Florida user runs an analysis, the classifier predicts based on NYC patterns
  that don't map to their region (no seismic in NYC data; no hurricane/flood
  patterns; no expansive-clay-soil foundation issues).

  Path 6 of the geographic diversity roadmap: generate deliberately diverse
  synthetic findings tagged to state-specific concerns. Cheaper and faster than
  scraping county data (Path 2) or parsing legal disclosures (Path 5).

Quality lessons from ai_augmented (v5.86.92):
  Previous augmented data used vivid catastrophic language for critical
  findings, creating spurious correlation between linguistic register and
  severity. This generator explicitly aims for MUNDANE realistic language
  at the 15/35/35/15 severity distribution, with each batch covering a
  specific (state, concern) combination to force vocabulary diversity.

Per-state concerns:
  CA: seismic, wildfire, older housing stock, stucco
  FL: hurricane, flood, humidity/mold, stucco
  TX: expansive clay foundation, extreme weather, older electrical
  AZ: expansive soils, heat stress, adobe-specific issues
  CO: altitude, radon, snow load
  WA: moisture, seismic, old cedar shake roofs
  IL: cold damage, ice dam, aging infrastructure
  MI: cold, ice dam, lake-effect moisture
  GA: termite, moisture, red clay
  NC: termite, moisture, coastal + inland variations

Architecture:
  - Inherits BaseBatchLabeler (Haiku 4.5 + Batch API, 50% cheaper)
  - Generates via "generation prompts" (state + concern + severity) rather
    than labeling existing rows
  - Writes new MLFindingLabel rows with source='ai_state_synthetic_v1'
  - Pre-populates category_v2, severity_v2, geographic_region so the rows
    are immediately usable without re-labeling
  - labeling_confidence=0.6 (synthetic — lower than real-data 0.85+)

Cost estimate:
  500 findings per state × 10 states = 5,000 findings total
  Per batch: 20 findings generated in one Claude call
  Total batches: 250
  Each batch: ~1,200 input + ~1,400 output tokens
  Haiku 4.5: $1/M input, $5/M output. Batch API: 50% off.
  Total: ~$3-5 for one full run
"""
from __future__ import annotations

import json
from typing import Optional

from ml_ingestion.batch_labeler import BaseBatchLabeler


VALID_CATEGORIES = [
    'electrical', 'environmental', 'foundation_structure', 'general',
    'hvac_systems', 'plumbing', 'roof_exterior',
]

VALID_SEVERITIES = ['critical', 'major', 'moderate', 'minor']


# State-specific concern areas. Each state has 3-5 concern areas. Each concern
# area is paraphrased concisely enough for the generation prompt to include.
STATE_CONCERNS = {
    'CA': {
        'state_name': 'California',
        'concerns': [
            'seismic retrofitting (older homes pre-1979 lacking foundation bolting, cripple wall bracing, soft-story weakness)',
            'wildfire risk (ember-vulnerable vents, combustible roof materials, cluttered defensible space, older wood-shake roofs)',
            'older housing stock (pre-1960 knob-and-tube wiring still in walls, galvanized plumbing nearing end of life, original cast-iron drainage)',
            'stucco and moisture intrusion (cracked stucco, weep screed defects, failed flashings at windows)',
            'drought and expansive clay (settling foundations in Central Valley, irrigation effects on soil movement)',
        ],
    },
    'FL': {
        'state_name': 'Florida',
        'concerns': [
            'hurricane and wind mitigation (missing hurricane ties, inadequate roof-to-wall connections, older shutters, unrated garage doors)',
            'flood and water intrusion (base elevations, sump pump conditions, grade sloping toward foundation, chronic moisture at slab)',
            'humidity and mold (inadequate bathroom ventilation, HVAC condensate issues, visible mold in attics and crawlspaces)',
            'stucco and CMU block construction (cracking at control joints, failed paint systems, efflorescence on block walls)',
            'termite and wood-destroying organism evidence (active swarmer tubes, damaged fascia, sill plate deterioration)',
        ],
    },
    'TX': {
        'state_name': 'Texas',
        'concerns': [
            'expansive clay foundation issues (slab heave and settlement, cracks in brick veneer, sticking doors, interior drywall cracks)',
            'extreme weather damage (hail impact on shingles, storm-related flashing separation, wind damage to fascia)',
            'older electrical systems (aluminum branch wiring in homes 1965-1973, Federal Pacific Stab-Lok panels, ungrounded outlets)',
            'HVAC systems in hot climate (oversized units, short-cycling, high humidity from poor sizing, condensate drain clogs)',
            'pier-and-beam foundations (crawlspace moisture, pier settling, joist sag, subfloor rot)',
        ],
    },
    'AZ': {
        'state_name': 'Arizona',
        'concerns': [
            'expansive soils and foundation movement (slab cracking patterns, doorframe racking, exterior stucco cracks from differential settlement)',
            'extreme heat stress on roofing (tile substrate failure, UV degradation of underlayment, rapid asphalt-shingle aging)',
            'HVAC stress from 115°F+ summers (condenser unit failures, duct degradation, insufficient insulation in attics)',
            'adobe and older stucco construction (rising damp, salt efflorescence, crumbling interior plaster)',
            'water intrusion despite arid climate (monsoon-season flash flooding, roof valley issues, poor grading)',
        ],
    },
    'CO': {
        'state_name': 'Colorado',
        'concerns': [
            'radon at EPA action levels (sub-slab depressurization needed, short-term test above 4 pCi/L common, crawlspace entry paths)',
            'snow load and ice dam damage (ceiling stains from ice dams, insufficient attic insulation, failed underlayment)',
            'altitude-related HVAC concerns (combustion air adjustments, water heater venting at altitude, boiler efficiency)',
            'expansive soils in Front Range (heaving basement slabs, foundation lateral pressure, bentonite soils)',
            'wildfire risk in foothills (combustible decking, uncleared defensible space, ember-vulnerable soffit vents)',
        ],
    },
    'WA': {
        'state_name': 'Washington',
        'concerns': [
            'moisture intrusion in mild wet climate (chronic leaks at window sills, siding decay, inadequate flashing at penetrations)',
            'seismic concerns Cascadia subduction zone (unbolted foundations pre-1985, unbraced cripple walls, unreinforced masonry chimneys)',
            'cedar shake roof failures (moss growth, curling/cupping shakes, cedar nearing end of life at 20-25 years)',
            'crawlspace issues (standing water, inadequate vapor barriers, rim joist rot, hantavirus/rodent concerns)',
            'older home galvanized plumbing (flow restrictions, pinhole leaks, lead solder at joints)',
        ],
    },
    'IL': {
        'state_name': 'Illinois',
        'concerns': [
            'freeze-thaw cycle damage (cracked foundation from water expansion, heaved concrete driveways, frost heave at footings)',
            'ice dam formation and attic issues (inadequate insulation, missing ice-and-water shield, gutter backup)',
            'aging housing stock in Chicago metro (knob-and-tube wiring, original cast-iron drainage, unpermitted basement conversions)',
            'HVAC in cold climate (furnace heat exchanger cracks, inefficient older units, venting condensation issues)',
            'basement moisture (lateral foundation leaks, sump pump reliability, window well drainage)',
        ],
    },
    'MI': {
        'state_name': 'Michigan',
        'concerns': [
            'cold climate issues (ice dams, insufficient attic insulation, heat cable degradation on roofs)',
            'lake-effect moisture (high humidity year-round, chronic basement dampness, mold in rim joists)',
            'foundation issues from freeze-thaw (bowing basement walls, efflorescence, waterproofing failures)',
            'older housing stock around Detroit (lead paint, galvanized plumbing, aging electrical service)',
            'septic and well water concerns (rural areas, aging septic tanks, well cap conditions)',
        ],
    },
    'GA': {
        'state_name': 'Georgia',
        'concerns': [
            'termite and wood-destroying organism activity (mud tubes on foundation, damaged sill plates, previous treatment history unclear)',
            'humidity and moisture issues (crawlspace moisture, mold in attics, HVAC condensate mismanagement)',
            'red clay soil and foundation movement (minor settling patterns, retaining wall issues, erosion at downspouts)',
            'storm damage (hail from summer storms, wind damage to shingles, tree limb impact)',
            'older home issues in Atlanta metro (lead paint 1978 and earlier, aluminum wiring 1965-1973, aging HVAC)',
        ],
    },
    'NC': {
        'state_name': 'North Carolina',
        'concerns': [
            'termite pressure (coastal and Piedmont regions, active infestations, treatment history, sill plate damage)',
            'hurricane/tropical storm damage (inland wind damage, coastal flooding, saltwater corrosion of hardware)',
            'moisture in humid climate (crawlspace encapsulation issues, mold in attics, HVAC oversizing)',
            'red clay expansive soil (differential settling, retaining wall lean, hillside drainage)',
            'older mountain homes (knob-and-tube, galvanized plumbing, aging septic in rural areas)',
        ],
    },
}


# Target severity distribution per (state, concern) batch.
# We want 15% critical, 35% major, 35% moderate, 15% minor overall.
# Per-batch of 20: 3 critical, 7 major, 7 moderate, 3 minor.
SEVERITY_TARGETS = {'critical': 3, 'major': 7, 'moderate': 7, 'minor': 3}


SYNTHESIS_PROMPT_TEMPLATE = """You are generating realistic, DIVERSE home inspection findings for a {state_name} property to balance a training dataset.

Produce exactly 20 findings using the format below. The findings must use MUNDANE REALISTIC LANGUAGE — not dramatic catastrophic prose. Imagine a pragmatic working inspector, not an alarmist.

CONCERN AREA: {concern}

EXACTLY 20 FINDINGS DISTRIBUTED AS:
- 3 critical severity
- 7 major severity
- 7 moderate severity
- 3 minor severity

CATEGORY TAXONOMY (use the most specific; AVOID 'general' unless truly nonspecific):
- electrical: wiring, panels, outlets, breakers, electrical safety hazards
- environmental: lead, radon, asbestos, mold, pests, hazardous materials, air/water quality
- foundation_structure: foundation, framing, load-bearing elements, structural cracks
- hvac_systems: heating, cooling, ducts, ventilation, thermostats, boilers, furnaces, A/C
- plumbing: water supply, drains, fixtures, water heaters, sewer lines
- roof_exterior: roof covering, gutters, siding, exterior paint/trim, windows/doors from outside, decks, fascia, chimneys
- general: ONLY when genuinely nonspecific

SEVERITY GUIDE (use real domain thresholds):
- critical: active hazard, imminent failure — gas leaks, active structural failure, radon >20 pCi/L, visible active mold + child present
- major: significant defect, $5K-$25K typical repair — aging systems past useful life, multi-point failures, moderate mold
- moderate: defect needing attention in 6-12 months, $500-$5K — routine-but-real maintenance items, early-stage wear, moderate concerns
- minor: cosmetic or low-urgency, under $500 — caulking, minor paint, worn weather stripping

LANGUAGE RULES — MUNDANE NOT DRAMATIC:
- Use specific measurements where they fit (sizes, percentages, years of age, counts) — but include them ONLY when realistic
- Pragmatic working-inspector voice. No "CATASTROPHIC FAILURE IMMINENT" hyperbole
- Include BORDERLINE cases at the moderate/major boundary, not just extremes
- Vary sentence structure: some use technical terms, some plain language
- Reference the {state_name}-specific concern naturally without labeling it as such

EXAMPLES of appropriate language by severity:
- critical: "Radon test result of 22 pCi/L in basement, substantially above EPA action level of 4 pCi/L. Sub-slab depressurization system recommended."
- major: "Electrical panel is Federal Pacific Stab-Lok, a model with documented breaker failure rates. Replacement recommended."
- moderate: "Minor grade sloping toward foundation on south elevation. Monitor for future water intrusion; regrading recommended within 1-2 years."
- minor: "Caulking separation at bathroom tub/tile joint. Routine maintenance item."

OUTPUT — respond with ONLY a JSON array of 20 objects. Each object:
{{
  "text": "realistic finding text, 15-60 words",
  "category": "one of the 7 categories",
  "severity": "critical|major|moderate|minor"
}}

Do not include any preamble, explanation, or markdown fencing. Just the JSON array."""


class StateDiverseSynthesizerV1(BaseBatchLabeler):
    """Synthesizer that generates diverse state-specific inspection findings.

    Unlike RelabelerV1 (which labels existing rows), this creates NEW rows.
    The "id" we pass to get_batch() is a fabricated request ID, not an
    existing row ID.

    Configuration via config dict:
      per_state (int, default 500): findings per state
      states (list, default all 10): which states to generate for
      skip_existing (bool, default True): don't re-generate if already done

    Usage:
      SyntheticV1 = StateDiverseSynthesizerV1(config={'per_state': 500})
      SyntheticV1.run()
    """

    JOB_TYPE = 'synthesize'
    SOURCE_NAME = 'ai_state_synthetic_v1'
    BATCH_SIZE = 20
    MODEL = 'claude-haiku-4-5'

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        cfg = config or {}
        self._per_state = cfg.get('per_state', 500)
        requested_states = cfg.get('states') or list(STATE_CONCERNS.keys())
        # Validate: only include states we have concerns defined for
        self._states = [s for s in requested_states if s in STATE_CONCERNS]
        self._skip_existing = cfg.get('skip_existing', True)

        # Build the list of (state, concern, severity_batch) generation requests
        self._request_queue = self._plan_requests()
        self._request_index = 0
        self._log(f'Planned {len(self._request_queue)} generation batches across '
                  f'{len(self._states)} states')

    def _plan_requests(self) -> list[dict]:
        """Plan the full set of generation requests up front.

        For each state:
          - target = per_state findings
          - batches_per_state = per_state / BATCH_SIZE (e.g. 500/20 = 25)
          - Distribute batches across concern areas round-robin so each
            concern gets roughly equal coverage

        Returns:
          list of dicts: [{id, state, concern, concern_idx}, ...]
        """
        # Check existing synthetic rows to avoid duplicating work
        existing_by_state = {}
        if self._skip_existing:
            from models import MLFindingLabel, db
            from sqlalchemy import func
            rows = (db.session.query(
                MLFindingLabel.geographic_region,
                func.count(MLFindingLabel.id),
            )
            .filter(MLFindingLabel.source == self.SOURCE_NAME)
            .group_by(MLFindingLabel.geographic_region)
            .all())
            for state, count in rows:
                if state:
                    existing_by_state[state] = count

        queue = []
        request_id = 0
        for state in self._states:
            already = existing_by_state.get(state, 0)
            if already >= self._per_state:
                self._log(f'  {state}: already has {already} synthetic rows, skipping')
                continue

            remaining = self._per_state - already
            batches_needed = (remaining + self.BATCH_SIZE - 1) // self.BATCH_SIZE
            concerns = STATE_CONCERNS[state]['concerns']

            # Round-robin through concerns so each gets roughly equal batches
            for batch_n in range(batches_needed):
                request_id += 1
                concern_idx = batch_n % len(concerns)
                queue.append({
                    'id': request_id,
                    'state': state,
                    'concern': concerns[concern_idx],
                    'concern_idx': concern_idx,
                })

            if already > 0:
                self._log(f'  {state}: {already} existing, generating {remaining} more')
            else:
                self._log(f'  {state}: generating {remaining} findings ({batches_needed} batches)')

        return queue

    def get_batch(self) -> list[dict]:
        """Return the next generation request as a single-item 'batch'.

        Unlike RelabelerV1 which returns up to BATCH_SIZE row dicts, here
        each 'batch' is one generation request that will produce BATCH_SIZE
        findings. So we return a list of one dict (the generation spec).
        """
        if self._request_index >= len(self._request_queue):
            return []
        req = self._request_queue[self._request_index]
        self._request_index += 1
        return [req]

    def build_prompt(self, batch: list[dict]) -> str:
        """Build the synthesis prompt for this request.

        batch is always length-1 here (one generation spec per API call).
        """
        req = batch[0]
        state_name = STATE_CONCERNS[req['state']]['state_name']
        return SYNTHESIS_PROMPT_TEMPLATE.format(
            state_name=state_name,
            concern=req['concern'],
        )

    def parse_response(self, response_text: str, batch: list[dict]) -> list[dict]:
        """Parse Claude's response into individual finding dicts.

        Claude returns a JSON array of ~20 findings. The base class passes
        each parsed item to save_result(item_id=request_id, result=item).
        But we want one save_result call PER generated finding, not per
        request. So we return the list of findings — the base class will
        iterate.
        """
        findings = super().parse_response(response_text, batch)
        if not isinstance(findings, list):
            raise ValueError(f'Expected JSON array, got {type(findings)}')
        # Attach state info to each finding for save_result to use
        req = batch[0]
        for f in findings:
            f['_state'] = req['state']
            f['_concern_idx'] = req['concern_idx']
        return findings

    def _process_results(self, results_url: str = None) -> None:
        """Override base to handle 1-request → N-findings pattern.

        The base class's default behavior maps results back to batch_items
        by an 'id' field, expecting a 1:1 relationship. For synthesis we
        have 1 batch_item (generation request) producing N findings (20).
        So we iterate all parsed findings and call save_result on each.
        """
        try:
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
                if items_failed <= 5:
                    err = getattr(item_response.result, 'error', None)
                    self._log(f'Batch item {custom_id} failed: {err}', 'warn')
                continue

            batch_items = self._item_id_by_custom_id.get(f'{custom_id}_items', [])
            if not batch_items:
                items_skipped += 1
                continue

            message = item_response.result.message
            response_text = message.content[0].text if message.content else ''

            try:
                findings = self.parse_response(response_text, batch_items)
            except Exception as e:
                self._log(f'Parse failed for {custom_id}: {e}', 'warn')
                items_failed += 1
                continue

            # For synthesis: each parsed finding is its own to-be-saved row.
            # Don't try to map by id — just iterate and save all.
            request_id = batch_items[0]['id']
            for finding in findings:
                try:
                    self.save_result(request_id, finding)
                    items_saved += 1
                except Exception as save_err:
                    self._log(f'Save error: {save_err}', 'warn')
                    items_failed += 1

            # Commit every 500 saved
            if items_saved and items_saved % 500 == 0:
                self._finalize()

        # Final commit
        self._finalize()
        self._log(f'Results processed: saved={items_saved}, failed={items_failed}, skipped={items_skipped}')

    def save_result(self, item_id: int, result: dict) -> None:
        """Write ONE synthetic finding to ml_finding_labels.

        Note item_id here is the REQUEST ID (generation batch id), not a
        row id, since this is a create not update. We create a fresh row.
        """
        from models import db, MLFindingLabel

        text = (result.get('text') or '').strip()
        cat = (result.get('category') or '').lower().strip()
        sev = (result.get('severity') or '').lower().strip()
        state = result.get('_state', '')

        if len(text) < 20 or cat not in VALID_CATEGORIES or sev not in VALID_SEVERITIES:
            # Malformed — skip, log for diagnostics, don't insert bad data
            self._rows_rejected += 1
            return

        # Truncate text to a reasonable ceiling to avoid DB bloat
        text = text[:500]

        row = MLFindingLabel(
            finding_text=text,
            category=cat,
            severity=sev,
            category_v2=cat,
            severity_v2=sev,
            is_real_finding=True,
            source=self.SOURCE_NAME,
            geographic_region=state,
            labeling_confidence=0.6,  # synthetic — lower than real-data 0.85+
            labeling_notes=f'synthetic for {state} concern #{result.get("_concern_idx", 0)}',
            source_version='v1',
        )
        db.session.add(row)

        self._rows_added += 1

        # Commit every 100 rows to avoid lost work if process dies
        if self._rows_added % 100 == 0:
            db.session.commit()
            self._log(f'Progress: {self._rows_added} synthetic findings created')
