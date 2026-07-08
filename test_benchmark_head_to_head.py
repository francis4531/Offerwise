"""
test_benchmark_head_to_head.py — v5.89.271. The honest head-to-head must score by
OBJECTIVE answer-key recall (so it can't be gamed) and the reasoning side must
actually catch the Pendleton findings the old engine missed. The raw-Claude side
needs the API and is not unit-tested here (it runs against Opus 4.8 on demand).
"""
import benchmark_head_to_head as b


def test_free_text_scoring_is_objective():
    strong = ("Master bath shower pan leak disclosed. Kitchen floor warping and "
              "rim-joist moisture undisclosed. Federal Pacific Stab-Lok panel with "
              "aluminum wiring. Furnace flue detached, carbon monoxide risk. "
              "Galvanized supply pipe corrosion, active leak. Asbestos pre-1978. "
              "Public sewer confirmed.")
    s = b.score_free_text(strong)
    assert s["core_recall"] == 1.0 and set(s["core_caught"]) == {"1", "2", "3", "3b", "6", "C"}


def test_free_text_scoring_zero_on_generic():
    s = b.score_free_text("The home is in good condition with minor cosmetic wear.")
    assert s["core_recall"] == 0.0 and s["core_caught"] == []


def test_partial_recall_scores_proportionally():
    # catches FPE + asbestos only -> 2 of 6 core
    s = b.score_free_text("Federal Pacific panel is a fire hazard. Asbestos noted.")
    assert set(s["core_caught"]) == {"3", "C"}
    assert abs(s["core_recall"] - round(2/6, 3)) < 1e-6


def test_reasoning_side_catches_all_six_core_findings():
    # deterministic — the reasoning engine on the Pendleton canonical readings must
    # surface all six core findings (this is the case the OLD engine lost).
    r = b.reasoning_side()
    assert r["core_recall"] == 1.0
    assert set(r["core_caught"]) == {"1", "2", "3", "3b", "6", "C"}


def test_head_to_head_without_client_runs_reasoning_only():
    out = b.head_to_head(client=None)
    assert out["reasoning"]["core_recall"] == 1.0
    assert "skipped" in out["raw_claude"]        # no API -> raw side skipped, not faked
    assert "verdict" not in out                  # no verdict without both sides


def test_benchmark_endpoint_returns_reasoning_side(monkeypatch):
    """Real smoke test of POST /api/admin/benchmark/pendleton — with no API key the
    endpoint runs the deterministic reasoning side and skips (does not fake) the raw
    side. Also gives the API-coverage gate a genuine reference to this route."""
    import os
    from flask import Flask
    import admin_routes
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)   # no key -> no real API call
    admin_routes._api_admin_required = (lambda f: f)          # pass-through admin gate
    admin_routes._is_admin = (lambda: True)
    app = Flask(__name__)
    app.add_url_rule('/api/admin/benchmark/pendleton', 'bench',
                     admin_routes.api_benchmark_pendleton, methods=['POST'])
    r = app.test_client().post('/api/admin/benchmark/pendleton')
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok'] is True
    assert d['reasoning']['core_recall'] == 1.0
    assert 'skipped' in d['raw_claude']       # raw side skipped, never fabricated
    assert 'verdict' not in d
