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

Scope: the shadow now runs BOTH sides of the cross-reference — the inspection
LLM extractor and the disclosure LLM extractor — so disclosure_status
(corroborated / contradiction / undisclosed) is a real signal, not 'undisclosed'
by construction. Each extractor fails independently and non-fatally.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

def _infer_jurisdiction(address: Optional[str]) -> str:
    """Resolve the state from the address for checklist composition. Delegates to
    the shared jurisdiction_resolver so there is ONE inference path in the tree,
    not three. On failure it returns the NATIONAL base ('*') — never a guessed
    state — so a non-CA or unparseable address is never scored against CA's
    overlays.
    compose() applies a state overlay only where one is authored (today: CA);
    every other state correctly resolves to the national base."""
    try:
        from jurisdiction_resolver import resolve_state
        return resolve_state(address=address)
    except Exception:
        return "*"  # national base, not a guessed state


def build_comparison(live_cross_ref: Any, reasoning_issues: list,
                     reasoning_offer: Any, extractor_readings: int,
                     extractor_ok: bool, disclosure_readings: int = 0) -> dict:
    """Pure: summarize what each engine surfaced. No side effects."""
    live_contra = len(getattr(live_cross_ref, "contradictions", []) or [])
    live_undis = len(getattr(live_cross_ref, "undisclosed_issues", []) or [])

    issues = reasoning_issues or []
    silent = sum(1 for i in issues
                 if getattr(i, "silent_hazard_flag", False)
                 or getattr(i, "decision_class", "") == "silent_hazard")

    def _dstat(name):
        return sum(1 for i in issues if getattr(i, "disclosure_status", "") == name)
    corroborated = _dstat("corroborated")
    contradiction = _dstat("contradiction")
    undis = _dstat("undisclosed")
    offer_low = int(getattr(reasoning_offer, "price_adjustment_low", 0) or 0) if reasoning_offer else 0
    offer_high = int(getattr(reasoning_offer, "price_adjustment_high", 0) or 0) if reasoning_offer else 0

    notes = (f"live: {live_contra} contradictions + {live_undis} undisclosed | "
             f"reasoning: {len(issues)} issues ({silent} silent hazards; disclosure "
             f"{corroborated} corroborated / {contradiction} contradiction / {undis} undisclosed) "
             f"from {extractor_readings} insp + {disclosure_readings} disc readings"
             f"{'' if extractor_ok else ' [EXTRACTOR FAILED]'}")

    return {
        "live_contradictions": live_contra,
        "live_undisclosed": live_undis,
        "extractor_ok": extractor_ok,
        "extractor_readings": extractor_readings,
        "disclosure_readings": disclosure_readings,
        "reasoning_issues": len(issues),
        "reasoning_silent_hazards": silent,
        "reasoning_corroborated": corroborated,
        "reasoning_contradiction": contradiction,
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
    disc_readings = []
    try:
        from reasoning import run_pipeline
        from reasoning.inspection_llm_extractor import extract_inspection_findings_llm
        from reasoning.disclosure_llm_extractor import extract_disclosure_findings_llm

        # Resolve the full jurisdiction path (municipal depth where authored) via
        # the shared resolver, and offer the extractors the FULL authored id
        # universe — identical to the buyer path. The pipeline gates readings down
        # to the resolved checklist, so no id any state/type needs is withheld.
        from reasoning.composition import all_authored_ids
        try:
            from jurisdiction_resolver import resolve_jurisdiction_path
            jurisdiction = resolve_jurisdiction_path(address=property_address)
        except Exception:
            jurisdiction = _infer_jurisdiction(property_address)
        ids = all_authored_ids()

        try:
            readings = extract_inspection_findings_llm(
                inspection_text or "", ids, client=ai_client) or []
            extractor_ok = True
        except Exception as e:
            logger.warning(f"[SHADOW] inspection extractor failed: {e}")
            readings = []
            extractor_ok = False

        # Disclosure side (the other half of the cross-reference). Its failure is
        # non-fatal and independent: without it, items fall back to 'undisclosed'.
        try:
            disc_readings = extract_disclosure_findings_llm(
                disclosure_text or "", ids, client=ai_client) or []
        except Exception as e:
            logger.warning(f"[SHADOW] disclosure extractor failed: {e}")
            disc_readings = []

        result = run_pipeline(
            field_readings=disc_readings, jurisdiction=jurisdiction, property_type=property_type,
            inspection_readings=readings, persist=False,
        )
        issues = result.issues_result.issues if result.issues_result else []
        offer = result.issues_result.offer if result.issues_result else None

        comp = build_comparison(live_cross_ref, issues, offer, len(readings),
                                extractor_ok, disclosure_readings=len(disc_readings))
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
            "disclosure_readings": len(disc_readings),
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
            disclosure_readings=comp.get("disclosure_readings"),
            reasoning_issues=comp.get("reasoning_issues"),
            reasoning_silent_hazards=comp.get("reasoning_silent_hazards"),
            reasoning_corroborated=comp.get("reasoning_corroborated"),
            reasoning_contradiction=comp.get("reasoning_contradiction"),
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
    return _summarize_rows(rows)


def _summarize_rows(rows) -> dict:
    """Pure aggregation over ShadowComparison-like rows (DB-free, unit-testable).
    Produces the global rollup plus the per-jurisdiction readiness readout that
    gates the per-state activation allowlist."""
    n = len(rows)
    if not n:
        return {"count": 0}
    ok = sum(1 for r in rows if r.ok)
    extr_ok = sum(1 for r in rows if r.extractor_ok)
    reasoning_more = sum(1 for r in rows if (r.reasoning_issues or 0)
                         > ((r.live_contradictions or 0) + (r.live_undisclosed or 0)))
    disc_any = sum(1 for r in rows if (r.disclosure_readings or 0) > 0)

    # Per-jurisdiction readiness — the readout that gates the per-state activation
    # allowlist (reasoning_in_report_jurisdictions). A state clears the bar when it
    # has enough real samples AND the extractor fires AND the disclosure side is
    # actually producing readings AND reasoning surfaces at least as much as the
    # live engine. Thresholds are deliberately conservative and tunable — they
    # answer "is it safe to turn the moat on for buyers in this state yet?".
    READY_MIN_SAMPLES = 10
    READY_EXTRACTOR = 0.80
    READY_DISCLOSURE = 0.50
    READY_MORE = 0.50

    def _state(r):
        j = (r.jurisdiction or "").split(":")[0].strip().upper()
        return j or "?"

    by_juris = {}
    for r in rows:
        st = _state(r)
        b = by_juris.setdefault(st, {"count": 0, "_extr": 0, "_disc": 0, "_more": 0})
        b["count"] += 1
        b["_extr"] += 1 if r.extractor_ok else 0
        b["_disc"] += 1 if (r.disclosure_readings or 0) > 0 else 0
        b["_more"] += 1 if (r.reasoning_issues or 0) > ((r.live_contradictions or 0)
                                                        + (r.live_undisclosed or 0)) else 0
    for st, b in by_juris.items():
        c = b["count"]
        b["extractor_ok_rate"] = round(b.pop("_extr") / c, 3)
        b["disclosure_extracted_rate"] = round(b.pop("_disc") / c, 3)
        b["reasoning_surfaced_more_rate"] = round(b.pop("_more") / c, 3)
        b["ready"] = bool(
            c >= READY_MIN_SAMPLES
            and b["extractor_ok_rate"] >= READY_EXTRACTOR
            and b["disclosure_extracted_rate"] >= READY_DISCLOSURE
            and b["reasoning_surfaced_more_rate"] >= READY_MORE
        )
        b["verdict"] = (
            "READY — clears the bar; safe to add to the activation allowlist"
            if b["ready"] else
            (f"needs samples ({c}/{READY_MIN_SAMPLES})" if c < READY_MIN_SAMPLES else
             "below threshold — keep validating")
        )

    return {
        "count": n,
        "ran_ok": ok,
        "extractor_ok": extr_ok,
        "extractor_ok_rate": round(extr_ok / n, 3),
        "disclosure_extracted_rate": round(disc_any / n, 3),
        "reasoning_surfaced_more_rate": round(reasoning_more / n, 3),
        "avg_reasoning_issues": round(sum((r.reasoning_issues or 0) for r in rows) / n, 1),
        "avg_corroborated": round(sum((r.reasoning_corroborated or 0) for r in rows) / n, 2),
        "avg_contradiction": round(sum((r.reasoning_contradiction or 0) for r in rows) / n, 2),
        "avg_undisclosed": round(sum((r.reasoning_undisclosed or 0) for r in rows) / n, 2),
        "avg_elapsed_ms": round(sum((r.elapsed_ms or 0) for r in rows) / n),
        "readiness_thresholds": {
            "min_samples": READY_MIN_SAMPLES, "extractor_ok_rate": READY_EXTRACTOR,
            "disclosure_extracted_rate": READY_DISCLOSURE,
            "reasoning_surfaced_more_rate": READY_MORE,
        },
        "by_jurisdiction": dict(sorted(by_juris.items(),
                                       key=lambda kv: (-kv[1]["count"], kv[0]))),
    }
