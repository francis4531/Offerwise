"""
Tests for cost band wiring (real offer math via the existing repair estimator).
"""
from reasoning.inspection_parser import parse_inspection_text, load_inspection_field_map
from reasoning import run_pipeline
from reasoning.issue_derivation import derive_issues
from reasoning.cost_bands import populate_cost_bands


PENDLETON_DETAIL = """HOME INSPECTION DETAILS
Electrical Service
3. Over Current Devices Federal Pacific. FPE brand panels with Stab-Lock circuit breakers.
5. Panel to Structure Copper/Aluminum. some of the electrical circuit wiring is aluminum.
Plumbing
6. Encrustations Evident Yes. encrustation on the galvanized water supply pipe.
9. Evidence of Leaks Yes. leak at the incoming water supply pipe.
Heating System
11. Vents / Flues Attention. furnace exhaust flue is partially detached in the garage. spent gasses.
"""


def _readings():
    return parse_inspection_text(PENDLETON_DETAIL, load_inspection_field_map())


def test_offer_basis_is_nonzero_with_costs():
    r = run_pipeline([], "CA", "SFH", inspection_readings=_readings(),
                     zip_code="95148", property_year_built=1977)
    off = r.issues_result.offer
    # the price-adjustment basis is now real money, not $0
    assert off.price_adjustment_high > 0
    assert off.price_adjustment_low > 0
    assert off.price_adjustment_high >= off.price_adjustment_low


def test_issues_get_cost_bands():
    r = run_pipeline([], "CA", "SFH", inspection_readings=_readings(),
                     zip_code="95148", property_year_built=1977)
    banded = [i for i in r.issues_result.issues if i.cost_band_high]
    assert len(banded) >= 1
    for i in banded:
        assert i.cost_band_low is not None
        assert i.cost_band_high >= i.cost_band_low


def test_zip_multiplier_applied():
    # San Jose (95148) should cost more than a 1.0-multiplier baseline ('')
    r_sj = run_pipeline([], "CA", "SFH", inspection_readings=_readings(), zip_code="95148")
    r_base = run_pipeline([], "CA", "SFH", inspection_readings=_readings(), zip_code="")
    assert r_sj.issues_result.offer.price_adjustment_high >= r_base.issues_result.offer.price_adjustment_high


def test_reserve_not_in_price_basis():
    r = run_pipeline([], "CA", "SFH", inspection_readings=_readings(),
                     zip_code="95148", property_year_built=1977)
    off = r.issues_result.offer
    # reserve issue titles must not appear among the price-adjustment basis
    assert not any(t in off.price_adjustment_issue_titles for t in off.reserve_issue_titles)


def test_graceful_when_estimator_unavailable(monkeypatch):
    # simulate the estimator import failing -> bands stay None, no raise
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "repair_cost_estimator":
            raise ImportError("simulated")
        return real_import(name, *a, **k)

    by_id = {"electrical.panel_brand_safety": {
        "group": "electrical", "importance": "major", "cost_impact": "yes",
        "disclosure_obligation_state": "not_required", "severity_when_negative": "major",
        "unanswered_implication": "specialist_required", "compliance_basis": {"basis": "best_practice"},
    }}
    class _C:
        def __init__(self, i): self.checklist_item_id = i; self.polarity = "contradicts"
    res = derive_issues([_C("electrical.panel_brand_safety")], by_id)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    out = populate_cost_bands(res, zip_code="95148")
    monkeypatch.setattr(builtins, "__import__", real_import)
    # no bands populated, offer basis stays 0, nothing raised
    assert out.offer.price_adjustment_high == 0
