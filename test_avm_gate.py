"""
test_avm_gate.py — the AVM corroboration gate.

Pins the fix for the tight-but-fabricated failure mode: a single-source AVM that
diverges wildly from the independent comp median (or from asking when comps are
thin) must be SUPPRESSED, not shipped as a confident "% below market" claim.
Canonical case: a $1.59M AVM on a $900K house.
"""

from avm_gate import avm_is_corroborated, avm_is_comp_outlier, comp_median
from market_intelligence import (
    MarketIntelligenceEngine,
    apply_market_adjustment,
)


def _research(avm, comps_prices, asking):
    comps = [{"price": p, "status": "sold", "price_per_sqft": 0, "days_on_market": 20}
             for p in comps_prices]
    return {
        "tool_results": [
            {"tool_name": "rentcast", "status": "success",
             "data": {"avm_price": avm, "comparables": comps}},
        ],
    }


# --- the corroboration predicate -------------------------------------------

def test_avm_outlier_vs_comps_is_distrusted():
    trusted, reason = avm_is_corroborated(1_590_000, 900_000, 905_000, 4)
    assert trusted is False
    assert "comp median" in reason


def test_avm_near_comp_median_is_trusted():
    trusted, reason = avm_is_corroborated(920_000, 900_000, 905_000, 4)
    assert trusted is True
    assert reason == ""


def test_avm_outlier_vs_asking_when_comps_thin():
    # <3 comps: fall back to asking as a weak sanity check
    trusted, reason = avm_is_corroborated(1_590_000, 900_000, 0, 0)
    assert trusted is False
    assert "asking" in reason


def test_avm_near_asking_when_comps_thin_is_trusted():
    trusted, _ = avm_is_corroborated(950_000, 900_000, 0, 0)
    assert trusted is True


# --- end to end: suppression through from_research_data ---------------------

def test_pipeline_suppresses_the_1_59M_case():
    eng = MarketIntelligenceEngine()
    mi = eng.from_research_data(
        _research(avm=1_590_000, comps_prices=[890_000, 905_000, 915_000, 900_000],
                  asking=900_000),
        asking_price=900_000,
    )
    assert mi.avm_price == 0            # suppressed
    assert mi.avm_trusted is False
    assert mi.avm_price_raw == 1_590_000
    assert mi.avm_suppression_reason
    # temperature must NOT have been set from the bad AVM
    assert mi.market_temperature == "neutral"


def test_pipeline_keeps_a_corroborated_avm():
    eng = MarketIntelligenceEngine()
    mi = eng.from_research_data(
        _research(avm=920_000, comps_prices=[890_000, 905_000, 915_000, 900_000],
                  asking=900_000),
        asking_price=900_000,
    )
    assert mi.avm_price == 920_000
    assert mi.avm_trusted is True


# --- the offer rationale never fabricates a below-AVM claim -----------------

def test_offer_emits_no_below_avm_claim_when_suppressed():
    eng = MarketIntelligenceEngine()
    mi = eng.from_research_data(
        _research(avm=1_590_000, comps_prices=[890_000, 905_000, 915_000, 900_000],
                  asking=900_000),
        asking_price=900_000,
    )
    out = apply_market_adjustment(855_000, 900_000, mi)
    assert out["market_applied"] is True
    assert out["asking_vs_avm_pct"] == 0.0           # no AVM gap propagated
    assert "AVM" not in out["rationale"]             # no "% below the AVM of $X"
    assert out["avm_trusted"] is False
    # comps still provide honest positioning
    assert out["comp_median_price"] > 0


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    bad = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception as e:
            bad += 1; print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)


# --- source-side comp-outlier gate (closes the narrative hole) --------------

def test_source_gate_flags_comp_outlier():
    out, reason = avm_is_comp_outlier(1_590_000, 900_000, 4)
    assert out is True
    assert "comp median" in reason


def test_source_gate_defers_when_comps_thin():
    # no strong comp evidence -> not an outlier at the source (asking-aware gate decides)
    out, _ = avm_is_comp_outlier(1_590_000, 0, 0)
    assert out is False
    out2, _ = avm_is_comp_outlier(1_590_000, 900_000, 2)  # only 2 comps
    assert out2 is False


def test_source_gate_keeps_corroborated_avm():
    out, _ = avm_is_comp_outlier(920_000, 905_000, 4)
    assert out is False


def test_comp_median_helper():
    assert comp_median([900_000, 890_000, 915_000, 0, None]) == 900_000
    assert comp_median([]) == 0
