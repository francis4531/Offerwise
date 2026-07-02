"""
Checklist composition engine (Reasoning Architecture Q-5.9).

Resolves the checklist a buyer's property is actually analyzed against by
layering, in dependency order:

    national_base  ->  state_overlay  ->  municipal_overlay

Two operations per overlay layer:
  * add    - introduce a new item id absent from the layer below
  * refine - override fields of an existing item id
             (scalar = last-layer-wins; list = union/append;
              citation/reasoning = append so provenance accumulates)

One prohibition:
  * NO SILENT DELETE - an overlay may not remove a base item. Non-applicability
    is expressed via the base item's own `applicability`, decided once and
    visibly, never deleted downstream. This preserves the "national base is
    validated and stable" guarantee.

The composed result is filtered by `applicability` to the property's
jurisdiction and property type. Every resolved item carries a provenance trace
(source_layer + refined_by) — that trace is itself audit evidence (Commitment 2.3).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .checklist_loader import ChecklistAsset, ChecklistItem, load_checklist

# Fields merged as scalars (last layer wins).
_SCALAR_FIELDS = (
    "question", "description", "group", "answer_type", "importance",
    "cost_impact", "disclosure_obligation_state", "severity_when_negative",
    "unanswered_implication",
)
# Fields merged as lists (union, order-preserving).
_LIST_FIELDS = ("related_items",)


class CompositionError(Exception):
    """Raised on an illegal composition (e.g. attempted silent delete)."""


@dataclass
class ResolvedChecklist:
    """The checklist a specific property is analyzed against."""
    jurisdiction_path: str          # e.g. "CA:santa_clara:san_jose"
    property_type: str              # e.g. "SFH"
    items: List[ChecklistItem] = field(default_factory=list)
    source_version: str = "unknown"

    def by_id(self) -> Dict[str, ChecklistItem]:
        return {it.id: it for it in self.items}

    def ids(self) -> List[str]:
        return [it.id for it in self.items]


def _jurisdiction_tokens(jurisdiction_path: str) -> Tuple[str, List[str]]:
    """
    Split "CA:santa_clara:san_jose" into ("CA", [progressively-specific muni paths]).
    Returns the state code and the list of municipal keys to apply, least->most specific.
    """
    parts = [p for p in jurisdiction_path.split(":") if p]
    state = parts[0].upper() if parts else "*"
    muni_keys: List[str] = []
    for i in range(2, len(parts) + 1):
        muni_keys.append(":".join([parts[0].upper()] + parts[1:i]))
    # muni_keys for CA:santa_clara:san_jose -> ["CA:santa_clara", "CA:santa_clara:san_jose"]
    return state, muni_keys


def _applies(item: ChecklistItem, state: str, property_type: str) -> bool:
    js = item.jurisdictions()
    pts = item.property_types()
    juris_ok = ("*" in js) or (state in js) or any(j.split(":")[0].upper() == state for j in js if ":" in j)
    type_ok = ("*" in pts) or (property_type in pts)
    return juris_ok and type_ok


def _apply_refine(target: ChecklistItem, patch: Dict[str, Any], layer_label: str) -> None:
    """Apply a single refine patch to an item in place (field-level merge)."""
    setvals = patch.get("set", patch)  # support {target, set:{...}} or flat dict
    for key, val in setvals.items():
        if key in ("target", "note", "verify"):
            continue
        if key in _SCALAR_FIELDS:
            setattr(target, key, val)
        elif key in _LIST_FIELDS:
            cur = list(getattr(target, key, []) or [])
            for v in (val or []):
                if v not in cur:
                    cur.append(v)
            setattr(target, key, cur)
        elif key == "applicability":
            # union jurisdictions + property_types (broaden, never narrow via overlay)
            cur = copy.deepcopy(target.applicability) or {}
            for axis in ("jurisdictions", "property_types"):
                merged = list(cur.get(axis, []))
                for v in (val.get(axis, []) if isinstance(val, dict) else []):
                    if v not in merged:
                        merged.append(v)
                cur[axis] = merged
            target.applicability = cur
        elif key == "compliance_basis":
            # last-wins on basis, but append citation/reasoning provenance
            cur = copy.deepcopy(target.compliance_basis) or {}
            newb = dict(val or {})
            if "basis" in newb:
                cur["basis"] = newb["basis"]
            for prov in ("citation", "reasoning"):
                if prov in newb:
                    existing = cur.get(prov)
                    cur[prov] = f"{existing} | {newb[prov]}" if existing else newb[prov]
            target.compliance_basis = cur
        else:
            # unknown scalar-ish field: last-wins, but never delete
            setattr(target, key, val)
    target.refined_by.append(layer_label)


def _refine_targets(overlay: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(overlay.get("refines") or [])


def _add_items(overlay: Dict[str, Any], layer_label: str) -> List[ChecklistItem]:
    from .checklist_loader import _coerce_item
    return [_coerce_item(d, layer_label, validate=False) for d in (overlay.get("adds") or [])]


def _assert_no_silent_delete(overlay: Dict[str, Any], layer_label: str) -> None:
    # An overlay expresses removal only via the base item's applicability, never
    # by a delete directive. If a 'deprecated'/'remove'/'delete' key tries to drop
    # an item without a superseded_by mapping, that's an illegal silent delete.
    for key in ("remove", "delete"):
        if overlay.get(key):
            raise CompositionError(
                f"{layer_label}: '{key}' is not permitted — overlays cannot delete "
                f"base items (no-silent-delete invariant). Use applicability instead."
            )
    # 'deprecated' is allowed ONLY when it records superseded_by (a visible split,
    # not a silent drop) — and even then it does not remove anything at compose time.


def compose(
    jurisdiction_path: str,
    property_type: str,
    asset: Optional[ChecklistAsset] = None,
) -> ResolvedChecklist:
    """
    Compose the resolved checklist for a property.

    jurisdiction_path : "*" | "CA" | "CA:santa_clara:san_jose"
    property_type      : "SFH" | "condo" | "townhouse" | ...
    """
    asset = asset or load_checklist()
    state, muni_keys = _jurisdiction_tokens(jurisdiction_path)

    # 1) start from a deep copy of the national base
    items: Dict[str, ChecklistItem] = {
        it.id: copy.deepcopy(it) for it in asset.national_base
    }

    # 2) apply the state overlay (adds + refines), if one exists for this state
    state_ov = asset.state_overlays.get(state)
    if state_ov:
        _assert_no_silent_delete(state_ov, f"state_overlay:{state}")
        for new_it in _add_items(state_ov, f"state_overlay:{state}"):
            items[new_it.id] = new_it
        for ref in _refine_targets(state_ov):
            tid = ref.get("target")
            if tid in items:
                _apply_refine(items[tid], ref, f"state_overlay:{state}")
            # refine targeting an absent id is a no-op (overlay may precede an add elsewhere)

    # 3) apply municipal overlays, least->most specific
    for mk in muni_keys:
        muni_ov = asset.municipal_overlays.get(mk)
        if not muni_ov:
            continue
        _assert_no_silent_delete(muni_ov, f"municipal_overlay:{mk}")
        for new_it in _add_items(muni_ov, f"municipal_overlay:{mk}"):
            items[new_it.id] = new_it
        for ref in _refine_targets(muni_ov):
            tid = ref.get("target")
            if tid in items:
                _apply_refine(items[tid], ref, f"municipal_overlay:{mk}")

    # 4) filter by applicability to this jurisdiction + property type
    resolved = [it for it in items.values() if _applies(it, state, property_type)]
    resolved.sort(key=lambda it: (it.group, it.id))

    return ResolvedChecklist(
        jurisdiction_path=jurisdiction_path,
        property_type=property_type,
        items=resolved,
        source_version=asset.version,
    )


def jurisdiction_coverage(asset: Optional[ChecklistAsset] = None) -> dict:
    """Honest footprint: which jurisdictions have authored depth beyond the
    national base. Every US state gets national_base; only the states/munis
    listed here have overlays. Used to surface where coverage is real vs the
    national floor (so nobody assumes CA-level depth exists everywhere)."""
    asset = asset or load_checklist()
    return {
        "national_base_items": len(asset.national_base),
        "state_overlays": sorted(asset.state_overlays.keys()),
        "municipal_overlays": sorted(asset.municipal_overlays.keys()),
    }
