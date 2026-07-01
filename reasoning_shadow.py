"""
reasoning_shadow.py — shadow-run the reasoning/ engine alongside the live
keyword engine on real analyses, to measure (at scale, on real traffic) whether
the reasoning engine surfaces what the live engine does, and more.

This is Layer A validation in production: it runs the LLM inspection extractor on
the real inspection text, then the reasoning pipeline, and records a
ShadowComparison row. It is:

  - OFF by default (flag OFFERWISE_REASONING_SHADOW), enabled per environment.
  - INCAPABLE of affecting the user-facing result — the caller invokes it after
    the live cross-reference and ignores its return; every path is swallowed.

Scope note: the TDS parser has no raw-text -> readings path, so the shadow runs
the INSPECTION side of reasoning (field_readings=[]). That exercises the core
moat (LLM extraction + issue derivation). The disclosure-side shadow needs a
disclosure extractor — a separate build — so disclosure_status here will read
'undisclosed' by construction and is not yet a fair moat signal.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

_STATE_RE = re.compile(r",\s*([A-Z]{2})\s*\d{5}|,\s*([A-Z]{2})\b")


def _infer_jurisdiction(address: Optional[str]) -> str:
    if address:
        m = _STATE_RE.search(address)
        if m:
            return (m.group(1) or m.group(2) or "CA").upper()
    return "CA"  # launch-market default


def build_comparison(live_cross_ref: Any, reasoning_issues: list,
                     reasoning_offer: Any, extractor_readings: int,
                     extractor_ok: bool) -> dict:
    """Pure: summarize what each engine surfaced. No side effects."""
    live_contra = len(getattr(live_cross_ref, "contradictions", []) or [])
    live_undis = len(getattr(live_cross_ref, "undisclosed_issues", []) or [])

    issues = reasoning_issues or []
    silent = sum(1 for i in issues
                 if getattr(i, "silent_hazard_flag", False)
                 or getattr(i, "decision_class", "") == "silent_hazard")
    undis = sum(1 for i in issues
                if getattr(i, "disclosure_status", "") in ("undisclosed", "contradiction"))
    offer_low = int(getattr(reasoning_offer, "price_adjustment_low", 0) or 0) if reasoning_offer else 0
    offer_high = int(getattr(reasoning_offer, "price_adjustment_high", 0) or 0) if reasoning_offer else 0

    notes = (f"live: {live_contra} contradictions + {live_undis} undisclosed | "
             f"reasoning: {len(issues)} issues ({silent} silent hazards, "
             f"{undis} undisclosed/contradiction) from {extractor_readings} readings"
             f"{'' if extractor_ok else ' [EXTRACTOR FAILED]'}")

    return {
        "live_contradictions": live_contra,
        "live_undisclosed": live_undis,
        "extractor_ok": extractor_ok,
        "extractor_readings": extractor_readings,
        "reasoning_issues": len(issues),
        "reasoning_silent_hazards": silent,
        "reasoning_undisclosed": undis,
        "reasoning_offer_low": offer_low,
        "reasoning_offer_high": offer_high,
        "notes": notes,
    }


def run_reasoning_shadow(*, inspection_text: str, disclosure_text: str = "",
                         property_address: Optional[str] = None,
                         property_price: float = 0,
                         live_cross_ref: Any = None,
                         analysis_id: Optional[int] = None,
                         property_type: str = "SFH",
                         ai_client: Any = None) -> Optional[dict]:
    """Run the reasoning engine in shadow and persist a comparison. NEVER raises;
    returns the comparison dict (or None). The caller must ignore the result and
    keep serving the live analysis."""
    t0 = time.time()
    comp = None
    extractor_ok = False
    readings = []
    try:
        from reasoning import compose, run_pipeline
        from reasoning.inspection_llm_extractor import extract_inspection_findings_llm

        jurisdiction = _infer_jurisdiction(property_address)
        ids = sorted(compose(jurisdiction, property_type).ids())

        try:
            readings = extract_inspection_findings_llm(
                inspection_text or "", ids, client=ai_client) or []
            extractor_ok = True
        except Exception as e:
            logger.warning(f"[SHADOW] extractor failed: {e}")
            readings = []
            extractor_ok = False

        result = run_pipeline(
            field_readings=[], jurisdiction=jurisdiction, property_type=property_type,
            inspection_readings=readings, persist=False,
        )
        issues = result.issues_result.issues if result.issues_result else []
        offer = result.issues_result.offer if result.issues_result else None

        comp = build_comparison(live_cross_ref, issues, offer, len(readings), extractor_ok)
        comp.update({
            "analysis_id": analysis_id,
            "jurisdiction": jurisdiction,
            "property_type": property_type,
            "ok": True,
            "error": None,
            "elapsed_ms": int((time.time() - t0) * 1000),
        })
        logger.info(f"[SHADOW] {comp['notes']} ({comp['elapsed_ms']}ms)")
    except Exception as e:
        logger.warning(f"[SHADOW] reasoning shadow failed (non-fatal): {e}")
        comp = {
            "analysis_id": analysis_id, "ok": False, "error": str(e)[:300],
            "extractor_ok": extractor_ok, "extractor_readings": len(readings),
            "elapsed_ms": int((time.time() - t0) * 1000),
        }

    _persist(comp)
    return comp


def _persist(comp: Optional[dict]) -> int:
    """Write a ShadowComparison row inside a Flask app context. Never raises."""
    if not comp:
        return 0
    try:
        from flask import has_app_context
        if not has_app_context():
            return 0
    except Exception:
        return 0
    try:
        from models import db, ShadowComparison
    except Exception:
        return 0
    try:
        row = ShadowComparison(
            analysis_id=comp.get("analysis_id"),
            jurisdiction=(comp.get("jurisdiction") or "")[:8] or None,
            property_type=(comp.get("property_type") or "")[:8] or None,
            live_contradictions=comp.get("live_contradictions"),
            live_undisclosed=comp.get("live_undisclosed"),
            extractor_ok=bool(comp.get("extractor_ok")),
            extractor_readings=comp.get("extractor_readings"),
            reasoning_issues=comp.get("reasoning_issues"),
            reasoning_silent_hazards=comp.get("reasoning_silent_hazards"),
            reasoning_undisclosed=comp.get("reasoning_undisclosed"),
            reasoning_offer_low=comp.get("reasoning_offer_low"),
            reasoning_offer_high=comp.get("reasoning_offer_high"),
            ok=bool(comp.get("ok")),
            error=(comp.get("error") or None),
            notes=(comp.get("notes") or None),
            elapsed_ms=comp.get("elapsed_ms"),
        )
        db.session.add(row)
        db.session.commit()
        return 1
    except Exception as e:
        logger.warning(f"[SHADOW] persist failed (non-fatal): {e}")
        try:
            from models import db
            db.session.rollback()
        except Exception:
            pass
        return 0


def shadow_summary(window_days: int = 30) -> dict:
    """Read side for an admin view: aggregate shadow outcomes."""
    try:
        from models import db, ShadowComparison
        from datetime import datetime, timedelta
    except Exception:
        return {}
    try:
        q = db.session.query(ShadowComparison)
        if window_days:
            q = q.filter(ShadowComparison.created_at >= datetime.utcnow() - timedelta(days=window_days))
        rows = q.all()
    except Exception:
        return {}
    n = len(rows)
    if not n:
        return {"count": 0}
    ok = sum(1 for r in rows if r.ok)
    extr_ok = sum(1 for r in rows if r.extractor_ok)
    reasoning_more = sum(1 for r in rows if (r.reasoning_issues or 0)
                         > ((r.live_contradictions or 0) + (r.live_undisclosed or 0)))
    return {
        "count": n,
        "ran_ok": ok,
        "extractor_ok": extr_ok,
        "extractor_ok_rate": round(extr_ok / n, 3),
        "reasoning_surfaced_more_rate": round(reasoning_more / n, 3),
        "avg_reasoning_issues": round(sum((r.reasoning_issues or 0) for r in rows) / n, 1),
        "avg_elapsed_ms": round(sum((r.elapsed_ms or 0) for r in rows) / n),
    }
