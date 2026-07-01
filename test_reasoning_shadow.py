"""
test_reasoning_shadow.py — the reasoning shadow harness.

Covers the pure comparison logic, jurisdiction inference, and the safety
invariant that a shadow run can never raise (so it can never break the live
analysis). Extraction/pipeline require an API key and are validated on staging.
"""

from reasoning_shadow import build_comparison, _infer_jurisdiction, run_reasoning_shadow


class _Live:
    def __init__(self, contradictions, undisclosed):
        self.contradictions = contradictions
        self.undisclosed_issues = undisclosed


class _Issue:
    def __init__(self, silent=False, disclosure="undisclosed"):
        self.silent_hazard_flag = silent
        self.decision_class = "silent_hazard" if silent else "negotiation_lever"
        self.disclosure_status = disclosure


class _Offer:
    price_adjustment_low = 12000
    price_adjustment_high = 21000


def test_infer_jurisdiction_from_address():
    assert _infer_jurisdiction("2839 Pendleton Dr, San Jose, CA 95148") == "CA"
    assert _infer_jurisdiction("100 Main St, Austin, TX 78701") == "TX"
    assert _infer_jurisdiction(None) == "CA"          # launch default
    assert _infer_jurisdiction("no state here") == "CA"


def test_build_comparison_counts():
    live = _Live([{"x": 1}, {"x": 2}], [{"y": 1}])
    issues = [_Issue(silent=True), _Issue(silent=False, disclosure="corroborated"),
              _Issue(silent=False, disclosure="undisclosed")]
    comp = build_comparison(live, issues, _Offer(), extractor_readings=9, extractor_ok=True)
    assert comp["live_contradictions"] == 2
    assert comp["live_undisclosed"] == 1
    assert comp["reasoning_issues"] == 3
    assert comp["reasoning_silent_hazards"] == 1
    assert comp["reasoning_undisclosed"] == 2   # 1 undisclosed + 1 contradiction
    assert comp["reasoning_offer_low"] == 12000
    assert comp["extractor_ok"] is True
    assert "reasoning: 3 issues" in comp["notes"]


def test_build_comparison_flags_extractor_failure():
    comp = build_comparison(_Live([], []), [], None, extractor_readings=0, extractor_ok=False)
    assert comp["extractor_ok"] is False
    assert "EXTRACTOR FAILED" in comp["notes"]
    assert comp["reasoning_offer_low"] == 0


def test_shadow_never_raises_on_bad_input():
    # No app context, junk inputs — must return a dict and not raise.
    out = run_reasoning_shadow(
        inspection_text=None, disclosure_text=None,
        property_address=None, property_price=0, live_cross_ref=None,
        analysis_id=None,
    )
    assert isinstance(out, dict)
    assert "ok" in out


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
