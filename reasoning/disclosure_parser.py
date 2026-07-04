"""
Format-general disclosure entry point (the disclosure half of the moat).

Mirrors reasoning/inspection_parser.extract_inspection_readings for the seller
side. The single place that answers "given a disclosure packet's text, what did
the seller say?" — so the buyer path and the shadow can share one implementation.

Strategy (identical shape to the inspection dispatcher):
  1. Deterministic first: the California TDS field-state extractor is precise and
     free (no LLM), so use it when the packet is a recognizable, complete TDS.
  2. Format-general fallback: on any other packet — a Texas TREC notice, a
     Washington Form 17, a partial/scanned TDS, or anything the deterministic
     path can't structure — fall back to the LLM disclosure extractor, which
     reads ANY state's disclosure into the checklist vocabulary.

Before this module, only step 1 was wired into the buyer path, so the entire
disclosure side of the cross-reference — the "seller answered clean / said
nothing, and the inspection found Y" sentence that IS the product — worked only
for California TDS forms. The generic extractor existed but ran only in the
shadow, where no buyer saw it. This closes that gap.

Returns {'readings': [...], 'method': 'tds' | 'llm' | 'empty'}. Never raises.
The readings are disclosure field_readings the pipeline consumes on the seller
side (map_field_to_claim tags them disclosure-sourced, which is what yields
corroborated / contradiction / undisclosed against the inspection findings).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def extract_disclosure_readings(
    text: str,
    *,
    checklist_ids: Optional[List[str]] = None,
    allow_llm: bool = True,
    llm_client: Any = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {"readings": [], "method": "empty"}
    if not text:
        return result

    # 1) Deterministic California-TDS path (precise, no LLM cost). Returns None
    #    for anything that isn't a recognizable/complete TDS, in which case we
    #    fall through to the format-general path — no CA assumption survives.
    try:
        from pdf_handler import extract_tds_field_state
        from .tds_parser import parse_tds_field_state
        field_state, _conf, _notes = extract_tds_field_state(text)
        if field_state is not None:
            readings = parse_tds_field_state(field_state)
            if readings:
                result["readings"] = readings
                result["method"] = "tds"
                return result
    except Exception:
        pass

    # 2) Format-general LLM path — reads ANY state's disclosure into the
    #    checklist vocabulary. Offered the FULL authored id universe when no
    #    explicit checklist is given (extraction precedes jurisdiction
    #    resolution; the pipeline gates down to the resolved checklist).
    if allow_llm:
        try:
            from .disclosure_llm_extractor import extract_disclosure_findings_llm
            if checklist_ids is None:
                from .composition import all_authored_ids
                checklist_ids = all_authored_ids()
            llm = extract_disclosure_findings_llm(
                text, checklist_ids, client=llm_client
            )
            if llm:
                result["readings"] = llm
                result["method"] = "llm"
                return result
        except Exception:
            pass

    return result
