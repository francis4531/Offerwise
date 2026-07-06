"""
test_shadow_findings.py — v5.89.266. Shadow comparisons now persist the actual
finding TITLES each engine surfaced (not just counts), so an admin can eyeball
reasoning-vs-live wins per state before flipping the buyer-facing flag. Locks the
capture: only the wins that matter (contradiction / undisclosed / silent hazard),
capped, corroborated excluded, and JSON serialization.
"""
import json
from reasoning_shadow import build_comparison, _json_findings


class Iss:
    def __init__(self, title, status, silent=False):
        self.title = title; self.disclosure_status = status
        self.silent_hazard_flag = silent; self.decision_class = ''


class Match:
    def __init__(self, explanation): self.explanation = explanation


class Live:
    def __init__(self, contra, undis):
        self.contradictions = [Match(x) for x in contra]
        self.undisclosed_issues = [Match(x) for x in undis]


def test_captures_reasoning_wins_and_excludes_corroborated():
    issues = [Iss('Master bath leak', 'contradiction'),
              Iss('Kitchen leak', 'undisclosed'),
              Iss('Public sewer confirmed', 'corroborated'),   # NOT a win — excluded
              Iss('FPE panel', 'unknown', silent=True)]        # silent hazard — included
    comp = build_comparison(Live([], []), issues, None, 10, True, 5)
    titles = [f['title'] for f in comp['reasoning_findings']]
    assert 'Master bath leak' in titles
    assert 'Kitchen leak' in titles
    assert 'FPE panel' in titles
    assert 'Public sewer confirmed' not in titles  # corroborated is not a "win"
    # silent hazard with no contra/undis status is labeled silent_hazard
    fpe = [f for f in comp['reasoning_findings'] if f['title'] == 'FPE panel'][0]
    assert fpe['status'] == 'silent_hazard'


def test_captures_live_findings_both_kinds():
    comp = build_comparison(Live(['seller said no leak, inspection found active leak'],
                                 ['FPE panel undisclosed']), [], None, 10, True, 5)
    kinds = {f['type'] for f in comp['live_findings']}
    assert kinds == {'contradiction', 'undisclosed'}


def test_findings_capped_at_five():
    issues = [Iss(f'issue {i}', 'undisclosed') for i in range(20)]
    comp = build_comparison(Live([], []), issues, None, 10, True, 5)
    assert len(comp['reasoning_findings']) == 5


def test_json_findings_roundtrips_and_is_none_when_empty():
    comp = build_comparison(Live([], []), [Iss('X', 'contradiction')], None, 1, True, 0)
    payload = json.loads(_json_findings(comp))
    assert payload['reasoning'][0]['title'] == 'X'
    # nothing to show -> None (old rows / empty comparisons stay null)
    empty = build_comparison(Live([], []), [Iss('ok', 'corroborated')], None, 1, True, 0)
    assert _json_findings(empty) is None
