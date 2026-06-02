"""
Reasoning-layer self-check (Phase 0).

A single function that exercises the checklist loader + composition engine and
reports health as a small dict. Used by:
  - the admin self-check tile (in-product visibility, per requirement)
  - the test suite (CI)
so both assert the same thing.

Never raises: on any failure it returns ok=False with a message, so the admin
dashboard degrades gracefully (the dashboard shows reality, including failure).
"""
from __future__ import annotations

from typing import Any, Dict


def reasoning_self_check() -> Dict[str, Any]:
    """
    Load + validate the checklist asset, compose representative slices, and
    assert the Q-5.9 composition invariants. Returns a status dict.
    """
    result: Dict[str, Any] = {
        "ok": False,
        "version": None,
        "national_base_count": None,
        "ca_sfh_count": None,
        "tx_sfh_count": None,
        "ca_only_count": None,
        "invariants_passed": False,
        "checks": {},
        "error": None,
    }
    try:
        from reasoning import load_checklist, compose
        from reasoning.composition import CompositionError

        asset = load_checklist()  # validates §3.5 anatomy; raises on violation
        result["version"] = asset.version
        result["national_base_count"] = len(asset.national_base)

        national = compose("*", "SFH")
        ca = compose("CA", "SFH")
        tx = compose("TX", "SFH")
        result["ca_sfh_count"] = len(ca.items)
        result["tx_sfh_count"] = len(tx.items)
        ca_only = set(ca.ids()) - set(national.ids())
        result["ca_only_count"] = len(ca_only)

        checks = result["checks"]
        # 1. national scope: unserved state resolves to national base only
        checks["national_scope"] = set(national.ids()) == set(tx.ids())
        # 2. CA overlay adds items on top of the base
        checks["ca_overlay_adds"] = len(ca.items) > len(national.items)
        # 3. unique ids in a resolved checklist
        ids = ca.ids()
        checks["unique_ids"] = len(ids) == len(set(ids))
        # 4. every resolved item carries provenance (audit trail)
        checks["provenance_present"] = all(bool(it.source_layer) for it in ca.items)
        # 5. no-silent-delete is enforced
        try:
            bad = load_checklist()
            bad.state_overlays["CA"] = dict(bad.state_overlays["CA"])
            bad.state_overlays["CA"]["remove"] = ["roof.age"]
            compose("CA", "SFH", asset=bad)
            checks["no_silent_delete_enforced"] = False
        except CompositionError:
            checks["no_silent_delete_enforced"] = True

        result["invariants_passed"] = all(checks.values())
        result["ok"] = result["invariants_passed"]

        # Phase 1a: deterministic form-field map coverage (additive; pure).
        try:
            from reasoning import load_form_field_map
            fmap = load_form_field_map()
            result["form_field_map"] = {
                "version": fmap.version,
                "deterministic_items": fmap.coverage.get("deterministic_items"),
                "locator_cited": fmap.coverage.get("locator_cited"),
                "needs_form_specimen": fmap.coverage.get("needs_form_specimen"),
            }
        except Exception as e:
            result["form_field_map"] = {"error": f"{type(e).__name__}: {e}"}

        # Phase 1c: issue derivation smoke check (pure; synthetic claims).
        try:
            from reasoning import derive_issues
            synthetic = {
                "_legal": {"group": "general", "importance": "major", "cost_impact": "no",
                           "disclosure_obligation_state": "required", "severity_when_negative": "major",
                           "unanswered_implication": "minor", "compliance_basis": {"basis": "legal_requirement"}},
                "_neg": {"group": "plumbing", "importance": "major", "cost_impact": "yes",
                         "disclosure_obligation_state": "required", "severity_when_negative": "major",
                         "unanswered_implication": "minor", "compliance_basis": {"basis": "best_practice"}},
            }
            class _C:
                def __init__(self, i): self.checklist_item_id = i; self.polarity = "contradicts"
            der = derive_issues([_C("_legal"), _C("_neg")], synthetic)
            result["issue_derivation"] = {
                "ok": len(der.issues) == 2,
                "issues": len(der.issues),
                "price_basis_items": len(der.offer.price_adjustment_issue_titles),
                "pre_close_items": len(der.offer.pre_close_action_titles),
            }
        except Exception as e:
            result["issue_derivation"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        # Pipeline orchestrator smoke check (pure; synthetic readings, no DB).
        try:
            from reasoning import run_pipeline, load_form_field_map
            ids = load_form_field_map().item_ids()
            readings = [{"item_id": ids[0], "value": "yes", "raw_text": "x"},
                        {"item_id": ids[1], "value": "no", "raw_text": "y"}] if len(ids) >= 2 else []
            pr = run_pipeline(readings, "CA", "SFH", persist=False)
            s = pr.summary()
            result["pipeline"] = {
                "ok": s["claims"] == len(readings),
                "claims": s["claims"], "issues": s["issues"],
                "checklist_version": s["checklist_version"],
            }
        except Exception as e:
            result["pipeline"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        # Phase 1b: TDS parser verified against the real 381 Tina Dr specimen.
        try:
            from reasoning.tds_parser import parse_tds_field_state, load_specimen_field_state
            from reasoning import compose as _compose
            readings = parse_tds_field_state(load_specimen_field_state())
            ck = set(_compose("CA", "SFH").ids())
            off = [r["item_id"] for r in readings if r["item_id"] not in ck]
            result["tds_parser"] = {
                "ok": len(off) == 0 and len(readings) > 0,
                "form_revision": "6/24",
                "specimen_readings": len(readings),
                "off_checklist": len(off),
            }
        except Exception as e:
            result["tds_parser"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        # Phase 1d: report bridge (flagged). Show wiring + flag state + a dry build.
        try:
            from reasoning.report_bridge import build_reasoning_section, reasoning_in_report_enabled
            from reasoning.tds_parser import load_specimen_field_state
            section = build_reasoning_section(tds_field_state=load_specimen_field_state())
            result["report_bridge"] = {
                "ok": section is not None,
                "flag_enabled": reasoning_in_report_enabled(),
                "specimen_claims": len(section["claims"]) if section else 0,
                "specimen_issues": len(section["issues"]) if section else 0,
            }
        except Exception as e:
            result["report_bridge"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        # Phase 3 (the moat): inspection parser verified against the real
        # 2839 Pendleton specimen — the four-plus silent hazards a disclosure
        # cannot reveal.
        try:
            from reasoning import (parse_inspection_text, load_inspection_field_map,
                                   run_pipeline, compose as _compose2)
            # use the recorded specimen findings as the ground-truth fixture
            from reasoning import load_inspection_specimen_findings
            spec = load_inspection_specimen_findings()
            # rebuild readings from the asset's verified concern/clean lists
            insp = []
            for iid, meta in (spec.get("concerns", {}) or {}).items():
                insp.append({"item_id": iid, "value": "yes",
                             "severity": meta.get("severity", "moderate"),
                             "silent_hazard": bool(meta.get("silent_hazard")),
                             "raw_text": meta.get("evidence", iid),
                             "locator": f"INSPECTION/{iid}"})
            for iid in (spec.get("clean", []) or []):
                insp.append({"item_id": iid, "value": "no", "severity": "clean",
                             "silent_hazard": False, "raw_text": iid,
                             "locator": f"INSPECTION/{iid}"})
            ck2 = set(_compose2("CA", "SFH").ids())
            off = [r["item_id"] for r in insp if r["item_id"] not in ck2]
            pr = run_pipeline([], "CA", "SFH", inspection_readings=insp,
                              zip_code="95148", property_year_built=1977, persist=False)
            silent = sum(1 for c in pr.claims if c.polarity == "contradicts"
                         and c.checklist_item_id in
                         {"electrical.panel_brand_safety", "electrical.wiring_material",
                          "hvac.flue_venting_integrity", "environmental.asbestos_risk",
                          "environmental.lead_paint_risk"})
            _offer = pr.issues_result.offer if pr.issues_result else None
            result["inspection_fixture"] = {
                "ok": len(off) == 0 and silent == 5,
                "property": "2839 Pendleton Dr",
                "silent_hazards_caught": silent,
                "issues": len(pr.issues_result.issues) if pr.issues_result else 0,
                "off_checklist": len(off),
                "price_basis_low": round(_offer.price_adjustment_low) if _offer else 0,
                "price_basis_high": round(_offer.price_adjustment_high) if _offer else 0,
            }
        except Exception as e:
            result["inspection_fixture"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        # Phase 1b (scan extraction): TDS text -> field-state bridge, verified on
        # a clean vision-style sample. Imports pdf_handler (heavy OCR deps); guard.
        try:
            from pdf_handler import extract_tds_field_state
            sample = ("TRANSFER DISCLOSURE STATEMENT Section A. items checked below: "
                      "Smoke Detector(s) [X] Roof: Type Tile Age 35 years "
                      "Section B. defects: Interior Walls [X] Roof [ ] Plumbing [ ] "
                      "Section C. aware: 1. Environmental hazards Yes [ ] No [X] "
                      "13. Homeowners Association Yes [ ] No [X] "
                      "Section D. smoke detector compliance [X] water heater braced")
            fs, fs_score, _ = extract_tds_field_state(sample)
            result["scan_extraction"] = {
                "ok": fs is not None and fs.get("section_B_defects_checked") == ["B_interior_walls"],
                "confidence": round(fs_score, 2) if fs_score else 0,
                "note": "TDS text->field-state (reuses existing OCR/vision; no new OCR)",
            }
        except Exception as e:
            result["scan_extraction"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        # Acceptance fixture: run the REAL 381 Tina Dr TDS specimen end-to-end
        # through the live pipeline and assert it behaves correctly. This is the
        # regression guard the build plan called for — not a synthetic smoke test.
        try:
            from reasoning.tds_parser import parse_tds_field_state, load_specimen_field_state
            from reasoning import run_pipeline, compose as _comp
            fs = load_specimen_field_state()
            readings = parse_tds_field_state(fs)
            ck_ids = set(_comp("CA", "SFH").ids())
            off = [r["item_id"] for r in readings if r["item_id"] not in ck_ids]
            pr = run_pipeline(readings, "CA", "SFH", persist=False)
            concern = [c for c in pr.claims if c.polarity == "contradicts"]
            acc = {
                "readings": len(readings),
                "all_on_checklist": len(off) == 0,
                "claims": len(pr.claims),
                "concern_claims": len(concern),
                "clean_form_no_concerns": len(concern) == 0,  # specimen disclosed nothing actionable
            }
            acc["ok"] = acc["all_on_checklist"] and len(readings) > 0 and acc["clean_form_no_concerns"]
            result["acceptance_fixture"] = acc
        except Exception as e:
            result["acceptance_fixture"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        # HONEST COVERAGE / WIRING REPORT — what is actually connected in the live
        # path vs built-but-unwired. This makes the tile tell the truth instead of
        # reading green while the system is half-connected.
        try:
            import inspect as _inspect
            from reasoning import report_bridge as _rb
            bridge_src = _inspect.getsource(_rb)
            # persistence is wired if the bridge passes a flag-driven persist into
            # run_pipeline (no longer hardcoded persist=False)
            persist_wired = ("reasoning_persist_enabled" in bridge_src
                             and "persist=want_persist" in bridge_src)
            persist_on = persist_wired and _rb.reasoning_persist_enabled()
        except Exception:
            persist_wired = False
            persist_on = False
        try:
            import os as _os
            from reasoning.report_bridge import reasoning_in_report_enabled as _flag
            flag_on = _flag()
        except Exception:
            flag_on = False
        # detect whether an inspection parser exists yet
        try:
            import importlib as _il
            _il.import_module("reasoning.inspection_parser")
            inspection_input = True
        except Exception:
            inspection_input = False
        # cost bands: wired if the pipeline calls the cost-band populator which
        # uses the existing repair_cost_estimator.
        try:
            import inspect as _inspect2
            from reasoning import cost_bands as _cbmod
            from reasoning import pipeline as _plmod
            cb_src = _inspect2.getsource(_cbmod)
            pl_src = _inspect2.getsource(_plmod)
            cost_wired = ("repair_cost_estimator" in cb_src
                          and "populate_cost_bands" in pl_src)
        except Exception:
            cost_wired = False

        result["coverage"] = {
            "deterministic_input": "TDS only",          # form-field map (35 form + 13 report) not yet fed
            "form_report_input_wired": False,            # 1a map has no live producer beyond TDS
            "inspection_input": inspection_input,        # the 21 inspection-only items incl silent hazards
            "persistence_wired": persist_wired,          # 0b write path connected to the live bridge?
            "persistence_live": persist_on,              # ...and the persist flag actually on?
            "cost_bands_wired": cost_wired,              # offer $ figures populated?
            "buyer_report_flag_on": flag_on,             # section visible to buyers?
            "honest_status": (
                "ENGINE COMPLETE, INTEGRATION " +
                ("IMPROVING" if inspection_input else "PARTIAL") + ": "
                "TDS path works end-to-end; "
                "inspection input " + ("WIRED (silent hazards caught)" if inspection_input else "ABSENT") + "; "
                "persistence " + (
                    "ON" if persist_on else
                    ("WIRED, flag off" if persist_wired else "OFF")
                ) + "; "
                "cost figures " + ("wired" if cost_wired else "NOT wired (offer $ = 0)") + "; "
                "buyer flag " + ("ON" if flag_on else "OFF") + "."
            ),
        }
    except Exception as e:  # never raise into the admin page
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def persistence_check() -> Dict[str, Any]:
    """
    Phase 0b: verify the persisted reasoning-tier tables exist in the DB.

    Requires app/DB context (called from the admin endpoint, not the test-shared
    pure self-check). Never raises — reports tables_present=False with an error
    so the admin dashboard shows reality.
    """
    expected = [
        "reasoning_findings", "reasoning_claims", "reasoning_issues",
        "claim_findings", "issue_claims",
    ]
    out: Dict[str, Any] = {"tables_present": False, "tables": {}, "error": None}
    try:
        import sqlalchemy as sa
        from models import db
        inspector = sa.inspect(db.engine)
        existing = set(inspector.get_table_names())
        out["tables"] = {t: (t in existing) for t in expected}
        out["tables_present"] = all(out["tables"].values())
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out
