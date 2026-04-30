"""Stream 3 — Relabel existing ml_finding_labels rows with better Claude labels.

Background:
  The existing 62K rows in ml_finding_labels were labeled by ai_parse, which
  used a simpler prompt that often mis-classified things. NYC HPD violations
  get tagged 'environmental/minor' regardless of whether they're about lead
  paint (appropriate) or just peeling cosmetic paint (not environmental).
  Municipal violation taxonomy rows ('ELECTRICAL- HAZARD' as a standalone
  4-word string) got labeled as if they were findings.

  This relabeler runs Claude over every row and populates the v2 label
  columns with a better classification. Training can then prefer v2 labels
  over the original category/severity when available.

Architecture (updated v5.86.79):
  Uses BaseBatchLabeler (Anthropic Batch API) instead of synchronous calls.
  50% cheaper and runs async — a single submission covers all 3,134 batches,
  then we poll for completion and process the JSONL result file.

Model choice:
  Claude Haiku 4.5 — explicitly designed for "classification, routing,
  extraction" workloads per Anthropic docs. Delivers comparable quality to
  Sonnet 4 at 1/3 the cost. With Batch API discount, full run is ~$7.

Cost estimate:
  62,684 rows / 20 per batch = 3,134 Claude invocations
  Each: ~1,425 input tokens + ~600 output tokens
  Haiku 4.5 pricing: $1/M input, $5/M output
  Batch API discount: 50%
  Total: ~$7 for one full pass
"""
from __future__ import annotations

import json
from typing import Optional

from ml_ingestion.batch_labeler import BaseBatchLabeler


# The taxonomy the training pipeline uses. Must match cat_map values in
# ml_training_pipeline.py and admin_routes.py so that v2 labels are
# directly usable without further normalization.
VALID_CATEGORIES = [
    'electrical',
    'environmental',
    'foundation_structure',
    'general',
    'hvac_systems',
    'plumbing',
    'roof_exterior',
]
VALID_SEVERITIES = ['critical', 'major', 'moderate', 'minor']


RELABEL_PROMPT_TEMPLATE = """You are a home inspection expert relabeling training data for an ML classifier.

For each finding below, decide:
1. category_v2: the correct category from this taxonomy ONLY — one of: electrical, environmental, foundation_structure, general, hvac_systems, plumbing, roof_exterior
2. severity_v2: the appropriate severity — one of: critical, major, moderate, minor
3. is_real_finding: true if this describes a specific defect or condition in a home; false if it's boilerplate, a disclaimer, a municipal code title/taxonomy label, a permit/violation ID, an address, or other non-finding text
4. confidence: 0.0-1.0 — how certain you are about the classification
5. notes: brief reasoning in 80 chars or less

CATEGORY GUIDE:
- electrical: wiring, panels, outlets, breakers, electrical safety hazards
- environmental: lead, radon, asbestos, mold, pests, hazardous materials, air/water quality
- foundation_structure: foundation, framing, load-bearing elements, structural cracks
- general: catch-all for items not clearly fitting other categories; safety items without specific category
- hvac_systems: heating, cooling, ducts, ventilation, thermostats
- plumbing: water supply, drains, fixtures, water heaters, sewer lines
- roof_exterior: roof covering, gutters, siding, exterior paint/trim, windows/doors from outside, decks

SEVERITY GUIDE (use real domain thresholds, not keyword matching):
- critical: active safety hazard or imminent catastrophic failure (active gas leak, structural collapse imminent, radon >20 pCi/L, lead blood level >40 μg/dL, active electrical fire risk)
- major: significant defect requiring prompt repair ($5K-25K typical, still functional but degrading — radon 10-20 pCi/L, moderate mold, roof at end of life, water heater near failure)
- moderate: defect warranting attention within 6-12 months ($500-5K — radon 4-10 pCi/L at EPA action level, HVAC needing service, moderate granule loss, minor water intrusion)
- minor: cosmetic or low-urgency (<$500 — caulking, minor paint peeling, worn weather stripping, routine maintenance)

IS_REAL_FINDING GUIDE:
- TRUE examples: "Foundation shows horizontal crack with 1/4 inch displacement", "Radon measured at 6.2 pCi/L in basement"
- FALSE examples: "ELECTRICAL- HAZARD" (municipal code title only), "219 WEST 145TH STREET" (address), "SECURITY GATE" (violation type name, no finding), "These services may include radon testing" (disclaimer boilerplate), "VIO25-50470" (violation ID)
- If the text is <30 chars and looks like a category label without a described condition → false
- If the text is a disclaimer, exclusion, or scope-of-inspection statement → false

INPUT — findings to label (JSON array):
{findings_json}

OUTPUT — respond with ONLY a JSON array, one object per input item:
[{{"id": 123, "category_v2": "roof_exterior", "severity_v2": "minor", "is_real_finding": true, "confidence": 0.9, "notes": "cosmetic exterior paint issue"}}]
"""


