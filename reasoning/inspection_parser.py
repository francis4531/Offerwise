"""
Inspection report parser (Phase 3 — the moat).

Converts inspection report TEXT into pipeline field readings keyed to checklist
items, using the real WINspect/ASHI field map (built from a real specimen,
2839 Pendleton Dr). This is the half that catches what disclosures hide: latent
hazards a seller cannot or need not disclose (FPE panel, aluminum wiring,
detached flue, pre-1978 asbestos/lead).

NO new OCR. Input is the text the existing extraction layer already produces
(PDFHandler / extract_text_via_vision with document_type='inspection_report').
This module only structures that text into checklist-keyed readings — it does
NOT duplicate document_parser.InspectionFinding (the legacy runtime path), which
serves the old risk model on the 8-category taxonomy. Different output, different
consumer.

Design reuses the hard-won lesson from the TDS parser: match each component
label and read the rating/tokens in a TIGHT trailing window, so a token from a
neighbouring item can't bleed across. The detail section is authoritative;
presence in the summary raises evidence-quality confidence.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_INSPECTION_MAP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "checklist", "inspection_field_map_v0_1.yaml",
)

_SEVERITY_RANK = {"clean": 0, "minor": 1, "moderate": 2, "major": 3, "critical": 4}


class InspectionMapError(ValueError):
    pass


@dataclass
class InspectionFieldMap:
    format: str
    rating_severity: Dict[str, str]
    findings: List[Dict[str, Any]]
    raw: Dict[str, Any] = field(default_factory=dict)


def load_inspection_field_map(path: Optional[str] = None) -> InspectionFieldMap:
    path = path or DEFAULT_INSPECTION_MAP_PATH
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    findings = raw.get("findings", []) or []
    if not findings:
        raise InspectionMapError("inspection field map has no findings")
    return InspectionFieldMap(
        format=str(raw.get("format", "?")),
        rating_severity={str(k).lower(): v for k, v in (raw.get("rating_severity", {}) or {}).items()},
        findings=findings,
        raw=raw,
    )


def _detail_region(text_low: str) -> str:
    """Return the Detail section if present, else the whole text.

    The report separates a SUMMARY SECTION from the detailed body ("FULL
    REPORT" / "Home Inspection Details"). The detail is authoritative.
    """
    for marker in ("home inspection details", "full report"):
        i = text_low.find(marker)
        if i != -1:
            return text_low[i:]
    return text_low


def _summary_region(text_low: str) -> str:
    i = text_low.find("summary")
    j = text_low.find("full report")
    if i != -1 and j != -1 and j > i:
        return text_low[i:j]
    return ""


def _severity_from_rating(window: str, fmap: InspectionFieldMap) -> str:
    """Read the rating token nearest the start of the window -> severity."""
    best = None
    best_pos = len(window) + 1
    for token, sev in fmap.rating_severity.items():
        p = window.find(token)
        if p != -1 and p < best_pos:
            best_pos = p
            best = sev
    return best or "clean"


def parse_inspection_text(
    text: str,
    fmap: Optional[InspectionFieldMap] = None,
) -> List[Dict[str, Any]]:
    """
    Inspection report text -> pipeline field readings.

    Each reading: {item_id, value ('yes'|'no'), source_form 'INSPECTION',
    locator, raw_text, severity, silent_hazard}. value 'yes' means a concern is
    present (contradicts a clean property); 'no' means the item was checked and
    is clean (a support reading, for dual confidence).
    """
    fmap = fmap or load_inspection_field_map()
    if not text:
        return []
    low = text.lower()
    detail = _detail_region(low)
    summary = _summary_region(low)

    # Pre-compute the positions of every finding label in the detail so we can
    # bound each item's reading window at the NEXT label (no cross-item bleed).
    label_positions = []
    for f in fmap.findings:
        lbl = f.get("match", "").lower()
        if lbl:
            p = detail.find(lbl)
            if p != -1:
                label_positions.append(p)
    label_positions = sorted(set(label_positions))

    def _next_label_after(start: int) -> int:
        for p in label_positions:
            if p > start:
                return p
        return len(detail)

    readings: List[Dict[str, str]] = []
    seen: Dict[str, Dict[str, Any]] = {}  # item_id -> reading (keep worst)

    for f in fmap.findings:
        if f.get("kind") == "datapoint":
            continue  # datapoints aren't concern/clean readings here
        label = f.get("match", "").lower()
        item_id = f.get("maps_to")
        if not label or not item_id:
            continue
        # find the label in the detail region; read a trailing window
        pos = detail.find(label)
        if pos == -1:
            continue
        start = pos + len(label)
        # bound the window at the next finding label so tokens can't bleed across
        bound = min(start + 400, _next_label_after(pos))
        rating_window = detail[start: min(start + 60, bound)]
        token_window = detail[start: bound]

        concern_tokens = [t.lower() for t in (f.get("concern_tokens") or [])]
        rating_sev = _severity_from_rating(rating_window, fmap)
        # a concern exists if a concern token appears in the window, OR the
        # rating itself is a concern grade (attention/maintenance/action req.)
        token_hit = any(t in token_window for t in concern_tokens) if concern_tokens else False
        rating_concern = rating_sev in ("moderate", "critical")
        is_concern = token_hit or rating_concern

        if not is_concern:
            # record a clean reading (checked & fine) — supports dual confidence
            value, severity = "no", "clean"
        else:
            value = "yes"
            severity = f.get("severity_override") or (
                "critical" if rating_sev == "critical" else "moderate"
            )

        silent = bool(f.get("silent_hazard")) and value == "yes"
        in_summary = label in summary
        reading = {
            "item_id": item_id,
            "value": value,
            "source_form": "INSPECTION",
            "locator": f"INSPECTION/{f.get('section','')}/{label}",
            "raw_text": (f.get("note") or label)[:300],
            "severity": severity,
            "silent_hazard": silent,
            "corroborated_in_summary": in_summary,
        }
        # keep the worst reading per item (a concern outranks a clean one)
        prev = seen.get(item_id)
        if prev is None or _SEVERITY_RANK.get(severity, 0) > _SEVERITY_RANK.get(prev["severity"], 0):
            seen[item_id] = reading

    readings = list(seen.values())
    return readings


def load_inspection_specimen_findings(fmap: Optional[InspectionFieldMap] = None) -> Dict[str, Any]:
    """Return the verified 2839 Pendleton specimen (concerns + clean) from the asset."""
    fmap = fmap or load_inspection_field_map()
    return fmap.raw.get("specimen_2839_pendleton", {})


def extract_inspection_readings(
    text: str,
    *,
    checklist_ids: Optional[List[str]] = None,
    fmap: Optional[InspectionFieldMap] = None,
    allow_llm: bool = True,
    min_keyword_concerns: int = 2,
    llm_client: Any = None,
) -> Dict[str, Any]:
    """
    Format-general entry point for inspection extraction.

    Strategy: run the cheap deterministic WINspect keyword parser first. If it
    finds a real set of concerns, use it (no LLM cost). If it comes up
    (near-)empty — which happens on any non-WINspect / narrative-format report —
    fall back to the LLM extractor, which reads ANY format into the checklist.

    Returns {'readings': [...], 'method': 'keyword'|'llm'|'keyword_empty'}.
    Never raises.
    """
    result = {"readings": [], "method": "keyword"}
    try:
        kw = parse_inspection_text(text, fmap)
    except Exception:
        kw = []
    kw_concerns = [r for r in kw if r.get("value") == "yes"]

    if len(kw_concerns) >= min_keyword_concerns:
        result["readings"] = kw
        result["method"] = "keyword"
        return result

    # keyword parser came up short -> try the format-general LLM path
    if allow_llm:
        try:
            from .inspection_llm_extractor import extract_inspection_findings_llm
            if checklist_ids is None:
                from .composition import compose
                checklist_ids = sorted(compose("CA", "SFH").ids())
            llm = extract_inspection_findings_llm(
                text, checklist_ids, client=llm_client
            )
            if llm:
                result["readings"] = llm
                result["method"] = "llm"
                return result
        except Exception:
            pass

    # nothing worked — return whatever the keyword parser had (possibly empty)
    result["readings"] = kw
    result["method"] = "keyword" if kw else "keyword_empty"
    return result
