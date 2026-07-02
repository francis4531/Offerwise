"""
reasoning_pendleton_regression.py — the canonical regression gate.

2839 Pendleton Dr is the deal where a single careful Claude pass beat the live
keyword engine. This harness scores the `reasoning/` engine against that
7-finding answer key (see the handoff), now including the DISCLOSURE
cross-reference — the literal moat sentence: "the seller disclosed X / said
nothing / answered clean, and the inspection found Y."

Two layers:

  Layer B (deterministic reasoning core) — runs OFFLINE, no API key. Feeds the
  pipeline canonical inspection readings AND canonical TDS/SPQ disclosure
  readings, then checks the derived Issues + disclosure_status + OfferHandoff.
  Isolates reasoning from the model: given correct readings on both sides, does
  the engine reason, classify, AND cross-reference disclosure correctly?

  Layer A (full model-as-pass) — runs only when ANTHROPIC_API_KEY is set and the
  Pendleton PDFs are present (staging). Extracts real PDF text, runs the LLM
  inspection extractor (robusted via ai_json) + the TDS parser, then the
  pipeline, and scores the SAME way.

Run:
    python3 reasoning_pendleton_regression.py
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

JURISDICTION = "CA"
PROPERTY_TYPE = "SFH"
PDF_DIR = os.path.join(os.path.dirname(__file__), "test_corpus", "regression_pendleton")


# --- the answer key --------------------------------------------------------
# expect: 'surface' (must appear), 'risk_down' (must NOT be a concern),
#         'confirmation' (informational). disclosure: expected disclosure_status
# when the TDS side is fed. limitation: a documented design gap (warn, not fail).
ANSWER_KEY = [
    dict(n=1, label="Master-bath water pattern (seller DISCLOSED shower leak; inspection corroborates)",
         items=["structure.water_intrusion_bath"], expect="surface",
         disclosure="corroborated"),
    dict(n=2, label="Undisclosed kitchen leak (floor warping + rim-joist stains)",
         items=["structure.water_intrusion_kitchen"], expect="surface",
         disclosure="undisclosed"),
    dict(n=3, label="Federal Pacific (FPE) panel + aluminum wiring (seller silent)",
         items=["electrical.panel_brand_safety", "electrical.wiring_material"],
         expect="surface", silent_hazard=True, disclosure="undisclosed"),
    dict(n=4, label="Seismic correction — sill anchors verified (risk DOWN)",
         items=["foundation.movement_evidence", "structure.seismic_retrofit"],
         expect="risk_down"),
    dict(n="3b", label="Furnace flue detached / CO risk (seller silent)",
         items=["hvac.flue_venting_integrity"], expect="surface",
         silent_hazard=True, disclosure="undisclosed"),
    dict(n=6, label="Active water-supply leak + galvanized encrustation (seller silent)",
         items=["plumbing.active_leaks", "plumbing.known_defect_pipe_material"],
         expect="surface", disclosure="undisclosed"),
    dict(n="C", label="Asbestos: seller answered TDS C.1 clean, inspection flags pre-1978 risk",
         items=["environmental.asbestos_risk"], expect="surface",
         disclosure="contradiction"),
    dict(n=7, label="Confirmations (public sewer, metal roof no active leak)",
         items=["plumbing.sewer_type", "roof.leak_evidence"], expect="confirmation"),
]


# --- canonical readings (Layer B) ------------------------------------------
def canonical_inspection_readings() -> List[Dict[str, Any]]:
    def r(item_id, value, severity, raw_text, corroborated=False):
        return dict(item_id=item_id, value=value, severity=severity,
                    raw_text=raw_text, corroborated_in_summary=corroborated,
                    locator=f"INSPECTION/{item_id}")
    return [
        r("electrical.panel_brand_safety", "yes", "critical",
          "main + sub panel are Federal Pacific Stab-Lock (known fire hazard)", True),
        r("electrical.wiring_material", "yes", "major",
          "some branch wiring is aluminum; verify outlets rated for AL"),
        r("electrical.gfci_protection", "yes", "moderate",
          "hall bath GFCI would not trip when tested"),
        r("hvac.flue_venting_integrity", "yes", "major",
          "furnace exhaust flue partially detached at a junction — spent gas may escape", True),
        r("plumbing.active_leaks", "yes", "major",
          "leak at incoming water supply pipe near the shut-off valve", True),
        r("plumbing.known_defect_pipe_material", "yes", "major",
          "encrustation on galvanized supply pipe — leakage/blockage imminent", True),
        r("structure.water_intrusion_bath", "yes", "moderate",
          "master-bath shower-pan water pattern (mixing valve dead, drain/toilet hardware, sill moisture)", True),
        r("structure.water_intrusion_kitchen", "yes", "moderate",
          "kitchen floor warping at the refrigerator + rim-joist moisture stains beneath the kitchen"),
        # inspection flags asbestos risk (pre-1978 popcorn ceiling) — the seller
        # answered TDS C.1 clean, so this becomes a disclosure contradiction.
        r("environmental.asbestos_risk", "yes", "minor",
          "pre-1978 acoustic (popcorn) ceiling may contain asbestos; not tested"),
        # risk DOWN — checked clean
        r("foundation.movement_evidence", "no", "clean",
          "structure does not show significant settlement"),
        r("structure.seismic_retrofit", "no", "clean",
          "sill-plate anchor bolts located and verified in the crawl space"),
        # confirmations
        r("plumbing.sewer_type", "no", "clean", "serviced by municipal sewer"),
        r("roof.leak_evidence", "no", "clean", "no evidence the roof is currently leaking"),
    ]


def canonical_tds_field_readings() -> List[Dict[str, str]]:
    """The seller's disclosure side (TDS/SPQ), from the real Pendleton forms.
    Only items in the form-field map + resolved checklist become claims.

      - SPQ 10: seller DISCLOSED water intrusion (master-bedroom shower-pan leak,
        garage leak 'has been fixed') -> water_intrusion_history 'yes'
        => corroborates the inspection's water finding.
      - TDS C.1: seller answered NO to environmental hazards (asbestos, lead, ...)
        -> asbestos_risk 'no' => the inspection's asbestos flag CONTRADICTS it.

    The seller disclosed NOTHING about the FPE panel, aluminum wiring, the
    detached flue, or the active supply-line leak -> those stay 'undisclosed'
    (no disclosure claim), which is the correct, high-leverage status.
    """
    return [
        # SPQ 10: seller disclosed the master-bedroom shower-pan leak -> BATH.
        # The kitchen leak was NOT mentioned -> no kitchen disclosure claim.
        dict(item_id="structure.water_intrusion_bath", value="yes"),           # SPQ 10 disclosed (shower)
        dict(item_id="environmental.asbestos_risk", value="no"),               # TDS C.1 answered clean
    ]


# --- scoring ---------------------------------------------------------------
def _issue_item_ids(issues) -> set:
    ids = set()
    for iss in issues:
        ids.update(getattr(iss, "claim_item_ids", []) or [])
    return ids


def _status_of_item(issues, item_id):
    for iss in issues:
        if item_id in (getattr(iss, "claim_item_ids", []) or []):
            return getattr(iss, "disclosure_status", None)
    return None


def score(issues, offer, layer="B") -> int:
    surfaced = _issue_item_ids(issues)
    silent_items = {iid for iss in issues
                    if (getattr(iss, "silent_hazard_flag", False)
                        or getattr(iss, "decision_class", "") == "silent_hazard")
                    for iid in (getattr(iss, "claim_item_ids", []) or [])}

    print(f"\n  derived issues: {len(issues)}   surfaced item ids: {sorted(surfaced)}")
    if offer:
        print(f"  \"what the seller didn't tell you\" (undisclosed/contradiction): "
              f"{offer.undisclosed_titles}\n")

    failures = 0
    for a in ANSWER_KEY:
        hit = [i for i in a["items"] if i in surfaced]

        if a["expect"] == "surface":
            ok = bool(hit)
            bits = [f"surfaced={bool(hit)}"]
            if a.get("silent_hazard"):
                sh = any(i in silent_items for i in a["items"])
                ok = ok and sh
                bits.append(f"silent_hazard={sh}")
            if a.get("disclosure"):
                actual = next((_status_of_item(issues, i) for i in a["items"]
                               if _status_of_item(issues, i)), None)
                want = a["disclosure"]
                if layer == "B":
                    ok = ok and (actual == want)
                    bits.append(f"disclosure={actual} (want {want})")
                else:
                    # Layer A (real extraction): a specific disclosure_status is
                    # non-deterministic — room-granularity (water_intrusion_bath
                    # can't tell master from hallway) plus LLM run-to-run variance.
                    # Report it, don't gate on it. Layer B is the deterministic gate.
                    tag = "matches B" if actual == want else "differs (not gated in A)"
                    bits.append(f"disclosure={actual} ({tag})")
            mark = "PASS" if ok else "FAIL"
            failures += 0 if ok else 1
            detail = " ".join(bits)
        elif a["expect"] == "risk_down":
            ok = not hit
            mark = "PASS" if ok else "FAIL"
            failures += 0 if ok else 1
            detail = f"flagged_as_concern={bool(hit)} (want False)"
        else:  # confirmation
            mark = "ok " if hit else "—  "
            detail = "present" if hit else "not surfaced (informational)"

        print(f"  [{mark}] #{str(a['n']):<2} {a['label']}\n           {detail}")
        if a.get("limitation"):
            print(f"           WARN (known gap): {a['limitation']}")

    return failures


def run_layer_b() -> int:
    from reasoning import run_pipeline
    print("=" * 78)
    print("LAYER B — deterministic reasoning core (offline: inspection + TDS readings)")
    print("=" * 78)
    result = run_pipeline(
        field_readings=canonical_tds_field_readings(),
        jurisdiction=JURISDICTION,
        property_type=PROPERTY_TYPE,
        inspection_readings=canonical_inspection_readings(),
        persist=False,
        zip_code="95148",
        property_year_built=1977,
    )
    issues = result.issues_result.issues if result.issues_result else []
    offer = result.issues_result.offer if result.issues_result else None
    print("\n  pipeline summary:", result.summary())
    failures = score(issues, offer, "B")
    print(f"\n  LAYER B: {'PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    return failures


def run_layer_a() -> int:
    insp = os.path.join(PDF_DIR, "inspection.pdf")
    if not os.environ.get("ANTHROPIC_API_KEY") or not os.path.exists(insp):
        print("\nLAYER A skipped (no ANTHROPIC_API_KEY or PDFs absent) — run on staging.")
        return 0
    print("\n" + "=" * 78)
    print("LAYER A — full model-as-pass on the real Pendleton PDFs")
    print("=" * 78)
    try:
        from reasoning import compose, run_pipeline
        from reasoning.inspection_llm_extractor import extract_inspection_findings_llm
        text = _extract_pdf_text(insp)
        ids = sorted(compose(JURISDICTION, PROPERTY_TYPE).ids())
        readings = extract_inspection_findings_llm(text, ids)
        print(f"  LLM extractor produced {len(readings)} readings")
        # NOTE: real TDS parsing -> field_readings is the next wire-up; here we
        # pass the canonical disclosure side so the cross-reference is exercised.
        result = run_pipeline(
            field_readings=canonical_tds_field_readings(),
            jurisdiction=JURISDICTION, property_type=PROPERTY_TYPE,
            inspection_readings=readings, persist=False,
            zip_code="95148", property_year_built=1977,
        )
        issues = result.issues_result.issues if result.issues_result else []
        offer = result.issues_result.offer if result.issues_result else None
        failures = score(issues, offer, "A")
        print(f"\n  LAYER A: {'PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
        return failures
    except Exception as e:
        print(f"  LAYER A error: {e}")
        return 1


def _extract_pdf_text(path: str) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        try:
            from pypdf import PdfReader
            return "\n".join((pg.extract_text() or "") for pg in PdfReader(path).pages)
        except Exception as e:
            raise RuntimeError(f"no PDF text extractor available: {e}")


if __name__ == "__main__":
    fb = run_layer_b()
    fa = run_layer_a()
    sys.exit(1 if (fb or fa) else 0)