# v5.86.88 — v2 prompt that addresses two bugs found in v1 labeling:
#   1. 43% of real findings were bucketed as 'general' (too broad)
#   2. 37.8% were labeled 'minor' vs 15% target; 'moderate' was starved at 19%
#
# The v2 prompt explicitly discourages general as a default AND gives severity
# distribution guidance with concrete examples for each bucket.
RELABEL_PROMPT_V2_TEMPLATE = """You are a home inspection expert relabeling training data for an ML classifier. Prior labeling was too aggressive with the 'general' category and over-used 'minor' severity. Your job is to produce more precise labels.

For each finding below, decide:
1. category_v2: the correct category from this taxonomy ONLY — one of: electrical, environmental, foundation_structure, general, hvac_systems, plumbing, roof_exterior
2. severity_v2: the appropriate severity — one of: critical, major, moderate, minor
3. is_real_finding: true if this describes a specific defect or condition in a home; false if it's boilerplate, a disclaimer, a municipal code title/taxonomy label, a permit/violation ID, an address, or other non-finding text
4. confidence: 0.0-1.0 — how certain you are about the classification
5. notes: brief reasoning in 80 chars or less

CATEGORY GUIDE — use the most specific category that fits. 'general' is a LAST RESORT.
- electrical: wiring, panels, outlets, breakers, electrical safety hazards (smoke detectors, GFCI, knob-and-tube, exposed wiring)
- environmental: lead, radon, asbestos, mold, pests, hazardous materials, air/water quality. NOTE: A municipal violation labeled 'environmental' in its category code is NOT necessarily environmental — judge by the finding text itself.
- foundation_structure: foundation, framing, load-bearing elements, structural cracks, beams, joists, slab issues
- hvac_systems: heating, cooling, ducts, ventilation, thermostats, boilers, furnaces, A/C units
- plumbing: water supply, drains, fixtures, water heaters, sewer lines, leaks, toilets, sinks
- roof_exterior: roof covering, gutters, siding, exterior paint/trim, windows/doors from outside, decks, fascia, soffits, chimneys
- general: ONLY when text is genuinely about something that doesn't fit above (e.g. "disrepair at building — needs broad scope" or missing documentation). If you can tell it involves a specific system (paint, roof, wiring, plumbing, structure), USE THAT CATEGORY. Do not use 'general' just because you're unsure.

DECISION RULE: If you're picking 'general', ask yourself: "Can I identify the system or location this is about?" If yes, use the specific category. If truly no, then general is correct. Over-use of general hurts the classifier.

SEVERITY GUIDE — use real domain thresholds, NOT keyword matching. Aim for roughly 15% critical / 35% major / 35% moderate / 15% minor across a batch when possible.

- critical (~15% of findings): active safety hazard or imminent failure. Gas leak, active fire hazard, structural collapse risk, radon >20 pCi/L, visible severe mold, lead paint flaking with child present.
- major (~35% of findings): significant defect requiring prompt repair, $5K-25K typical. Roof at end of life, water heater past lifespan, knob-and-tube wiring, foundation cracks >1/4", moderate mold, multiple GFCI failures.
- moderate (~35% of findings): defect warranting attention in 6-12 months, $500-5K typical. Radon 4-10 pCi/L, HVAC needing service, partial granule loss, minor water intrusion, one failed GFCI, worn but functional components.
- minor (~15% of findings): cosmetic or routine maintenance, <$500. Caulking needed, minor cosmetic paint issues, worn weather stripping, dirty filters, loose fixtures.

DECISION RULE FOR SEVERITY: When a finding is between minor and moderate, lean moderate unless it's purely cosmetic. Municipal violations ('failure to maintain', 'violation of section X') are usually moderate, not minor — they indicate real defects even if the text is bureaucratic.

IS_REAL_FINDING GUIDE:
- TRUE: "Foundation shows horizontal crack with 1/4 inch displacement", "Radon measured at 6.2 pCi/L in basement", "Paint peeling on south elevation near windows"
- FALSE: "ELECTRICAL- HAZARD" (municipal code title only), "219 WEST 145TH STREET" (address), "SECURITY GATE" (violation type name, no finding), "These services may include radon testing" (disclaimer boilerplate), "VIO25-50470" (violation ID), "See section 27-2005" (cross-reference only)
- If the text is <30 chars and is just a category label without describing a condition → false
- If the text is a disclaimer, exclusion, or scope-of-inspection statement → false

INPUT — findings to label (JSON array):
{findings_json}

OUTPUT — respond with ONLY a JSON array, one object per input item:
[{{"id": 123, "category_v2": "roof_exterior", "severity_v2": "minor", "is_real_finding": true, "confidence": 0.9, "notes": "cosmetic exterior paint issue"}}]
"""


