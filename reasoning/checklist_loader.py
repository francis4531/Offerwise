"""
Checklist asset loader + validator.

Loads the versioned checklist YAML (data/checklist/us_v0_5.yaml) into typed
objects and validates it against the Section 3.5 item anatomy and the layer
structure. Pure / read-only — safe to import anywhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_CHECKLIST_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "checklist", "us_v0_5.yaml",
)

# Allowed enumerations from the Section 3.5 anatomy.
ANSWER_TYPES = {
    "numeric_years", "numeric", "boolean", "enum", "freetext", "range", "dollar_amount",
}
IMPORTANCE = {"critical", "major", "supporting", "informational"}
COST_IMPACT = {"yes", "indirect", "no"}
DISCLOSURE_OBLIGATION = {"required", "conditional", "not_required"}
SEVERITY = {"minor", "moderate", "major", "critical"}
UNANSWERED = {
    "seller_should_disclose", "specialist_required",
    "unanswerable_from_documents", "minor",
}
COMPLIANCE_BASIS = {"legal_requirement", "best_practice", "offerwise_judgment"}

# Fields every populated item must carry (Section 3.5).
REQUIRED_FIELDS = (
    "id", "question", "group", "answer_type", "importance", "cost_impact",
    "compliance_basis", "disclosure_obligation_state", "severity_when_negative",
    "unanswered_implication", "applicability",
)


class ChecklistValidationError(ValueError):
    """Raised when the checklist asset violates the Section 3.5 anatomy."""


@dataclass
class ChecklistItem:
    """A single first-class checklist item (Section 3.5 anatomy)."""
    id: str
    question: str
    group: str
    answer_type: str
    importance: str
    cost_impact: str
    compliance_basis: Dict[str, Any]
    disclosure_obligation_state: str
    severity_when_negative: str
    unanswered_implication: str
    applicability: Dict[str, List[str]]
    description: str = ""
    related_items: List[str] = field(default_factory=list)
    # provenance of how this item was produced during composition (set by composer)
    source_layer: str = "national_base"
    refined_by: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def jurisdictions(self) -> List[str]:
        return list(self.applicability.get("jurisdictions", []))

    def property_types(self) -> List[str]:
        return list(self.applicability.get("property_types", []))


@dataclass
class ChecklistAsset:
    """The full versioned asset: base + overlays, before composition."""
    version: str
    national_base: List[ChecklistItem]
    state_overlays: Dict[str, Dict[str, Any]]          # e.g. {"CA": {adds, refines, deprecated}}
    municipal_overlays: Dict[str, Dict[str, Any]]      # e.g. {"CA:santa_clara:san_jose": {...}}
    raw: Dict[str, Any] = field(default_factory=dict)

    def all_base_ids(self) -> set:
        return {it.id for it in self.national_base}


def _coerce_item(d: Dict[str, Any], source_layer: str, validate: bool) -> ChecklistItem:
    # Defensive: YAML coerces bare yes/no to bool. cost_impact's vocabulary is
    # yes|indirect|no, so a serializer may have written True/False. Normalize
    # back to the string vocabulary before validation.
    ci = d.get("cost_impact")
    if isinstance(ci, bool):
        d = dict(d)
        d["cost_impact"] = "yes" if ci else "no"
    if validate:
        missing = [f for f in REQUIRED_FIELDS if f not in d]
        if missing:
            raise ChecklistValidationError(
                f"item {d.get('id', '<no id>')} missing required fields: {missing}"
            )
        _validate_enums(d)
    return ChecklistItem(
        id=d["id"],
        question=d.get("question", ""),
        group=d.get("group", ""),
        answer_type=d.get("answer_type", ""),
        importance=d.get("importance", ""),
        cost_impact=d.get("cost_impact", ""),
        compliance_basis=d.get("compliance_basis", {}) or {},
        disclosure_obligation_state=d.get("disclosure_obligation_state", ""),
        severity_when_negative=d.get("severity_when_negative", ""),
        unanswered_implication=d.get("unanswered_implication", ""),
        applicability=d.get("applicability", {}) or {},
        description=d.get("description", "") or "",
        related_items=list(d.get("related_items", []) or []),
        source_layer=source_layer,
        raw=dict(d),
    )


def _validate_enums(d: Dict[str, Any]) -> None:
    iid = d.get("id", "<no id>")

    def _check(field_name: str, allowed: set):
        val = d.get(field_name)
        if val not in allowed:
            raise ChecklistValidationError(
                f"item {iid}: {field_name}={val!r} not in {sorted(allowed)}"
            )

    _check("answer_type", ANSWER_TYPES)
    _check("importance", IMPORTANCE)
    _check("cost_impact", COST_IMPACT)
    _check("disclosure_obligation_state", DISCLOSURE_OBLIGATION)
    _check("severity_when_negative", SEVERITY)
    _check("unanswered_implication", UNANSWERED)

    basis = (d.get("compliance_basis") or {}).get("basis")
    if basis not in COMPLIANCE_BASIS:
        raise ChecklistValidationError(
            f"item {iid}: compliance_basis.basis={basis!r} not in {sorted(COMPLIANCE_BASIS)}"
        )

    appl = d.get("applicability") or {}
    if "jurisdictions" not in appl or "property_types" not in appl:
        raise ChecklistValidationError(
            f"item {iid}: applicability must have jurisdictions and property_types"
        )


def _state_key_from_overlay_name(name: str) -> str:
    # "ca_overlay" -> "CA"; generic "<xx>_overlay" -> "<XX>"
    base = name[:-len("_overlay")] if name.endswith("_overlay") else name
    return base.upper()


def load_checklist(path: Optional[str] = None, validate: bool = True) -> ChecklistAsset:
    """
    Load and (by default) validate the checklist asset.

    Raises ChecklistValidationError on any anatomy violation when validate=True.
    """
    path = path or DEFAULT_CHECKLIST_PATH
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict) or "national_base" not in raw:
        raise ChecklistValidationError("asset missing national_base")

    national = [_coerce_item(d, "national_base", validate) for d in raw["national_base"]]

    # state overlays: any top-level key ending in _overlay (e.g. ca_overlay)
    state_overlays: Dict[str, Dict[str, Any]] = {}
    for key, val in raw.items():
        if key.endswith("_overlay") and key != "municipal_overlay" and isinstance(val, dict):
            state_overlays[_state_key_from_overlay_name(key)] = val

    municipal_overlays: Dict[str, Dict[str, Any]] = {}
    muni = raw.get("municipal_overlay", {}) or {}
    for juris, val in muni.items():
        if juris.startswith("_"):   # template/comment entries
            continue
        if isinstance(val, dict):
            municipal_overlays[juris] = val

    asset = ChecklistAsset(
        version=str(raw.get("VERSION", raw.get("version", "unknown"))),
        national_base=national,
        state_overlays=state_overlays,
        municipal_overlays=municipal_overlays,
        raw=raw,
    )

    if validate:
        # validate overlay 'adds' items too
        for sk, ov in state_overlays.items():
            for d in (ov.get("adds") or []):
                _coerce_item(d, f"state_overlay:{sk}", True)
        # unique ids across base + all adds
        _assert_unique_ids(asset)

    return asset


def _assert_unique_ids(asset: ChecklistAsset) -> None:
    seen = {}
    def _add(iid, where):
        if iid in seen:
            raise ChecklistValidationError(
                f"duplicate item id {iid!r} in {where} (also in {seen[iid]})"
            )
        seen[iid] = where
    for it in asset.national_base:
        _add(it.id, "national_base")
    for sk, ov in asset.state_overlays.items():
        for d in (ov.get("adds") or []):
            _add(d["id"], f"state_overlay:{sk}")
