"""
Cost band wiring (Phase: real offer math).

Populates DerivedIssue.cost_band_low/high using the EXISTING repair cost
estimator (repair_cost_estimator.estimate_repair_costs) — NOT a second cost
engine. Each issue maps to a repair category via its checklist group, and the
estimator already handles category/severity baselines, the ZIP metro multiplier,
and age adjustment.

After bands are populated, the offer handoff's price-adjustment basis is
recomputed from the negotiation-lever issues (the estimable ones). Reserve and
silent-hazard issues also receive bands (so the buyer-held reserve can show a
dollar figure) but remain OUT of the price-adjustment basis — consistent with
the two-output design (estimable price basis vs separate buyer-held reserve).
"""
from __future__ import annotations

from typing import Any, List, Optional

from .issue_derivation import IssueDerivationResult, DerivedIssue, OfferHandoff, _build_offer


def populate_cost_bands(
    result: IssueDerivationResult,
    *,
    zip_code: str = "",
    property_year_built: Optional[int] = None,
    checklist_by_id: Optional[dict] = None,
) -> IssueDerivationResult:
    """
    Fill cost_band_low/high on each issue via the existing estimator, then
    recompute the offer's price-adjustment dollar basis. Never raises — if the
    estimator is unavailable, issues keep None bands and the offer reads $0
    (honest absence rather than a fabricated number).

    Each issue is priced from its ACTUAL constituent items' groups (via
    checklist_by_id), so a cross-system cluster (e.g. an electrical+HVAC reserve)
    is costed as the sum of its real systems — not mis-priced as a single group.
    """
    try:
        from repair_cost_estimator import estimate_repair_costs
    except Exception:
        return result  # estimator unavailable; leave bands unpopulated

    def _item_group(iid: str) -> str:
        if checklist_by_id and iid in checklist_by_id:
            it = checklist_by_id[iid]
            g = it.get("group") if isinstance(it, dict) else getattr(it, "group", None)
            if g:
                return g
        return ""

    for issue in result.issues:
        try:
            # Collapse to DISTINCT systems: multiple findings in one system are
            # facets of one repair, not additive. One finding per group (at the
            # issue severity) -> a cross-system reserve sums its real systems,
            # while same-system items don't multiply.
            groups = []
            for iid in (issue.claim_item_ids or []):
                g = _item_group(iid) or issue.group
                if g not in groups:
                    groups.append(g)
            if not groups:
                groups = [issue.group]
            findings = [{"category": g, "severity": issue.severity,
                         "description": g} for g in groups]
            est = estimate_repair_costs(
                zip_code=zip_code or "",
                findings=findings,
                property_year_built=property_year_built,
            )
            total_low = est.get("total_low") or 0
            total_high = est.get("total_high") or 0
            if total_low or total_high:
                issue.cost_band_low = float(total_low)
                issue.cost_band_high = float(total_high)
        except Exception:
            # one issue failing must not break the rest
            continue

    # Recompute the offer now that bands are populated. Reuse _build_offer (the
    # single source of offer assembly) rather than a parallel copy — bands are
    # populated so its cost sums are correct, and this keeps the disclosure moat
    # list (undisclosed_titles) from drifting out, as a duplicate rebuild did.
    result.offer = _build_offer(result.issues)
    return result