# Filter expression parser for targeted re-labeling.
# Accepts: "key=value, key=value" — AND-combined across clauses.
# Valid keys: category, category_v2, severity, severity_v2, source, is_real_finding
# Values: strings, OR boolean for is_real_finding ("true"/"false"), OR special
# sentinel "null" meaning IS NULL.
_FILTER_VALID_KEYS = {
    'category', 'category_v2', 'severity', 'severity_v2',
    'source', 'is_real_finding',
}


def apply_filter_expression(query, filter_str: str):
    """Apply a comma-separated key=value filter to an MLFindingLabel query.

    Used by both the preview endpoint and the RelabelerV1 class so the UI's
    "N rows would match" count matches the actual re-label's scope.

    Examples:
      "category_v2=general"              → rows labeled general
      "category_v2=general, severity_v2=minor"
      "source=nyc_hpd, is_real_finding=true"
      "category_v2=null"                 → rows that haven't been v2-labeled yet

    Raises ValueError for unknown keys or malformed expressions.
    """
    from models import MLFindingLabel

    if not filter_str or not filter_str.strip():
        return query

    clauses = [c.strip() for c in filter_str.split(',') if c.strip()]
    for clause in clauses:
        if '=' not in clause:
            raise ValueError(f'malformed clause "{clause}" (expected key=value)')
        key, val = clause.split('=', 1)
        key, val = key.strip().lower(), val.strip()
        if key not in _FILTER_VALID_KEYS:
            raise ValueError(
                f'unknown filter key "{key}" (valid: {", ".join(sorted(_FILTER_VALID_KEYS))})'
            )

        col = getattr(MLFindingLabel, key)
        if val.lower() == 'null':
            query = query.filter(col.is_(None))
        elif key == 'is_real_finding':
            if val.lower() in ('true', '1', 'yes'):
                query = query.filter(col.is_(True))
            elif val.lower() in ('false', '0', 'no'):
                query = query.filter(col.is_(False))
            else:
                raise ValueError(f'is_real_finding must be true/false, got "{val}"')
        else:
            query = query.filter(col == val)

    return query


