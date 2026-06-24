"""Tests for the single source-of-truth funnel view (_funnel_view).

Pure-logic tests: given stage counts, the view must compute drop-offs only where
the denominator is meaningful, suppress rates under the volume bar, and derive
honest insights from the same numbers."""
import os
os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'ci')
os.environ.setdefault('DATABASE_URL', 'sqlite:///funnel_test.db')

from app import _funnel_view, _RATE_MIN_DENOM, _CANON_FUNNEL_STAGES


def test_high_volume_computes_drop_pct():
    counts = {'visit': 1000, 'risk_check_start': 400, 'risk_check_complete': 200}
    v = _funnel_view(counts)
    by = {s['stage']: s for s in v['stages']}
    assert by['risk_check_start']['drop_pct'] == 60.0      # 400/1000
    assert by['risk_check_start']['rate_suppressed'] is False
    assert by['risk_check_complete']['drop_pct'] == 50.0   # 200/400
    assert v['low_volume'] is False


def test_low_volume_suppresses_rates():
    # prior stage below the bar -> no percentage, flagged suppressed
    counts = {'visit': 50, 'risk_check_start': 10}
    v = _funnel_view(counts)
    by = {s['stage']: s for s in v['stages']}
    assert by['risk_check_start']['drop_pct'] is None
    assert by['risk_check_start']['rate_suppressed'] is True
    assert v['low_volume'] is True


def test_suppression_is_per_step_on_prior_denominator():
    # visit clears the bar (rate ok), but a later thin stage suppresses the next
    counts = {'visit': 500, 'risk_check_start': 60, 'risk_check_complete': 20}
    v = _funnel_view(counts)
    by = {s['stage']: s for s in v['stages']}
    assert by['risk_check_start']['drop_pct'] == 88.0       # prior 500 >= 100
    assert by['risk_check_complete']['drop_pct'] is None     # prior 60 < 100
    assert by['risk_check_complete']['rate_suppressed'] is True


def test_real_dashboard_numbers():
    # the actual shape from the screenshot: big top, near-zero conversion
    counts = {'visit': 2004, 'risk_check_start': 121, 'risk_check_complete': 51,
              'signup': 24, 'address_entered': 32, 'analysis_complete': 3,
              'pricing_view': 53, 'purchase': 1}
    v = _funnel_view(counts)
    assert v['low_volume'] is False                          # 2004 visits is real volume
    assert v['totals'] == {'visits': 2004, 'analyses': 3, 'purchases': 1}
    drop = next(i for i in v['insights'] if i['kind'] == 'drop')
    assert 'Visited' in drop['text'] and 'Started a risk check' in drop['text']
    assert '1883 lost' in drop['text']                      # 2004 - 121
    # conversion insight states counts, no low-volume caveat (top volume is fine)
    conv = next(i for i in v['insights'] if i['kind'] == 'conversion')
    assert '2004 visits' in conv['text'] and '3 analyses' in conv['text']
    assert 'read the counts' not in conv['text']


def test_low_volume_conversion_caveat():
    counts = {'visit': 40, 'analysis_complete': 1, 'purchase': 0}
    v = _funnel_view(counts)
    conv = next(i for i in v['insights'] if i['kind'] == 'conversion')
    assert 'read the counts' in conv['text']


def test_empty_is_safe():
    v = _funnel_view({})
    assert v['totals'] == {'visits': 0, 'analyses': 0, 'purchases': 0}
    assert v['low_volume'] is True
    # no biggest-drop insight when nothing moved
    assert not any(i['kind'] == 'drop' for i in v['insights'])
    assert len(v['stages']) == len(_CANON_FUNNEL_STAGES)


def test_rate_min_denom_is_sane():
    assert _RATE_MIN_DENOM >= 30  # rates under ~30 are always noise
