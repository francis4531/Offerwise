"""
Issue derivation engine (Phase 1c, Reasoning Architecture Q-5.4 / Q-5.6).

Turns resolved Claims into Tier-3 Issues by re-clustering on the DECISION axis
(what the buyer should do) rather than the topical axis (what the property is).
Pure logic over Claim-like inputs + the resolved checklist — no DB, no network,
no live-path touch. Runs parallel to the existing risk_scoring_model.py, which
it supersedes only later (build-plan Phase 4).

Pipeline:
  1. assign each concern-Claim a decision_class from its checklist-item fields
  2. cluster Claims into Issues on (decision_class [, remedy scope])
  3. roll up severity (max + cluster escalation), aggregate cost, OR disclosure-risk
  4. emit the two-output offer handoff: estimable price-adjustment basis (the
     negotiation levers) vs a SEPARATE buyer-held reserve contingency (the
     due-diligence items). pre_close items are cure-before-close (weak lever);
     silent hazards are surfaced prominently regardless of class.

decision_class assignment is a documented v0.1 heuristic over the §3.5 anatomy
(which the architecture's offerwise_judgment basis permits); it is refinable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DECISION_CLASSES = (
    "pre_close_required_action",
    "negotiation_lever",
    "due_diligence_and_reserve",
    "silent_hazard",
)

_SEVERITY_ORDER = ["minor", "moderate", "major", "critical"]


def _sev_idx(s: str) -> int:
    return _SEVERITY_ORDER.index(s) if s in _SEVERITY_ORDER else 1


def _escalate(sev: str, n_high: int, threshold: int = 3) -> str:
    """Escalate one band ONLY when several genuinely high-severity (major+) items
    stack in one system — not merely because the cluster has >=N items at its own
    max (which over-inflates moderate clusters to critical). n_high counts items
    at 'major' or above."""
    i = _sev_idx(sev)
    # don't escalate moderate/minor clusters; only a real pile-up of major+ bumps
    if sev in ("major", "critical") and n_high >= threshold and i < len(_SEVERITY_ORDER) - 1:
        i += 1
    return _SEVERITY_ORDER[i]


@dataclass
class DerivedIssue:
    decision_class: str
    title: str
    severity: str
    silent_hazard_flag: bool
    is_reserve: bool
    claim_item_ids: List[str] = field(default_factory=list)
    cost_band_low: Optional[float] = None
    cost_band_high: Optional[float] = None
    disclosure_risk: bool = False
    group: str = "general"
    # disclosure cross-reference status vs the seller's TDS/SPQ for this concern:
    # 'corroborated'        — seller disclosed it AND the inspection confirms it
    # 'contradiction'       — seller answered clean/no, the inspection found it
    # 'undisclosed'         — the inspection found it, the seller said nothing
    #                         (incl. items a TDS structurally can't cover, e.g. panel brand)
    # 'disclosed_not_found' — seller disclosed it, the inspection didn't independently find it
    disclosure_status: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = self.__dict__.copy()
        return d


@dataclass
class OfferHandoff:
    # estimable, defensible price-adjustment basis (negotiation levers)
    price_adjustment_low: float = 0.0
    price_adjustment_high: float = 0.0
    price_adjustment_issue_titles: List[str] = field(default_factory=list)
    # SEPARATE buyer-held reserve contingency (NOT deducted from the bid)
    reserve_issue_titles: List[str] = field(default_factory=list)
    # legally-required cures before close (weak/zero price lever)
    pre_close_action_titles: List[str] = field(default_factory=list)
    # surfaced prominently regardless of class
    silent_hazard_titles: List[str] = field(default_factory=list)
    # the moat list: concerns the seller did NOT disclose (undisclosed) or
    # actively answered clean on (contradiction) — "what the seller didn't tell you"
    undisclosed_titles: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class IssueDerivationResult:
    issues: List[DerivedIssue]
    offer: OfferHandoff

    def to_dict(self) -> Dict[str, Any]:
        return {"issues": [i.to_dict() for i in self.issues], "offer": self.offer.to_dict()}


def _item_fields(item: Any) -> Dict[str, Any]:
    """Pull the decision-relevant §3.5 fields from a ChecklistItem or dict."""
    if isinstance(item, dict):
        cb = item.get("compliance_basis", {}) or {}
        return {
            "group": item.get("group", "general"),
            "importance": item.get("importance", "supporting"),
            "cost_impact": item.get("cost_impact", "no"),
            "disclosure_obligation_state": item.get("disclosure_obligation_state", "not_required"),
            "severity_when_negative": item.get("severity_when_negative", "moderate"),
            "unanswered_implication": item.get("unanswered_implication", "minor"),
            "basis": cb.get("basis", "best_practice"),
        }
    cb = getattr(item, "compliance_basis", {}) or {}
    return {
        "group": getattr(item, "group", "general"),
        "importance": getattr(item, "importance", "supporting"),
        "cost_impact": getattr(item, "cost_impact", "no"),
        "disclosure_obligation_state": getattr(item, "disclosure_obligation_state", "not_required"),
        "severity_when_negative": getattr(item, "severity_when_negative", "moderate"),
        "unanswered_implication": getattr(item, "unanswered_implication", "minor"),
        "basis": cb.get("basis", "best_practice"),
    }


def assign_decision_class(item: Any) -> Dict[str, Any]:
    """
    Map a checklist item's §3.5 fields to a decision_class + silent_hazard flag.

    v0.1 heuristic (documented, refinable):
      legal_requirement + low cost   -> pre_close_required_action (legal cure)
      estimable cost (cost_impact=yes)-> negotiation_lever (defensible $ basis)
      specialist_required / unknown   -> due_diligence_and_reserve (hold reserve)
      else                            -> due_diligence_and_reserve (conservative)
    silent_hazard flag = critical/major importance, not legally required to
      disclose, and not a legal_requirement item (structurally form-invisible).
    A pure silent hazard with no estimable action becomes class silent_hazard.
    """
    f = _item_fields(item)
    basis, cost, dos = f["basis"], f["cost_impact"], f["disclosure_obligation_state"]
    importance, unanswered = f["importance"], f["unanswered_implication"]

    if basis == "legal_requirement" and cost in ("no", "indirect"):
        dc = "pre_close_required_action"
    elif cost == "yes":
        dc = "negotiation_lever"
    elif unanswered == "specialist_required":
        dc = "due_diligence_and_reserve"
    else:
        dc = "due_diligence_and_reserve"

    silent_hazard = (
        importance in ("critical", "major")
        and dos == "not_required"
        and basis != "legal_requirement"
    )
    # silent_hazard is primarily a FLAG that can decorate any class. It becomes a
    # class of its OWN only for a pure case: a flagged item with no actionable
    # decision (no estimable cost, no specialist/reserve path) — i.e. the default
    # fell through with nothing else to do about it.
    if silent_hazard and dc == "due_diligence_and_reserve" and unanswered != "specialist_required":
        dc = "silent_hazard"

    return {"decision_class": dc, "silent_hazard_flag": silent_hazard,
            "severity": f["severity_when_negative"], "group": f["group"],
            "disclosure_required": dos == "required"}


def _cluster_key(dc: str, group: str, disclosure_status: Optional[str] = None) -> tuple:
    # negotiation levers and pre-close cures cluster by (class, group, disclosure):
    # a "seller disclosed it" finding and a "seller hid it" finding in the SAME
    # system are different negotiation stories and must stay separate issues (so a
    # disclosed shower leak can't mask an undisclosed kitchen leak). reserve and
    # silent hazards cluster by class only (one "hold money / verify" decision
    # regardless of topic).
    if dc in ("negotiation_lever", "pre_close_required_action"):
        return (dc, group, disclosure_status)
    return (dc,)


_TITLES = {
    "pre_close_required_action": "Required pre-close action",
    "negotiation_lever": "Negotiation item",
    "due_diligence_and_reserve": "Due-diligence reserve",
    "silent_hazard": "Silent hazard",
}

# Human-readable group labels — buyers never see raw enum/underscore names.
_GROUP_LABELS = {
    "foundation_structure": "Foundation & structure",
    "foundation": "Foundation",
    "roof_exterior": "Roof & exterior",
    "roof": "Roof",
    "plumbing": "Plumbing",
    "plumbing_water": "Plumbing & water",
    "electrical": "Electrical",
    "electrical_fire": "Electrical & fire safety",
    "hvac": "Heating & cooling",
    "heating": "Heating",
    "environmental": "Environmental hazards",
    "water_damage": "Water & moisture",
    "pest": "Pest & wood-destroying organisms",
    "safety": "Safety",
    "permits": "Permits & legal",
    "site": "Site & drainage",
    "structure": "Structure",
    "water_heater": "Water heater",
    "general": "Other",
}


def _group_label(group: str) -> str:
    return _GROUP_LABELS.get(group, group.replace("_", " ").strip().capitalize())


def _human_title(dc: str, group: str) -> str:
    """Buyer-facing, complete-phrase titles — never raw enum names or fragments."""
    g = _group_label(group)
    if dc == "negotiation_lever":
        return f"{g}: negotiate a price adjustment"
    if dc == "pre_close_required_action":
        return f"{g}: must be resolved before closing"
    if dc == "due_diligence_and_reserve":
        return "Set aside a reserve for further investigation"
    if dc == "silent_hazard":
        return f"{g}: hidden risk a disclosure wouldn't reveal"
    return g


# Disclosure cross-reference: rank by negotiation leverage (most leverage first).
_DISCLOSURE_PRIORITY = {
    "contradiction": 3, "undisclosed": 2, "corroborated": 1, "disclosed_not_found": 0,
}


def _claim_field(c, name):
    return getattr(c, name, None) if not isinstance(c, dict) else c.get(name)


def _disclosure_status_by_item(claims: List[Any]) -> Dict[str, str]:
    """Compare what the SELLER said (TDS/SPQ claims) against what the INSPECTION
    found, per checklist item, to label each concern. This is the moat: it turns
    "a finding" into "the seller disclosed X / said nothing / said it was fine."

    A claim is inspection-sourced when source_form starts with 'INSPECTION';
    anything else (TDS, SPQ, NHD, ...) is a disclosure source. polarity
    'contradicts' = a concern is present; 'supports' = checked clean.
    """
    insp_concern: set = set()
    disc_concern: set = set()
    disc_clean: set = set()
    for c in claims:
        iid = _claim_field(c, "checklist_item_id")
        if not iid:
            continue
        polarity = _claim_field(c, "polarity")
        src = (_claim_field(c, "source_form") or "").upper()
        is_inspection = src.startswith("INSPECTION")
        if polarity == "contradicts":
            (insp_concern if is_inspection else disc_concern).add(iid)
        elif polarity == "supports" and not is_inspection:
            disc_clean.add(iid)

    status: Dict[str, str] = {}
    for iid in insp_concern:
        if iid in disc_concern:
            status[iid] = "corroborated"
        elif iid in disc_clean:
            status[iid] = "contradiction"
        else:
            status[iid] = "undisclosed"
    for iid in disc_concern - insp_concern:
        status[iid] = "disclosed_not_found"
    return status


def derive_issues(claims: List[Any], checklist_by_id: Dict[str, Any]) -> IssueDerivationResult:
    """
    claims: Claim-like objects/dicts with .checklist_item_id and .polarity
            (only polarity == 'contradicts' — a concern present — feeds Issues).
    checklist_by_id: {item_id: ChecklistItem} from the resolved checklist.
    """
    dstat = _disclosure_status_by_item(claims)
    clusters: Dict[tuple, Dict[str, Any]] = {}

    for c in claims:
        iid = getattr(c, "checklist_item_id", None) or (c.get("checklist_item_id") if isinstance(c, dict) else None)
        polarity = getattr(c, "polarity", None) or (c.get("polarity") if isinstance(c, dict) else None)
        if not iid or polarity != "contradicts":
            continue  # 'supports' = clean; not an Issue
        item = checklist_by_id.get(iid)
        if item is None:
            continue
        a = assign_decision_class(item)
        key = _cluster_key(a["decision_class"], a["group"], dstat.get(iid))
        cl = clusters.setdefault(key, {
            "decision_class": a["decision_class"], "group": a["group"],
            "items": [], "severities": [], "silent": False, "disclosure_risk": False,
        })
        if iid not in cl["items"]:
            cl["items"].append(iid)
            cl["severities"].append(a["severity"])
        cl["silent"] = cl["silent"] or a["silent_hazard_flag"]
        cl["disclosure_risk"] = cl["disclosure_risk"] or a["disclosure_required"]

    issues: List[DerivedIssue] = []
    for key, cl in clusters.items():
        sevs = cl["severities"]
        max_sev = max(sevs, key=_sev_idx) if sevs else "moderate"
        # count DISTINCT systems carrying a major+ finding — not raw item count
        # (multiple findings in one system are one repair, not a pile-up).
        high_groups = {
            (checklist_by_id.get(iid, {}).get("group")
             if isinstance(checklist_by_id.get(iid), dict)
             else getattr(checklist_by_id.get(iid, None), "group", None)) or cl["group"]
            for iid, s in zip(cl["items"], sevs) if _sev_idx(s) >= _sev_idx("major")
        }
        rolled = _escalate(max_sev, len(high_groups))
        dc = cl["decision_class"]
        title = _human_title(dc, cl["group"])
        # cluster disclosure status = highest-leverage status among its items
        statuses = [dstat.get(iid) for iid in cl["items"] if dstat.get(iid)]
        cluster_status = (
            max(statuses, key=lambda s: _DISCLOSURE_PRIORITY.get(s, -1))
            if statuses else None
        )
        issues.append(DerivedIssue(
            decision_class=dc,
            title=title,
            severity=rolled,
            silent_hazard_flag=cl["silent"],
            is_reserve=(dc == "due_diligence_and_reserve"),
            claim_item_ids=cl["items"],
            disclosure_risk=cl["disclosure_risk"],
            group=cl["group"],
            disclosure_status=cluster_status,
        ))

    return IssueDerivationResult(issues=issues, offer=_build_offer(issues))


def _build_offer(issues: List[DerivedIssue]) -> OfferHandoff:
    offer = OfferHandoff()
    for iss in issues:
        if iss.silent_hazard_flag or iss.decision_class == "silent_hazard":
            offer.silent_hazard_titles.append(iss.title)
        # the moat: what the seller did not tell you
        if iss.disclosure_status in ("undisclosed", "contradiction"):
            offer.undisclosed_titles.append(iss.title)
        if iss.decision_class == "negotiation_lever":
            offer.price_adjustment_issue_titles.append(iss.title)
            offer.price_adjustment_low += iss.cost_band_low or 0.0
            offer.price_adjustment_high += iss.cost_band_high or 0.0
        elif iss.decision_class == "due_diligence_and_reserve":
            offer.reserve_issue_titles.append(iss.title)   # SEPARATE, not deducted
        elif iss.decision_class == "pre_close_required_action":
            offer.pre_close_action_titles.append(iss.title)  # cure, weak lever
    return offer
