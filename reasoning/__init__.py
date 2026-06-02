"""
OfferWise reasoning layer (Phase 0 foundations).

This package introduces the checklist-driven reasoning model specified in the
Reasoning Architecture (composition Q-5.9, grouping Q-5.5). It is ADDITIVE: it
does not modify or replace the existing runtime path in cross_reference_engine.py
/ document_parser.py. Those continue to serve the live product. The structured
pipeline built on this package supersedes them later (build plan Phase 4), once
it is validated end to end.

Phase 0 scope (this module set):
  - load + validate the versioned checklist asset (checklist_loader)
  - compose a resolved checklist for a given jurisdiction + property type,
    merging national_base -> state_overlay -> municipal_overlay (composition)

Persistence of Finding/Claim/Issue tiers is a separate, migration-backed
increment (Phase 0b) and intentionally not included here.
"""

from .checklist_loader import (
    ChecklistItem,
    ChecklistAsset,
    load_checklist,
    DEFAULT_CHECKLIST_PATH,
)
from .composition import (
    ResolvedChecklist,
    compose,
    CompositionError,
)
from .form_field_map import (
    FormFieldMap,
    DeterministicClaim,
    load_form_field_map,
    map_fields_to_claims,
    map_field_to_claim,
    DEFAULT_MAP_PATH,
)
from .issue_derivation import (
    DerivedIssue,
    OfferHandoff,
    IssueDerivationResult,
    derive_issues,
    assign_decision_class,
    DECISION_CLASSES,
)
from .pipeline import (
    run_pipeline,
    PipelineResult,
)
from .inspection_parser import (
    parse_inspection_text,
    load_inspection_field_map,
    load_inspection_specimen_findings,
    extract_inspection_readings,
)
from .inspection_llm_extractor import extract_inspection_findings_llm
from .cost_bands import populate_cost_bands
from .tds_parser import (
    parse_tds_field_state,
    load_tds_field_map,
    load_specimen_field_state,
)

__all__ = [
    "ChecklistItem",
    "ChecklistAsset",
    "load_checklist",
    "DEFAULT_CHECKLIST_PATH",
    "ResolvedChecklist",
    "compose",
    "CompositionError",
    "FormFieldMap",
    "DeterministicClaim",
    "load_form_field_map",
    "map_fields_to_claims",
    "map_field_to_claim",
    "DEFAULT_MAP_PATH",
    "DerivedIssue",
    "OfferHandoff",
    "IssueDerivationResult",
    "derive_issues",
    "assign_decision_class",
    "DECISION_CLASSES",
    "run_pipeline",
    "PipelineResult",
    "parse_tds_field_state",
    "load_tds_field_map",
    "load_specimen_field_state",
    "parse_inspection_text",
    "load_inspection_field_map",
    "load_inspection_specimen_findings",
    "extract_inspection_readings",
    "extract_inspection_findings_llm",
    "populate_cost_bands",
]
