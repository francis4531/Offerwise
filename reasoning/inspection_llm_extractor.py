"""
LLM-based inspection extractor — format-general (the scalable moat).

The WINspect keyword parser (reasoning/inspection_parser.py) only works on reports
that use a structured rating vocabulary. Real inspection reports come in hundreds
of vendor formats — many are free-text narrative (numbered prose findings, no
rating column). This module reads ANY report's text and maps its findings into the
checklist's controlled vocabulary using a single LLM call.

Reuses the project's existing Anthropic client + the proven call/parse pattern
from permit_lookup (no new LLM infrastructure). Output is constrained to real
checklist item ids; anything the model can't map is dropped rather than invented.

Design: this is a FALLBACK, not a replacement. The cheap deterministic keyword
parser runs first; this LLM path runs only when that comes up (near-)empty, so we
don't pay for a call on formats the keyword parser already handles.
"""
from __future__ import annotations
from model_config import HAIKU

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

INSPECTION_EXTRACT_MODEL = os.environ.get(
    "INSPECTION_EXTRACT_MODEL", HAIKU
)

# Silent hazards: latent risks a seller disclosure structurally cannot reveal.
# Used to set the silent_hazard flag deterministically from the mapped item id,
# rather than trusting the model to label it.
_SILENT_HAZARD_ITEMS = {
    "electrical.panel_brand_safety",
    "electrical.wiring_material",
    "hvac.flue_venting_integrity",
    "environmental.asbestos_risk",
    "environmental.lead_paint_risk",
    "environmental.mold_risk",
    "plumbing.known_defect_pipe_material",
}

_SEVERITY_RANK = {"clean": 0, "minor": 1, "moderate": 2, "major": 3, "critical": 4}


def _build_prompt(checklist_ids: List[str], report_text: str) -> str:
    ids_block = "\n".join(f"  - {i}" for i in checklist_ids)
    # cap the report text to keep the call bounded; findings cluster early/mid
    text = report_text[:60000]
    return f"""You are extracting structured findings from a home inspection report. The \
report may be in ANY vendor's format (narrative prose, rating tables, numbered \
sections — it varies). Read the report and map each genuine finding to the SINGLE \
best-matching checklist item id from the controlled list below.

CONTROLLED CHECKLIST ITEM IDS (map ONLY to these — never invent an id):
{ids_block}

For each finding you can confidently map, output an object with:
  - item_id: exact id from the list above
  - value: "yes" if the report indicates a concern/defect/deficiency for this item,
    "no" if the report explicitly checked it and found it acceptable/satisfactory.
  - severity: one of "minor","moderate","major","critical" (for value "yes"), or
    "clean" (for value "no"). Judge from the report's language and the real-world
    risk: active leaks, fire hazards, combustion/CO risks, structural movement,
    and failed safety devices are major/critical; cosmetic or routine-maintenance
    items are minor/moderate.
  - evidence: a SHORT quote or paraphrase (<=15 words) from the report supporting it.

Rules:
- Map only findings the report actually supports. Do not infer issues not stated.
- One object per checklist item (the strongest finding for that item). If the
  report doesn't address an item, omit it entirely.
- Prefer specificity: a Federal Pacific/Stab-Lok panel -> electrical.panel_brand_safety;
  aluminum branch wiring -> electrical.wiring_material; a cracked/leaking flue or
  CO risk -> hvac.flue_venting_integrity; pre-1978 asbestos/lead -> the
  environmental.* items; an active water leak -> plumbing.active_leaks; kitchen
  water evidence (floor warping at the fridge/sink, cabinet/subfloor staining) ->
  structure.water_intrusion_kitchen; bath/shower water (shower-pan/tub/fixture
  leaks, moisture-damaged sills) -> structure.water_intrusion_bath.
- Be honest: if uncertain, omit rather than guess.

Respond with ONLY a JSON array of these objects. No preamble, no markdown fences.

REPORT TEXT:
{text}
"""


def _strip_fences(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        # ```json ... ``` or ``` ... ```
        inner = raw[3:]
        if inner.lower().startswith("json"):
            inner = inner[4:]
        if "```" in inner:
            inner = inner[: inner.rfind("```")]
        raw = inner.strip()
    return raw


def extract_inspection_findings_llm(
    report_text: str,
    checklist_ids: List[str],
    *,
    client: Any = None,
    model: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Read any-format inspection text -> checklist-keyed readings via one LLM call.

    Returns the same reading shape the pipeline consumes:
      {item_id, value, severity, silent_hazard, source_form, locator, raw_text}
    Never raises — returns [] on any failure (caller falls back to whatever the
    keyword parser produced).
    """
    if not report_text or not checklist_ids:
        return []

    if client is None:
        try:
            # reuse the project's existing client wrapper (same as permit_lookup)
            from analysis_ai_helper import get_anthropic_client
            client = get_anthropic_client()
        except Exception as e:
            logger.warning("inspection LLM extract: no client (%s)", e)
            return []
    if not client:
        return []

    allowed = set(checklist_ids)
    prompt = _build_prompt(checklist_ids, report_text)

    # Robust structured-output handling (ai_json): a 44-page report easily
    # overran the old max_tokens=4000 -> truncated array -> json.loads failed ->
    # this returned [] and the pipeline silently lost the inspection moat. Now:
    # higher budget + retry on truncation + salvage, and parse failure is
    # surfaced + instrumented (endpoint 'inspection-extract') rather than
    # masquerading as "no findings".
    from ai_json import call_ai_json
    parsed = call_ai_json(
        prompt,
        max_tokens=8000,
        temperature=0,
        model=model or INSPECTION_EXTRACT_MODEL,
        ai_client=client,
        endpoint='inspection-extract',
        retry_on_truncation=True,
        max_tokens_ceiling=16000,
    )
    if not parsed.ok or not isinstance(parsed.data, list):
        logger.warning(
            "inspection LLM extract: unparseable or not a list "
            "(stop_reason=%s truncated=%s chars=%s err=%s)",
            parsed.stop_reason, parsed.truncated, parsed.output_chars, parsed.error,
        )
        return []
    data = parsed.data

    readings: List[Dict[str, Any]] = []
    seen: Dict[str, Dict[str, Any]] = {}
    for obj in data:
        if not isinstance(obj, dict):
            continue
        iid = (obj.get("item_id") or "").strip()
        if iid not in allowed:
            continue  # never accept an id outside the controlled vocabulary
        value = "yes" if str(obj.get("value", "")).lower() == "yes" else "no"
        sev = str(obj.get("severity", "")).lower()
        if value == "no":
            sev = "clean"
        elif sev not in _SEVERITY_RANK or sev == "clean":
            sev = "moderate"  # default a concern to moderate if model omitted it
        reading = {
            "item_id": iid,
            "value": value,
            "severity": sev,
            "silent_hazard": (value == "yes" and iid in _SILENT_HAZARD_ITEMS),
            "source_form": "INSPECTION_LLM",
            "locator": f"INSPECTION_LLM/{iid}",
            "raw_text": str(obj.get("evidence", iid))[:300],
            "corroborated_in_summary": False,
        }
        # keep the worst reading per item
        prev = seen.get(iid)
        if prev is None or _SEVERITY_RANK.get(sev, 0) > _SEVERITY_RANK.get(prev["severity"], 0):
            seen[iid] = reading
    return list(seen.values())