class RelabelerV1(BaseBatchLabeler):
    """Batch-API-based Claude relabeler for existing ml_finding_labels rows.

    Two modes:
      (a) Full re-label: no filter, walks all rows where category_v2 IS NULL.
          Original behavior, ~$7 for 60K rows.
      (b) Targeted re-label: filter expression in config, walks matching rows
          regardless of whether they have v2 labels. Used for iterating on
          prompt quality without re-labeling everything.

    Prompt versions:
      v1 — original prompt (Oct 2025 re-label)
      v2 — revised prompt (discourages 'general' catch-all, sets severity
           distribution expectations)
    """

    JOB_TYPE = 'relabel'
    SOURCE_NAME = 'relabel_v1'
    BATCH_SIZE = 20
    MODEL = 'claude-haiku-4-5'

    def __init__(self, config: Optional[dict] = None):
        super().__init__(config)
        # Offset cursor: walks through ml_finding_labels in id order so
        # repeated get_batch() calls in _collect_all_requests() return
        # consecutive chunks, not the same chunk over and over.
        self._last_id_seen = 0
        self._filter_str = (config or {}).get('filter', '').strip()
        # v5.86.88: prompt version. Default v1 for backward compatibility;
        # targeted re-runs typically specify v2.
        self._prompt_version = (config or {}).get('prompt_version', 'v1')
        if self._prompt_version not in ('v1', 'v2'):
            self._prompt_version = 'v1'

    def get_batch(self) -> list[dict]:
        """Fetch the next batch of rows, ordered by id ascending.

        When filter is set: returns rows matching the filter (ignoring v2-labeled
        status so we can re-label already-labeled rows with a better prompt).
        When filter is unset: returns rows where category_v2 IS NULL (legacy
        behavior — labels only unlabeled rows).
        """
        from models import MLFindingLabel

        q = MLFindingLabel.query

        if self._filter_str:
            # Targeted re-label: use the filter, ignore v2-null requirement
            q = apply_filter_expression(q, self._filter_str)
        else:
            # Full re-label: only unlabeled rows
            q = q.filter(MLFindingLabel.category_v2.is_(None))

        rows = (q.filter(MLFindingLabel.id > self._last_id_seen)
                 .order_by(MLFindingLabel.id.asc())
                 .limit(self.BATCH_SIZE)
                 .all())

        if not rows:
            return []

        self._last_id_seen = rows[-1].id
        return [{'id': r.id, 'text': r.finding_text} for r in rows]

    def build_prompt(self, batch: list[dict]) -> str:
        """Build the relabel prompt for this batch.

        Only sends id + text to Claude — no existing category/severity, so
        Claude labels fresh without being anchored to potentially-wrong
        existing labels. Chooses v1 or v2 template based on self._prompt_version.
        """
        findings = [{'id': item['id'], 'text': (item['text'] or '')[:500]} for item in batch]
        template = RELABEL_PROMPT_V2_TEMPLATE if self._prompt_version == 'v2' else RELABEL_PROMPT_TEMPLATE
        return template.format(
            findings_json=json.dumps(findings, ensure_ascii=False)
        )

    # parse_response() uses inherited default — expects a JSON array response

    def save_result(self, item_id: int, result: dict) -> None:
        """Write Claude's v2 labels back to the row."""
        # Validate Claude's response before writing — bad values go to NULL
        # rather than polluting the v2 columns with garbage.
        cat = (result.get('category_v2') or '').lower().strip()
        if cat not in VALID_CATEGORIES:
            cat = None

        sev = (result.get('severity_v2') or '').lower().strip()
        if sev not in VALID_SEVERITIES:
            sev = None

        is_real = result.get('is_real_finding')
        if not isinstance(is_real, bool):
            is_real = None

        confidence = result.get('confidence')
        if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
            confidence = None
        else:
            confidence = float(confidence)

        notes = (result.get('notes') or '')[:500] if result.get('notes') else None

        # If Claude couldn't classify at all, skip this row (will retry on next run)
        if cat is None and sev is None and is_real is None:
            self._rows_rejected += 1
            return

        self._update_labels(
            row_id=item_id,
            category_v2=cat,
            severity_v2=sev,
            is_real_finding=is_real,
            labeling_confidence=confidence,
            labeling_notes=notes,
        )

