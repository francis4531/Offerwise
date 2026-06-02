"""
Deterministic form-field / structured-report mapper (Phase 1a, Q-5.2 path).

The deterministic half of evidence mapping: standardized forms (TDS/SPQ) and
structured reports (NHD/title/CLUE) have known field locations, so a curated
map resolves a field reading to a checklist item with known polarity — no model
needed. This covers the 47 deterministic items the source audit identified.

This module is PURE and ADDITIVE: it does not touch the live analysis path. It
consumes (form, locator, value) tuples and emits Claim-shaped dicts. The parser
that PRODUCES those tuples from a real C.A.R. form PDF is a flagged follow-on
(needs real form specimens — see the asset's `gaps`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

DEFAULT_MAP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "checklist", "form_field_map_v0_1.yaml",
)

# A "positive reading" of a defect/hazard/zone field (yes / present / IN-zone /
# disclosed) supports the unfavorable state; a negative reading supports the
# favorable state. These string sets classify a raw field value.
_POSITIVE = {"yes", "y", "true", "present", "in", "in-zone", "checked", "disclosed", "x"}
_NEGATIVE = {"no", "n", "false", "absent", "out", "not-in-zone", "unchecked", "not disclosed"}


class FormMapError(ValueError):
    pass


@dataclass
class FormFieldMap:
    version: str
    mappings_by_item: Dict[str, Dict[str, Any]]
    mappings_by_locator: Dict[str, Dict[str, Any]]
    coverage: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    def item_ids(self) -> List[str]:
        return list(self.mappings_by_item.keys())


@dataclass
class DeterministicClaim:
    """A Claim produced deterministically from a form/report field reading."""
    checklist_item_id: str
    resolved_value: str
    polarity: str               # 'supports' | 'contradicts' (vs favorable state)
    resolution_state: str       # 'answered'
    evidence_quality_confidence: float
    inference_confidence: float
    source_form: str
    source_locator: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def load_form_field_map(path: Optional[str] = None) -> FormFieldMap:
    path = path or DEFAULT_MAP_PATH
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict) or "mappings" not in raw:
        raise FormMapError("form-field map missing 'mappings'")

    by_item: Dict[str, Dict[str, Any]] = {}
    by_locator: Dict[str, Dict[str, Any]] = {}
    for m in raw["mappings"]:
        iid = m.get("item_id")
        if not iid:
            raise FormMapError("mapping entry missing item_id")
        by_item[iid] = m
        loc = m.get("locator")
        if loc:
            by_locator[loc] = m
    return FormFieldMap(
        version=str(raw.get("VERSION", "unknown")),
        mappings_by_item=by_item,
        mappings_by_locator=by_locator,
        coverage=raw.get("coverage", {}) or {},
        raw=raw,
    )


def _classify_value(value: str) -> str:
    """Return 'positive' | 'negative' | 'unknown' for a raw field value."""
    v = (value or "").strip().lower()
    if v in _POSITIVE:
        return "positive"
    if v in _NEGATIVE:
        return "negative"
    return "unknown"


def map_field_to_claim(
    item_id: str,
    value: str,
    fmap: FormFieldMap,
    resolved_item_ids: Optional[set] = None,
) -> Optional[DeterministicClaim]:
    """
    Deterministically resolve one form/report field reading for a known item.

    - item_id: the checklist item this field answers (from the curated map)
    - value:   the raw field reading ('yes'/'no'/'IN'/'present'/...)
    - resolved_item_ids: if given, the item must be in the property's resolved
      checklist (composition) or the field is ignored (returns None).
    """
    m = fmap.mappings_by_item.get(item_id)
    if not m:
        return None
    if resolved_item_ids is not None and item_id not in resolved_item_ids:
        return None

    cls = _classify_value(value)
    if cls == "unknown":
        # deterministic path only resolves recognized form values
        return None
    polarity = "contradicts" if cls == "positive" else "supports"  # positive defect reading contradicts a clean property
    # evidence quality: cited locator > inferred > needs_specimen
    conf = {"cited": 0.95, "inferred": 0.8, "needs_specimen": 0.6}.get(
        m.get("locator_confidence"), 0.7
    )
    return DeterministicClaim(
        checklist_item_id=item_id,
        resolved_value=value,
        polarity=polarity,
        resolution_state="answered",
        evidence_quality_confidence=conf,
        inference_confidence=1.0,  # deterministic map = no inference ambiguity
        source_form=m.get("form", ""),
        source_locator=m.get("locator"),
    )


def map_fields_to_claims(
    field_readings: List[Dict[str, str]],
    fmap: Optional[FormFieldMap] = None,
    resolved_item_ids: Optional[set] = None,
) -> List[DeterministicClaim]:
    """
    Batch form/report field readings -> deterministic Claims.

    field_readings: [{"item_id": "...", "value": "yes"}, ...]
    """
    fmap = fmap or load_form_field_map()
    out: List[DeterministicClaim] = []
    for fr in field_readings:
        claim = map_field_to_claim(
            fr.get("item_id", ""), fr.get("value", ""), fmap, resolved_item_ids
        )
        if claim:
            out.append(claim)
    return out
