"""
Reasoning pipeline orchestrator (integration spine).

Wires the four built components into one runnable pipeline:

    field readings
      -> compose(jurisdiction, property_type)        # resolved checklist (Q-5.9)
      -> map_fields_to_claims(...)                    # deterministic Claims (1a)
      -> derive_issues(...)                           # Tier-3 Issues (1c)
      -> [optional] persist Finding/Claim/Issue rows  # 0b tables

ADDITIVE: this is a new callable. It is NOT yet invoked by the live analysis
flow — activating it for real analyses is a later, flagged step (1d). When
persist=False it is pure (no DB) and fully testable; when persist=True it writes
rows within a Flask app context.

A "field reading" is one standardized form/report value:
    {"item_id": "roof.leak_evidence", "value": "yes",
     "source_form": "TDS", "locator": "TDS B.1", "raw_text": "roof leaks: yes"}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .composition import compose
from .form_field_map import load_form_field_map, map_field_to_claim, DeterministicClaim
from .issue_derivation import derive_issues, IssueDerivationResult


@dataclass
class PipelineResult:
    jurisdiction: str
    property_type: str
    checklist_version: str
    resolved_item_count: int
    claims: List[DeterministicClaim] = field(default_factory=list)
    issues_result: Optional[IssueDerivationResult] = None
    persisted: Dict[str, int] = field(default_factory=dict)

    def summary(self) -> Dict[str, Any]:
        issues = self.issues_result.issues if self.issues_result else []
        offer = self.issues_result.offer if self.issues_result else None
        return {
            "jurisdiction": self.jurisdiction,
            "property_type": self.property_type,
            "checklist_version": self.checklist_version,
            "resolved_items": self.resolved_item_count,
            "claims": len(self.claims),
            "issues": len(issues),
            "price_basis_items": len(offer.price_adjustment_issue_titles) if offer else 0,
            "reserve_items": len(offer.reserve_issue_titles) if offer else 0,
            "pre_close_items": len(offer.pre_close_action_titles) if offer else 0,
            "silent_hazards": len(offer.silent_hazard_titles) if offer else 0,
            "persisted": self.persisted,
        }


def run_pipeline(
    field_readings: List[Dict[str, str]],
    jurisdiction: str,
    property_type: str,
    *,
    inspection_readings: Optional[List[Dict[str, Any]]] = None,
    persist: bool = False,
    analysis_id: Optional[int] = None,
    property_id: Optional[int] = None,
    zip_code: str = "",
    property_year_built: Optional[int] = None,
) -> PipelineResult:
    """
    Run the deterministic reasoning pipeline end to end.

    field_readings: deterministic form/report readings (TDS etc.).
    inspection_readings: readings from the inspection parser (the moat); each is
      {item_id, value ('yes'/'no'), severity, silent_hazard, ...}. These become
      Claims directly (the inspection states the finding; no form-field map).

    persist=False -> pure (no DB). persist=True -> also write Finding/Claim/Issue
    rows (requires an active Flask app context and the models DB).
    """
    resolved = compose(jurisdiction, property_type)
    resolved_ids = set(resolved.ids())
    by_id = resolved.by_id()
    fmap = load_form_field_map()

    # 1a: field readings -> deterministic Claims (gated to the resolved checklist)
    claims: List[DeterministicClaim] = []
    reading_by_item: Dict[str, Dict[str, str]] = {}
    for fr in field_readings:
        iid = fr.get("item_id", "")
        claim = map_field_to_claim(iid, fr.get("value", ""), fmap, resolved_item_ids=resolved_ids)
        if claim:
            claims.append(claim)
            reading_by_item[iid] = fr

    # Phase 3 (the moat): inspection readings -> Claims directly. The inspection
    # states the finding, so these don't go through the form-field map; they are
    # gated to the resolved checklist and carry the inspector's severity.
    for ir in (inspection_readings or []):
        iid = ir.get("item_id", "")
        if iid not in resolved_ids:
            continue
        val = ir.get("value", "")
        polarity = "contradicts" if val == "yes" else "supports"
        claims.append(DeterministicClaim(
            checklist_item_id=iid,
            resolved_value=ir.get("raw_text", val),
            polarity=polarity,
            resolution_state="answered",
            evidence_quality_confidence=0.9 if ir.get("corroborated_in_summary") else 0.8,
            inference_confidence=1.0,  # inspector observed it directly
            source_form="INSPECTION",
            source_locator=ir.get("locator"),
        ))
        reading_by_item[iid] = ir

    # 1c: Claims -> Issues (derive_issues consumes anything with item_id + polarity)
    issues_result = derive_issues(claims, by_id)

    # Real offer math: populate cost bands from the EXISTING repair cost
    # estimator (no second cost engine), then recompute the price-adjustment
    # basis. Pure/defensive — if the estimator is unavailable, bands stay None.
    try:
        from .cost_bands import populate_cost_bands
        issues_result = populate_cost_bands(
            issues_result, zip_code=zip_code, property_year_built=property_year_built,
            checklist_by_id=by_id,
        )
    except Exception:
        pass

    result = PipelineResult(
        jurisdiction=jurisdiction,
        property_type=property_type,
        checklist_version=resolved.source_version,
        resolved_item_count=len(resolved.items),
        claims=claims,
        issues_result=issues_result,
    )

    if persist:
        result.persisted = _persist(result, reading_by_item, analysis_id, property_id)

    return result


def _persist(
    result: PipelineResult,
    reading_by_item: Dict[str, Dict[str, str]],
    analysis_id: Optional[int],
    property_id: Optional[int],
) -> Dict[str, int]:
    """Write Finding/Claim/Issue rows + their links. Requires app context.

    Atomic: on ANY failure the session is rolled back (no partial/orphan rows)
    and the exception re-raised so the caller can fall back to a pure run.
    """
    from models import db, Finding, Claim, Issue

    finding_by_item: Dict[str, Any] = {}
    claim_by_item: Dict[str, Any] = {}

    try:
        for dc in result.claims:
            fr = reading_by_item.get(dc.checklist_item_id, {})
            finding = Finding(
                analysis_id=analysis_id, property_id=property_id,
                source_document=dc.source_form or fr.get("source_form"),
                source_quote=fr.get("raw_text"),
                raw_text=fr.get("raw_text") or f"{dc.checklist_item_id}={dc.resolved_value}",
                modality="structured_form_field",
                confidence=dc.evidence_quality_confidence,
            )
            claim = Claim(
                analysis_id=analysis_id, property_id=property_id,
                checklist_item_id=dc.checklist_item_id,
                checklist_version=result.checklist_version,
                resolved_value=dc.resolved_value,
                resolution_state=dc.resolution_state,
                polarity=dc.polarity,
                inference_confidence=dc.inference_confidence,
                evidence_quality_confidence=dc.evidence_quality_confidence,
            )
            db.session.add(finding)
            db.session.add(claim)
            db.session.flush()
            # link finding -> claim (supporting if it backs a concern, else neutral)
            claim.findings.append(finding)
            finding_by_item[dc.checklist_item_id] = finding
            claim_by_item[dc.checklist_item_id] = claim

        issues = result.issues_result.issues if result.issues_result else []
        issue_rows = []
        for di in issues:
            issue = Issue(
                analysis_id=analysis_id, property_id=property_id,
                decision_class=di.decision_class,
                silent_hazard_flag=di.silent_hazard_flag,
                severity=di.severity,
                cost_band_low=di.cost_band_low,
                cost_band_high=di.cost_band_high,
                is_reserve=di.is_reserve,
                title=di.title,
            )
            db.session.add(issue)
            db.session.flush()
            for iid in di.claim_item_ids:
                c = claim_by_item.get(iid)
                if c is not None:
                    issue.claims.append(c)
            issue_rows.append(issue)

        db.session.commit()
    except Exception:
        # leave NO partial rows behind; let the caller fall back to a pure run
        try:
            db.session.rollback()
        except Exception:
            pass
        raise

    return {
        "findings": len(finding_by_item),
        "claims": len(claim_by_item),
        "issues": len(issue_rows),
    }
