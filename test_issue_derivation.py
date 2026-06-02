"""
Unit tests for the Phase 1c issue derivation engine (Q-5.4 / Q-5.6).

Pure logic — no DB, no live path. Items are built as plain dicts so the tests
assert the engine's behavior independent of the live checklist content.
"""
from reasoning import derive_issues, assign_decision_class


def _item(group, importance, cost, dos, sev, unanswered, basis):
    return {
        "group": group, "importance": importance, "cost_impact": cost,
        "disclosure_obligation_state": dos, "severity_when_negative": sev,
        "unanswered_implication": unanswered, "compliance_basis": {"basis": basis},
    }


class _Claim:
    def __init__(self, item_id, polarity="contradicts"):
        self.checklist_item_id = item_id
        self.polarity = polarity


# ── decision_class assignment ──────────────────────────────────────────────

def test_legal_low_cost_is_pre_close():
    it = _item("general", "major", "no", "required", "major", "seller_should_disclose", "legal_requirement")
    a = assign_decision_class(it)
    assert a["decision_class"] == "pre_close_required_action"


def test_estimable_cost_is_negotiation_lever():
    it = _item("plumbing", "major", "yes", "required", "major", "specialist_required", "best_practice")
    a = assign_decision_class(it)
    assert a["decision_class"] == "negotiation_lever"


def test_specialist_unknown_is_reserve():
    it = _item("roof_exterior", "major", "no", "not_required", "major", "specialist_required", "offerwise_judgment")
    a = assign_decision_class(it)
    assert a["decision_class"] in ("due_diligence_and_reserve", "silent_hazard")


def test_silent_hazard_flag_and_pure_class():
    # critical/major, not required to disclose, not legal, no estimable cost,
    # and no specialist/reserve path -> pure silent_hazard class
    it = _item("electrical", "major", "indirect", "not_required", "major", "minor", "best_practice")
    a = assign_decision_class(it)
    assert a["silent_hazard_flag"] is True
    assert a["decision_class"] == "silent_hazard"


def test_silent_hazard_with_specialist_stays_reserve_with_flag():
    # a flagged item that needs specialist eval keeps its reserve decision + flag
    it = _item("electrical", "major", "indirect", "not_required", "major", "specialist_required", "best_practice")
    a = assign_decision_class(it)
    assert a["decision_class"] == "due_diligence_and_reserve"
    assert a["silent_hazard_flag"] is True


def test_silent_hazard_flag_can_coexist_with_negotiation():
    # silent hazard but estimable cost -> negotiation_lever flagged silent
    it = _item("electrical", "critical", "yes", "not_required", "critical", "specialist_required", "best_practice")
    a = assign_decision_class(it)
    assert a["decision_class"] == "negotiation_lever"
    assert a["silent_hazard_flag"] is True


# ── clustering ─────────────────────────────────────────────────────────────

def test_reserve_clusters_cross_topic_into_one_issue():
    by_id = {
        "roof.x": _item("roof_exterior", "major", "no", "not_required", "major", "specialist_required", "offerwise_judgment"),
        "crawl.y": _item("foundation_structure", "major", "no", "not_required", "major", "specialist_required", "offerwise_judgment"),
        "chimney.z": _item("roof_exterior", "moderate", "no", "not_required", "moderate", "specialist_required", "offerwise_judgment"),
    }
    claims = [_Claim("roof.x"), _Claim("crawl.y"), _Claim("chimney.z")]
    res = derive_issues(claims, by_id)
    reserve = [i for i in res.issues if i.decision_class == "due_diligence_and_reserve"]
    # all three cross-topic reserve claims merge into ONE reserve issue
    assert len(reserve) == 1
    assert set(reserve[0].claim_item_ids) == {"roof.x", "crawl.y", "chimney.z"}


def test_negotiation_splits_by_group():
    by_id = {
        "elec.a": _item("electrical", "major", "yes", "required", "major", "minor", "best_practice"),
        "plumb.b": _item("plumbing", "major", "yes", "required", "major", "minor", "best_practice"),
    }
    claims = [_Claim("elec.a"), _Claim("plumb.b")]
    res = derive_issues(claims, by_id)
    neg = [i for i in res.issues if i.decision_class == "negotiation_lever"]
    assert len(neg) == 2  # different remedy scopes -> different issues


def test_clean_claims_do_not_produce_issues():
    by_id = {"x.y": _item("plumbing", "major", "yes", "required", "major", "minor", "best_practice")}
    claims = [_Claim("x.y", polarity="supports")]  # clean reading
    res = derive_issues(claims, by_id)
    assert len(res.issues) == 0


# ── severity roll-up ───────────────────────────────────────────────────────

def test_moderate_cluster_does_not_escalate():
    # three moderate reserve claims must STAY moderate — clustering moderate
    # findings into 'critical' was the over-flagging bug; fixed.
    by_id = {f"r{i}": _item("g%d" % i, "major", "no", "not_required", "moderate", "specialist_required", "offerwise_judgment") for i in range(3)}
    claims = [_Claim(k) for k in by_id]
    res = derive_issues(claims, by_id)
    reserve = [i for i in res.issues if i.decision_class == "due_diligence_and_reserve"][0]
    assert reserve.severity == "moderate"  # moderate items do not inflate to major


def test_multiple_major_systems_escalate():
    # three MAJOR findings across three DISTINCT systems -> escalate one band
    by_id = {f"r{i}": _item("g%d" % i, "major", "no", "not_required", "major", "specialist_required", "offerwise_judgment") for i in range(3)}
    claims = [_Claim(k) for k in by_id]
    res = derive_issues(claims, by_id)
    reserve = [i for i in res.issues if i.decision_class == "due_diligence_and_reserve"][0]
    assert reserve.severity == "critical"  # 3 distinct major systems -> escalate


# ── offer handoff (two outputs) ────────────────────────────────────────────

def test_offer_handoff_separates_basis_from_reserve_and_cures():
    by_id = {
        "cure": _item("general", "major", "no", "required", "major", "minor", "legal_requirement"),
        "neg": _item("plumbing", "major", "yes", "required", "major", "minor", "best_practice"),
        "res": _item("roof_exterior", "major", "no", "not_required", "major", "specialist_required", "offerwise_judgment"),
    }
    claims = [_Claim("cure"), _Claim("neg"), _Claim("res")]
    res = derive_issues(claims, by_id)
    offer = res.offer
    # the negotiation lever is the price-adjustment basis (human title)
    assert any("Plumbing" in t and "negotiate" in t for t in offer.price_adjustment_issue_titles)
    # the reserve is SEPARATE, not in the price basis
    assert len(offer.reserve_issue_titles) == 1
    assert not any("reserve" in t.lower() for t in offer.price_adjustment_issue_titles)
    # the legal cure is a pre-close action, not a price deduction
    assert len(offer.pre_close_action_titles) == 1


def test_titles_are_human_readable():
    # titles must never expose raw enum names or underscore'd group ids
    by_id = {
        "a": _item("foundation_structure", "major", "yes", "required", "major", "minor", "best_practice"),
        "b": _item("roof_exterior", "major", "yes", "not_required", "major", "minor", "best_practice"),
    }
    res = derive_issues([_Claim("a"), _Claim("b")], by_id)
    for i in res.issues:
        assert "_" not in i.title, f"raw enum leaked into title: {i.title!r}"
        assert i.title[0].isupper()
        # the foundation item should read as a complete phrase
    titles = " ".join(i.title for i in res.issues)
    assert "Foundation & structure" in titles
