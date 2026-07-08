"""
benchmark_head_to_head.py — v5.89.271

An HONEST, objective head-to-head: OfferWise's reasoning engine vs a single raw
Claude pass, scored against the 2839 Pendleton Dr answer key (the same 7-finding
key the regression uses). This exists because the old /comparison page ("5 wins,
0 losses" vs GPT-5 / Opus 4.7, Apr 2026, synthetic cases) is stale and, on
contradiction detection, contradicted by our own Pendleton finding. This produces
REAL numbers, on a REAL deal, against the CURRENT model — and it's scored by
answer-key recall, not subjective judging, so it can't be gamed.

Two sides, scored identically (which of the 7 key findings each surfaced):
  - reasoning side: deterministic, offline (canonical readings -> run_pipeline).
  - raw Claude side: needs the API — one pass over both documents at claude-opus-4-8.

The raw-Claude output is free text, so it's scored by keyword signals per finding
(objective presence check). Run it from the admin page; the numbers come from a
real run, never fabricated.
"""
from typing import Any, Dict, List, Optional

# Keyword signals per answer-key finding — a finding is "caught" in free text if
# ANY signal group matches (all terms in a group present, case-insensitive). Signals
# are drawn from the finding's defining facts, not OfferWise's phrasing, so they're
# fair to any system's output.
FINDING_SIGNALS = [
    dict(n="1", label="Master-bath water pattern (disclosed shower leak; inspection corroborates)",
         groups=[["master", "bath"], ["shower pan"], ["shower", "leak"]]),
    dict(n="2", label="Undisclosed kitchen leak (floor warping + rim-joist stains)",
         groups=[["kitchen", "warp"], ["kitchen", "moisture"], ["rim joist"], ["rim-joist"]]),
    dict(n="3", label="Federal Pacific (FPE) panel + aluminum wiring (seller silent)",
         groups=[["federal pacific"], ["fpe"], ["stab-lok"], ["stab lok"], ["aluminum", "wiring"]]),
    dict(n="4", label="Seismic sill anchors verified — risk DOWN (should NOT be flagged as a hazard)",
         groups=[["sill", "anchor"], ["anchor bolt"], ["seismic", "verified"]], risk_down=True),
    dict(n="3b", label="Furnace flue detached / CO risk (seller silent)",
         groups=[["flue"], ["carbon monoxide"], ["co risk"], ["vent", "furnace"]]),
    dict(n="6", label="Active water-supply leak + galvanized encrustation (seller silent)",
         groups=[["galvanized"], ["supply", "leak"], ["encrustation"], ["corrosion", "pipe"]]),
    dict(n="C", label="Asbestos: seller answered clean, inspection flags pre-1978 risk (contradiction)",
         groups=[["asbestos"]]),
    dict(n="7", label="Confirmations (public sewer, metal roof no active leak)",
         groups=[["public sewer"], ["metal roof"]], informational=True),
]

# Map answer-key item_ids (reasoning side) to the finding numbers above.
_ITEM_TO_FINDING = {
    "structure.water_intrusion_bath": "1",
    "structure.water_intrusion_kitchen": "2",
    "electrical.panel_brand_safety": "3", "electrical.wiring_material": "3",
    "foundation.movement_evidence": "4", "structure.seismic_retrofit": "4",
    "hvac.flue_venting_integrity": "3b",
    "plumbing.active_leaks": "6", "plumbing.known_defect_pipe_material": "6",
    "environmental.asbestos_risk": "C",
    "plumbing.sewer_type": "7", "roof.leak_evidence": "7",
}

RAW_CLAUDE_PROMPT = """You are a buyer's advocate. Below are a seller's property \
disclosure and a home inspection report for the same house. Cross-reference them: \
identify every contradiction between what the seller disclosed and what the \
inspection found, every material issue the inspection surfaced that the seller did \
NOT disclose, and any latent safety hazards. Be specific and name each issue.

=== SELLER DISCLOSURE ===
{disclosure}

=== INSPECTION REPORT ===
{inspection}

List the findings, one per line, most important first."""


def _text_catches(text: str, finding: dict) -> bool:
    t = (text or "").lower()
    for group in finding["groups"]:
        if all(term.lower() in t for term in group):
            return True
    return False


