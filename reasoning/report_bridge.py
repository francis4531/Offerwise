"""
Phase 1d — attach the reasoning layer's output to the buyer analysis result.

This is the first place the checklist-driven reasoning touches the live buyer
path. It is GATED OFF by default (env OFFERWISE_REASONING_IN_REPORT=1 to enable)
and purely ADDITIVE: it adds a 'reasoning' key to result_dict and never modifies
or removes existing fields. It is defensive — any failure leaves the analysis
response exactly as it was.

Input: the analysis already knows the property's jurisdiction/type and may carry
parsed TDS field state. When real field state is present it is used; otherwise no
reasoning section is attached (we do not fabricate Claims).
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional


def reasoning_in_report_enabled() -> bool:
    """Buyer-facing reasoning section. OFF by default.

    Resolution order (first decisive wins):
      1. DB setting 'reasoning_in_report' (admin toggle) — '1'/'on'/'true' = ON,
         '0'/'off'/'false' = OFF. Lets an admin flip it from the dashboard with
         no deploy.
      2. Env var OFFERWISE_REASONING_IN_REPORT == '1'.
    Fully defensive: any DB/context error falls through to the env var, so the
    analysis path never breaks on a settings lookup.
    """
    try:
        from models import SystemSetting
        v = SystemSetting.get("reasoning_in_report", None)
        if v is not None:
            return str(v).strip().lower() in ("1", "on", "true", "yes")
    except Exception:
        pass
    return os.environ.get("OFFERWISE_REASONING_IN_REPORT", "0") == "1"


def reasoning_persist_enabled() -> bool:
    """Separate from the buyer-facing flag: persistence is an ops decision and is
    fully guarded, so it can be turned on without exposing anything to buyers.

    Defaults ON (writes the structured reasoning corpus). Set
    OFFERWISE_REASONING_PERSIST=0 to disable. This default is independent of the
    buyer-facing OFFERWISE_REASONING_IN_REPORT flag, which stays OFF by default."""
    return os.environ.get("OFFERWISE_REASONING_PERSIST", "1") == "1"


def build_reasoning_section(
    *,
    jurisdiction: str = "*",   # national base by default; caller resolves the real one
    property_type: str = "SFH",  # broad floor; caller resolves the real one
    tds_field_state: Optional[Dict[str, Any]] = None,
    inspection_readings: Optional[list] = None,
    disclosure_readings: Optional[list] = None,
    zip_code: str = "",
    property_year_built: Optional[int] = None,
    analysis_id: Optional[int] = None,
    property_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Build the reasoning section from disclosure field readings and/or inspection
    readings. Returns None if there is nothing real to reason over (we never
    fabricate Claims for the buyer).

    Disclosure side: prefer pre-built disclosure_readings (format-general,
    produced by reasoning.disclosure_parser for ANY state's disclosure). Fall
    back to parsing a California tds_field_state only when no readings were
    supplied — so a non-CA disclosure is no longer silently dropped.

    Persistence (writing Finding/Claim/Issue rows) is controlled by the SEPARATE
    OFFERWISE_REASONING_PERSIST flag and is fully guarded: a DB write failure can
    never affect the returned section or the buyer response.
    """
    if not tds_field_state and not inspection_readings and not disclosure_readings:
        return None
    try:
        from reasoning.tds_parser import parse_tds_field_state
        from reasoning import run_pipeline

        # Disclosure side: prefer the format-general readings; fall back to
        # parsing a CA TDS field state only when none were supplied.
        if disclosure_readings:
            readings = list(disclosure_readings)
        elif tds_field_state:
            readings = parse_tds_field_state(tds_field_state)
        else:
            readings = []
        if not readings and not inspection_readings:
            return None
        # Persist only when its own flag is on; isolate write failures so they
        # never break the section build (fall back to a pure run).
        want_persist = reasoning_persist_enabled()
        try:
            result = run_pipeline(readings, jurisdiction, property_type,
                                  inspection_readings=inspection_readings,
                                  zip_code=zip_code, property_year_built=property_year_built,
                                  persist=want_persist,
                                  analysis_id=analysis_id, property_id=property_id)
        except Exception:
            # persistence (or anything in the persisted path) failed -> retry pure
            result = run_pipeline(readings, jurisdiction, property_type,
                                  inspection_readings=inspection_readings,
                                  zip_code=zip_code, property_year_built=property_year_built,
                                  persist=False)
        issues = result.issues_result.issues if result.issues_result else []
        offer = result.issues_result.offer if result.issues_result else None

        return {
            "checklist_version": result.checklist_version,
            "resolved_items": result.resolved_item_count,
            "claims": [
                {
                    "item": c.checklist_item_id,
                    "value": c.resolved_value,
                    "polarity": c.polarity,
                    "evidence_quality": c.evidence_quality_confidence,
                    "source": c.source_locator,
                }
                for c in result.claims
            ],
            "issues": [
                {
                    "decision_class": i.decision_class,
                    "title": i.title,
                    "severity": i.severity,
                    "silent_hazard": i.silent_hazard_flag,
                    "is_reserve": i.is_reserve,
                    "items": i.claim_item_ids,
                    "cost_low": round(i.cost_band_low) if i.cost_band_low else None,
                    "cost_high": round(i.cost_band_high) if i.cost_band_high else None,
                }
                for i in issues
            ],
            "offer": {
                "price_adjustment_basis": offer.price_adjustment_issue_titles if offer else [],
                "price_adjustment_low": round(offer.price_adjustment_low) if offer else 0,
                "price_adjustment_high": round(offer.price_adjustment_high) if offer else 0,
                "buyer_held_reserve": offer.reserve_issue_titles if offer else [],
                "pre_close_actions": offer.pre_close_action_titles if offer else [],
                "silent_hazards": offer.silent_hazard_titles if offer else [],
            },
            "disclaimer": (
                "Preview: checklist-driven reasoning (deterministic disclosure path). "
                "A clean disclosure is not a clean property — items the form does not "
                "ask about are covered by inspection-side analysis."
            ),
        }
    except Exception:
        return None


def attach_reasoning_if_enabled(
    result_dict: Dict[str, Any],
    *,
    jurisdiction: str = "*",   # national base by default; caller resolves the real one
    property_type: str = "SFH",  # broad floor; caller resolves the real one
    tds_field_state: Optional[Dict[str, Any]] = None,
    inspection_readings: Optional[list] = None,
    disclosure_readings: Optional[list] = None,
    zip_code: str = "",
    property_year_built: Optional[int] = None,
    analysis_id: Optional[int] = None,
    property_id: Optional[int] = None,
) -> None:
    """Attach result_dict['reasoning'] when the buyer flag is on; independently
    persist the reasoning corpus when the persist flag is on. The two are
    decoupled: persistence can run with the buyer flag OFF (nothing is exposed),
    and a persist failure never blocks the buyer section. Never raises."""
    want_buyer = reasoning_in_report_enabled()
    want_persist = reasoning_persist_enabled()
    if not want_buyer and not want_persist:
        return
    try:
        section = build_reasoning_section(
            jurisdiction=jurisdiction,
            property_type=property_type,
            tds_field_state=tds_field_state,
            inspection_readings=inspection_readings,
            disclosure_readings=disclosure_readings,
            zip_code=zip_code,
            property_year_built=property_year_built,
            analysis_id=analysis_id,
            property_id=property_id,
        )
        # Only the buyer flag controls EXPOSURE. Persistence already happened
        # inside build_reasoning_section (gated by its own flag).
        if want_buyer and section is not None:
            result_dict["reasoning"] = section
    except Exception:
        pass