def score_free_text(text: str) -> Dict[str, Any]:
    """Objective recall of the 7 findings from any system's free-text output."""
    caught, missed = [], []
    for f in FINDING_SIGNALS:
        (caught if _text_catches(text, f) else missed).append(f["n"])
    # 'risk_down' (#4) and 'informational' (#7) don't count toward the core recall
    # score; core = the 6 real issues (#1,2,3,3b,6,C). #4 is a bonus if the system
    # correctly does NOT raise it as a hazard (can't tell from free text alone —
    # reported, not scored). #7 is informational.
    core = [f["n"] for f in FINDING_SIGNALS if not f.get("risk_down") and not f.get("informational")]
    core_caught = [n for n in caught if n in core]
    return {"caught": caught, "missed": missed,
            "core_caught": core_caught, "core_total": len(core),
            "core_recall": round(len(core_caught) / len(core), 3) if core else 0.0}


def reasoning_side() -> Dict[str, Any]:
    """Deterministic offline run of the reasoning engine on the Pendleton canonical
    readings, scored by which answer-key findings it surfaced. No API."""
    from reasoning import run_pipeline
    from reasoning_pendleton_regression import (
        canonical_tds_field_readings, canonical_inspection_readings,
        JURISDICTION, PROPERTY_TYPE,
    )
    result = run_pipeline(
        field_readings=canonical_tds_field_readings(),
        jurisdiction=JURISDICTION, property_type=PROPERTY_TYPE,
        inspection_readings=canonical_inspection_readings(),
        persist=False, zip_code="95148", property_year_built=1977,
    )
    issues = result.issues_result.issues if result.issues_result else []
    surfaced_items = set()
    for iss in issues:
        for iid in (getattr(iss, "claim_item_ids", []) or []):
            surfaced_items.add(iid)
    findings_caught = {_ITEM_TO_FINDING[i] for i in surfaced_items if i in _ITEM_TO_FINDING}
    caught, missed = [], []
    for f in FINDING_SIGNALS:
        (caught if f["n"] in findings_caught else missed).append(f["n"])
    core = [f["n"] for f in FINDING_SIGNALS if not f.get("risk_down") and not f.get("informational")]
    core_caught = [n for n in caught if n in core]
    return {"caught": caught, "missed": missed,
            "core_caught": core_caught, "core_total": len(core),
            "core_recall": round(len(core_caught) / len(core), 3) if core else 0.0,
            "issue_count": len(issues)}


def _extract_pendleton_docs() -> Optional[Dict[str, str]]:
    """Extract disclosure + inspection text from the regression specimens."""
    import os
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "test_corpus", "regression_pendleton")
    out = {}
    try:
        import pdfplumber
        for key, fname in (("disclosure", "disclosures.pdf"), ("inspection", "inspection.pdf")):
            path = os.path.join(base, fname)
            with pdfplumber.open(path) as pdf:
                out[key] = "\n".join((p.extract_text() or "") for p in pdf.pages)[:60000]
        return out
    except Exception:
        return None


def raw_claude_side(client, model: str = "claude-opus-4-8") -> Dict[str, Any]:
    """One raw Claude pass over both documents, scored by the same answer key.
    Requires an Anthropic client (the API) — never fabricated."""
    docs = _extract_pendleton_docs()
    if not docs:
        return {"error": "could not extract Pendleton specimen text"}
    prompt = RAW_CLAUDE_PROMPT.format(disclosure=docs["disclosure"], inspection=docs["inspection"])
    resp = client.messages.create(
        model=model, max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content)
    scored = score_free_text(text)
    scored["model"] = model
    scored["output_chars"] = len(text)
    return scored


def head_to_head(client=None, model: str = "claude-opus-4-8") -> Dict[str, Any]:
    """Run both sides and return the comparison. The reasoning side is always run
    (offline); the raw-Claude side runs only if a client is supplied."""
    out = {"case": "2839 Pendleton Dr, San Jose CA 95148",
           "answer_key": [{"n": f["n"], "label": f["label"]} for f in FINDING_SIGNALS],
           "reasoning": reasoning_side()}
    if client is not None:
        out["raw_claude"] = raw_claude_side(client, model=model)
    else:
        out["raw_claude"] = {"skipped": "no Anthropic client provided (needs API to run)"}
    r = out["reasoning"].get("core_recall")
    c = out["raw_claude"].get("core_recall")
    if c is not None:
        out["verdict"] = ("reasoning_wins" if r > c else
                          "raw_claude_wins" if c > r else "tie")
    return out
