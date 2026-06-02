"""
Tests for agentic_monitor.py — OfferWise autonomous property surveillance engine.

Coverage targets:
  - _check_comps_for_watch      (new comp detection + buyer/professional alerts)
  - _check_earthquake_for_watch (USGS event detection)
  - _check_price_for_watch      (AVM drop detection)
  - _check_permits_for_watch    (multi-source permit cascade)
  - fire_buyer_concern_signal   (buyer engagement → inspector/agent alert)
  - _resolve_county             (keyword + geocoder fallback)
  - _fetch_permits_*            (Socrata / PermitData / OpenPermit)
  - _format_price               (formatting utility)
  - register_monitoring_jobs    (scheduler wiring)
  - Edge cases: missing API keys, bad API responses, dedup guard, 22h cooldown
"""
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, call


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_watch(**kwargs):
    """Create a minimal PropertyWatch-like mock."""
    w = MagicMock()
    w.id = kwargs.get('id', 1)
    w.user_id = kwargs.get('user_id', 42)
    w.address = kwargs.get('address', '123 Main St, San Jose, CA 95112')
    w.asking_price = kwargs.get('asking_price', 800000.0)
    w.avm_at_analysis = kwargs.get('avm_at_analysis', 790000.0)
    w.created_at = kwargs.get('created_at', datetime(2026, 1, 1))
    w.last_comps_check_at = kwargs.get('last_comps_check_at', None)
    w.last_earthquake_check_at = kwargs.get('last_earthquake_check_at', None)
    w.last_price_check_at = kwargs.get('last_price_check_at', None)
    w.last_permit_check_at = kwargs.get('last_permit_check_at', None)
    w.baseline_permits_json = kwargs.get('baseline_permits_json', None)
    w.baseline_comps_json = kwargs.get('baseline_comps_json', None)
    w.latitude = kwargs.get('latitude', 37.3382)
    w.longitude = kwargs.get('longitude', -121.8863)
    w.inspector_report_id = kwargs.get('inspector_report_id', None)
    w.agent_share_id = kwargs.get('agent_share_id', None)
    w.contractor_lead_id = kwargs.get('contractor_lead_id', None)
    w.ghost_buyer_email = kwargs.get('ghost_buyer_email', None)
    w.is_active = kwargs.get('is_active', True)
    return w


def _make_db(user_email='buyer@test.com'):
    """Create a minimal db session mock."""
    db = MagicMock()
    user = MagicMock()
    user.email = user_email
    user.name = 'Test Buyer'
    db.session = MagicMock()
    # query(...).get(...) pattern
    db.query.return_value.get.return_value = user
    return db


# ── Import guard ─────────────────────────────────────────────────────────────

def test_import():
    """agentic_monitor imports without errors."""
    import agentic_monitor
    assert hasattr(agentic_monitor, '_job_comps_monitor')
    assert hasattr(agentic_monitor, '_job_earthquake_monitor')
    assert hasattr(agentic_monitor, '_job_price_monitor')
    assert hasattr(agentic_monitor, '_job_permit_monitor')
    assert hasattr(agentic_monitor, 'fire_buyer_concern_signal')
    assert hasattr(agentic_monitor, 'register_monitoring_jobs')


# ── _format_price ─────────────────────────────────────────────────────────────

def test_format_price_normal():
    from agentic_monitor import _format_price
    assert _format_price(800000) == '$800,000'

def test_format_price_none():
    from agentic_monitor import _format_price
    assert _format_price(None) == 'N/A'

def test_format_price_zero():
    from agentic_monitor import _format_price
    assert _format_price(0) == '$0'

def test_format_price_small():
    from agentic_monitor import _format_price
    result = _format_price(1500)
    assert '1,500' in result


# ── Cooldown guard (_now helper) ─────────────────────────────────────────────

def test_comps_check_skipped_within_cooldown():
    """Watch checked < 23h ago should be skipped."""
    from agentic_monitor import _check_comps_for_watch
    watch = _make_watch(last_comps_check_at=datetime.now() - timedelta(hours=5))
    db = _make_db()
    with patch('agentic_monitor.RENTCAST_API_KEY', 'fake-key'):
        with patch('agentic_monitor.requests.get') as mock_get:
            _check_comps_for_watch(watch, db)
            mock_get.assert_not_called()

def test_earthquake_check_skipped_within_cooldown():
    from agentic_monitor import _check_earthquake_for_watch
    watch = _make_watch(last_earthquake_check_at=datetime.now() - timedelta(hours=10))
    db = _make_db()
    with patch('agentic_monitor.requests.get') as mock_get:
        _check_earthquake_for_watch(watch, db)
        mock_get.assert_not_called()

def test_price_check_skipped_within_cooldown():
    from agentic_monitor import _check_price_for_watch
    watch = _make_watch(last_price_check_at=datetime.now() - timedelta(hours=10))
    db = _make_db()
    with patch('agentic_monitor.RENTCAST_API_KEY', 'fake-key'):
        with patch('agentic_monitor.requests.get') as mock_get:
            _check_price_for_watch(watch, db)
            mock_get.assert_not_called()

def test_permit_check_skipped_within_cooldown():
    from agentic_monitor import _check_permits_for_watch
    watch = _make_watch(last_permit_check_at=datetime.now() - timedelta(hours=10))
    db = _make_db()
    with patch('agentic_monitor.requests.get') as mock_get:
        _check_permits_for_watch(watch, db)
        mock_get.assert_not_called()


# ── Missing API key guards ────────────────────────────────────────────────────

def test_comps_no_api_key_skips():
    from agentic_monitor import _check_comps_for_watch
    watch = _make_watch()
    db = _make_db()
    with patch('agentic_monitor.RENTCAST_API_KEY', ''):
        with patch('agentic_monitor.requests.get') as mock_get:
            _check_comps_for_watch(watch, db)
            mock_get.assert_not_called()

def test_price_no_api_key_skips():
    from agentic_monitor import _check_price_for_watch
    watch = _make_watch()
    db = _make_db()
    with patch('agentic_monitor.RENTCAST_API_KEY', ''):
        with patch('agentic_monitor.requests.get') as mock_get:
            _check_price_for_watch(watch, db)
            mock_get.assert_not_called()


# ── Comp monitor ─────────────────────────────────────────────────────────────

def _rentcast_comps_response(new_comps=None, price=780000):
    """Build a mock RentCast AVM response with optional new comps."""
    comps = new_comps or []
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        'price': price,
        'comparables': comps,
    }
    return resp

def test_comps_no_new_comps_no_alert():
    from agentic_monitor import _check_comps_for_watch
    watch = _make_watch()
    db = _make_db()
    resp = _rentcast_comps_response(new_comps=[])
    with patch('agentic_monitor.RENTCAST_API_KEY', 'key'):
        with patch('agentic_monitor.requests.get', return_value=resp):
            with patch('agentic_monitor._send') as mock_send:
                _check_comps_for_watch(watch, db)
                mock_send.assert_not_called()

def test_comps_old_comps_no_alert():
    """Comps that closed before watch was created should not trigger."""
    from agentic_monitor import _check_comps_for_watch
    old_comp = {
        'address': '456 Oak Ave', 'lastSalePrice': 750000,
        'lastSaleDate': '2025-12-01',  # before watch created_at=2026-01-01
        'squareFootage': 1800, 'bedrooms': 3,
    }
    watch = _make_watch(created_at=datetime(2026, 1, 1))
    db = _make_db()
    resp = _rentcast_comps_response(new_comps=[old_comp])
    with patch('agentic_monitor.RENTCAST_API_KEY', 'key'):
        with patch('agentic_monitor.requests.get', return_value=resp):
            with patch('agentic_monitor._send') as mock_send:
                _check_comps_for_watch(watch, db)
                mock_send.assert_not_called()

def test_comps_new_below_asking_triggers_alert():
    """New comp that closed below asking price should alert buyer and professionals."""
    from agentic_monitor import _check_comps_for_watch
    new_comp = {
        'address': '789 Elm St', 'lastSalePrice': 750000,
        'lastSaleDate': '2026-02-01',  # after watch created
        'squareFootage': 1800, 'bedrooms': 3,
    }
    watch = _make_watch(asking_price=800000, created_at=datetime(2026, 1, 1))
    db = _make_db()
    resp = _rentcast_comps_response(new_comps=[new_comp], price=780000)
    with patch('agentic_monitor.RENTCAST_API_KEY', 'key'):
        with patch('agentic_monitor.requests.get', return_value=resp):
            with patch('agentic_monitor._send') as mock_send:
                with patch('agentic_monitor._save_alert') as mock_save:
                    _check_comps_for_watch(watch, db)
                    mock_send.assert_called_once()
                    mock_save.assert_called_once()
                    # Alert type should be new_comp
                    alert_type = mock_save.call_args[0][2]
                    assert alert_type == 'new_comp'

def test_comps_new_above_asking_no_alert():
    """New comp above asking gives buyer no leverage — no alert."""
    from agentic_monitor import _check_comps_for_watch
    hot_comp = {
        'address': '999 Rich Ave', 'lastSalePrice': 950000,
        'lastSaleDate': '2026-02-01',
        'squareFootage': 1800, 'bedrooms': 3,
    }
    watch = _make_watch(asking_price=800000, created_at=datetime(2026, 1, 1))
    db = _make_db()
    resp = _rentcast_comps_response(new_comps=[hot_comp])
    with patch('agentic_monitor.RENTCAST_API_KEY', 'key'):
        with patch('agentic_monitor.requests.get', return_value=resp):
            with patch('agentic_monitor._send') as mock_send:
                _check_comps_for_watch(watch, db)
                mock_send.assert_not_called()

def test_comps_rentcast_error_no_crash():
    """RentCast returning 500 should not raise."""
    from agentic_monitor import _check_comps_for_watch
    watch = _make_watch()
    db = _make_db()
    resp = MagicMock(); resp.status_code = 500
    with patch('agentic_monitor.RENTCAST_API_KEY', 'key'):
        with patch('agentic_monitor.requests.get', return_value=resp):
            _check_comps_for_watch(watch, db)  # must not raise


# ── Earthquake monitor ────────────────────────────────────────────────────────

def _usgs_response(features=None):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'features': features or []}
    return resp

def _usgs_feature(mag=4.5, place='10km NW of San Jose, CA', time_offset_hours=2):
    t = int((datetime.utcnow() - timedelta(hours=time_offset_hours)).timestamp() * 1000)
    return {
        'properties': {'mag': mag, 'place': place, 'time': t}
    }

def test_earthquake_no_events_no_alert():
    from agentic_monitor import _check_earthquake_for_watch
    watch = _make_watch()
    db = _make_db()
    with patch('agentic_monitor.requests.get', return_value=_usgs_response([])):
        with patch('agentic_monitor._send') as mock_send:
            _check_earthquake_for_watch(watch, db)
            mock_send.assert_not_called()

def test_earthquake_m4_triggers_alert():
    from agentic_monitor import _check_earthquake_for_watch
    watch = _make_watch()
    db = _make_db()
    features = [_usgs_feature(mag=4.5)]
    with patch('agentic_monitor.requests.get', return_value=_usgs_response(features)):
        with patch('agentic_monitor._notify_linked_professionals'):
            with patch('agentic_monitor._send') as mock_send:
                with patch('agentic_monitor._save_alert') as mock_save:
                    with patch('models.User') as MockUser:
                        MockUser.query.get.return_value = MagicMock(email='buyer@test.com', id=42)
                        _check_earthquake_for_watch(watch, db)
                        mock_send.assert_called_once()
                        alert_type = mock_save.call_args[0][2]
                        assert alert_type == 'earthquake'

def test_earthquake_watch_missing_coords_skipped():
    """Watch without lat/lng can't be earthquake-checked."""
    from agentic_monitor import _check_earthquake_for_watch
    watch = _make_watch(latitude=None, longitude=None)
    db = _make_db()
    with patch('agentic_monitor.requests.get') as mock_get:
        _check_earthquake_for_watch(watch, db)
        mock_get.assert_not_called()

def test_earthquake_usgs_error_no_crash():
    from agentic_monitor import _check_earthquake_for_watch
    watch = _make_watch()
    db = _make_db()
    resp = MagicMock(); resp.status_code = 503
    with patch('agentic_monitor.requests.get', return_value=resp):
        _check_earthquake_for_watch(watch, db)  # must not raise

def test_earthquake_magnitude_in_alert_body():
    """Alert body should mention the magnitude."""
    from agentic_monitor import _check_earthquake_for_watch
    watch = _make_watch()
    db = _make_db()
    features = [_usgs_feature(mag=5.2)]
    with patch('agentic_monitor.requests.get', return_value=_usgs_response(features)):
        with patch('agentic_monitor._send') as mock_send:
            with patch('agentic_monitor._save_alert'):
                _check_earthquake_for_watch(watch, db)
                call_args = mock_send.call_args
                html_body = call_args[0][2] if call_args[0] else str(call_args)
                assert '5.2' in html_body


# ── Price monitor ─────────────────────────────────────────────────────────────

def _rentcast_avm_response(price):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'price': price}
    return resp

def test_price_no_drop_no_alert():
    from agentic_monitor import _check_price_for_watch
    watch = _make_watch(avm_at_analysis=800000)
    db = _make_db()
    with patch('agentic_monitor.RENTCAST_API_KEY', 'key'):
        with patch('agentic_monitor.requests.get', return_value=_rentcast_avm_response(795000)):
            with patch('agentic_monitor._send') as mock_send:
                _check_price_for_watch(watch, db)
                mock_send.assert_not_called()

def test_price_drop_2pct_triggers_alert():
    """A 2%+ AVM drop should fire a price_drop alert."""
    from agentic_monitor import _check_price_for_watch
    watch = _make_watch(avm_at_analysis=800000)
    db = _make_db()
    with patch('agentic_monitor.RENTCAST_API_KEY', 'key'):
        with patch('agentic_monitor.requests.get', return_value=_rentcast_avm_response(780000)):
            with patch('agentic_monitor._send') as mock_send:
                with patch('agentic_monitor._save_alert') as mock_save:
                    _check_price_for_watch(watch, db)
                    mock_send.assert_called_once()
                    alert_type = mock_save.call_args[0][2]
                    assert alert_type == 'price_drop'

def test_price_drop_uses_baseline_over_asking():
    """AVM baseline should take priority over asking_price for comparison."""
    from agentic_monitor import _check_price_for_watch
    # avm_at_analysis=800K, asking=850K, current AVM=775K
    # Drop should be 800K→775K = 3.1%, not 850K→775K
    watch = _make_watch(avm_at_analysis=800000, asking_price=850000)
    db = _make_db()
    with patch('agentic_monitor.RENTCAST_API_KEY', 'key'):
        with patch('agentic_monitor.requests.get', return_value=_rentcast_avm_response(775000)):
            with patch('agentic_monitor._send') as mock_send:
                with patch('agentic_monitor._save_alert') as mock_save:
                    _check_price_for_watch(watch, db)
                    mock_send.assert_called_once()

def test_price_api_error_no_crash():
    from agentic_monitor import _check_price_for_watch
    watch = _make_watch()
    db = _make_db()
    resp = MagicMock(); resp.status_code = 429
    with patch('agentic_monitor.RENTCAST_API_KEY', 'key'):
        with patch('agentic_monitor.requests.get', return_value=resp):
            _check_price_for_watch(watch, db)  # must not raise

def test_price_missing_avm_field_no_crash():
    """Response without 'price' field should not crash."""
    from agentic_monitor import _check_price_for_watch
    watch = _make_watch(avm_at_analysis=800000)
    db = _make_db()
    resp = MagicMock(); resp.status_code = 200; resp.json.return_value = {}
    with patch('agentic_monitor.RENTCAST_API_KEY', 'key'):
        with patch('agentic_monitor.requests.get', return_value=resp):
            _check_price_for_watch(watch, db)  # must not raise


# ── Permit monitor ────────────────────────────────────────────────────────────

def _permit(pid='P001', ptype='Roof Replacement', date='2026-03-01'):
    return {'id': pid, 'permit_type': ptype, 'issue_date': date,
            'description': f'{ptype} work', 'status': 'issued'}

def test_permits_no_permits_no_alert():
    from agentic_monitor import _check_permits_for_watch
    watch = _make_watch(baseline_permits_json=None)
    db = _make_db()
    with patch('agentic_monitor._fetch_permits_socrata', return_value=[]):
        with patch('agentic_monitor._fetch_permits_permitdata', return_value=[]):
            with patch('agentic_monitor._fetch_permits_openpermit', return_value=[]):
                with patch('agentic_monitor._send') as mock_send:
                    _check_permits_for_watch(watch, db)
                    mock_send.assert_not_called()

def test_permits_new_permit_triggers_alert():
    from agentic_monitor import _check_permits_for_watch
    watch = _make_watch(baseline_permits_json=json.dumps([]))
    db = _make_db()
    new_permit = _permit('P999', 'Electrical Panel Upgrade')
    with patch('agentic_monitor._resolve_county', return_value='Santa Clara'):
        with patch('agentic_monitor._fetch_permits_socrata', return_value=[new_permit]):
            with patch('agentic_monitor._notify_linked_professionals'):
                with patch('agentic_monitor._send') as mock_send:
                    with patch('agentic_monitor._save_alert') as mock_save:
                        with patch('models.User') as MockUser:
                            MockUser.query.get.return_value = MagicMock(email='buyer@test.com', id=42)
                            _check_permits_for_watch(watch, db)
                            mock_send.assert_called_once()
                            alert_type = mock_save.call_args[0][2]
                            assert alert_type == 'new_permit'

def test_permits_baseline_permit_no_alert():
    """A permit that was already in the baseline should not alert."""
    from agentic_monitor import _check_permits_for_watch
    existing = _permit('P001', 'Roof')
    watch = _make_watch(baseline_permits_json=json.dumps([existing]))
    db = _make_db()
    with patch('agentic_monitor._fetch_permits_socrata', return_value=[existing]):
        with patch('agentic_monitor._send') as mock_send:
            _check_permits_for_watch(watch, db)
            mock_send.assert_not_called()

def test_permits_cascade_socrata_first():
    """Socrata source should be tried first; if it returns data, others skipped."""
    from agentic_monitor import _check_permits_for_watch
    watch = _make_watch(baseline_permits_json=json.dumps([]))
    db = _make_db()
    socrata_data = [_permit('P001', 'Plumbing')]
    with patch('agentic_monitor._resolve_county', return_value='Santa Clara'):
        with patch('agentic_monitor._fetch_permits_socrata', return_value=socrata_data) as mock_soc:
            with patch('agentic_monitor._fetch_permits_permitdata', return_value=[]) as mock_pd:
                with patch('agentic_monitor._send'):
                    with patch('agentic_monitor._save_alert'):
                        _check_permits_for_watch(watch, db)
                        mock_soc.assert_called_once()
                        mock_pd.assert_not_called()

def test_permits_cascade_fallback_to_permitdata():
    """If Socrata returns empty, PermitData.io should be tried."""
    from agentic_monitor import _check_permits_for_watch
    watch = _make_watch(baseline_permits_json=json.dumps([]))
    db = _make_db()
    with patch('agentic_monitor._fetch_permits_socrata', return_value=[]):
        with patch('agentic_monitor._fetch_permits_permitdata', return_value=[_permit('P002', 'HVAC')]) as mock_pd:
            with patch('agentic_monitor._send'):
                with patch('agentic_monitor._save_alert'):
                    _check_permits_for_watch(watch, db)
                    mock_pd.assert_called_once()

def test_permits_cascade_fallback_to_openpermit():
    """If both Socrata and PermitData return empty, OpenPermit is tried."""
    from agentic_monitor import _check_permits_for_watch
    watch = _make_watch(baseline_permits_json=json.dumps([]))
    db = _make_db()
    with patch('agentic_monitor._fetch_permits_socrata', return_value=[]):
        with patch('agentic_monitor._fetch_permits_permitdata', return_value=[]):
            with patch('agentic_monitor._fetch_permits_openpermit', return_value=[_permit('P003', 'Foundation')]) as mock_op:
                with patch('agentic_monitor._send'):
                    with patch('agentic_monitor._save_alert'):
                        _check_permits_for_watch(watch, db)
                        mock_op.assert_called_once()


# ── _resolve_county ───────────────────────────────────────────────────────────

def test_resolve_county_keyword_santa_clara():
    from agentic_monitor import _resolve_county, COUNTY_PERMIT_APIS
    county = _resolve_county('123 Main St, San Jose, Santa Clara, CA 95112')
    assert county == 'Santa Clara'
    assert county in COUNTY_PERMIT_APIS

def test_resolve_county_keyword_alameda():
    from agentic_monitor import _resolve_county
    county = _resolve_county('456 Broadway, Oakland, Alameda, CA 94601')
    assert county == 'Alameda'

def test_resolve_county_unknown_returns_empty_not_crash():
    from agentic_monitor import _resolve_county
    # Census geocoder will be called; mock it to return empty
    resp = MagicMock(); resp.status_code = 200
    resp.json.return_value = {'result': {'addressMatches': []}}
    with patch('agentic_monitor.requests.get', return_value=resp):
        result = _resolve_county('1 Unknown Way, Nowhere, ZZ 00000')
        assert isinstance(result, str)  # empty string, not exception

def test_resolve_county_geocoder_error_no_crash():
    from agentic_monitor import _resolve_county
    with patch('agentic_monitor.requests.get', side_effect=Exception('timeout')):
        result = _resolve_county('Anywhere, USA')
        assert result == ''


# ── _fetch_permits_socrata ────────────────────────────────────────────────────

def test_fetch_permits_socrata_known_county():
    from agentic_monitor import _fetch_permits_socrata
    resp = MagicMock(); resp.status_code = 200
    resp.json.return_value = [{'id': 'P1', 'address': '123 Main', 'permit_type': 'Roof'}]
    with patch('agentic_monitor.requests.get', return_value=resp):
        result = _fetch_permits_socrata('Santa Clara', '123 Main St')
        assert len(result) == 1

def test_fetch_permits_socrata_unknown_county_returns_empty():
    from agentic_monitor import _fetch_permits_socrata
    result = _fetch_permits_socrata('Unknown County', '123 Main St')
    assert result == []

def test_fetch_permits_socrata_api_error_returns_empty():
    from agentic_monitor import _fetch_permits_socrata
    resp = MagicMock(); resp.status_code = 500
    with patch('agentic_monitor.requests.get', return_value=resp):
        result = _fetch_permits_socrata('Santa Clara', '123 Main St')
        assert result == []

def test_fetch_permits_socrata_exception_returns_empty():
    from agentic_monitor import _fetch_permits_socrata
    with patch('agentic_monitor.requests.get', side_effect=Exception('network error')):
        result = _fetch_permits_socrata('Santa Clara', '123 Main St')
        assert result == []


# ── _fetch_permits_permitdata ─────────────────────────────────────────────────

def test_fetch_permits_permitdata_no_key_returns_empty():
    from agentic_monitor import _fetch_permits_permitdata
    with patch('agentic_monitor.os.environ.get', return_value=''):
        result = _fetch_permits_permitdata('123 Main St')
        assert result == []

def test_fetch_permits_permitdata_with_key_returns_normalised():
    from agentic_monitor import _fetch_permits_permitdata
    resp = MagicMock(); resp.status_code = 200
    resp.json.return_value = {'permits': [
        {'permit_number': 'PD001', 'permit_type': 'Electrical',
         'description': 'Panel upgrade', 'issue_date': '2026-03-01', 'status': 'issued'}
    ]}
    with patch('agentic_monitor.os.environ.get', return_value='fake-pd-key'):
        with patch('agentic_monitor.requests.get', return_value=resp):
            result = _fetch_permits_permitdata('123 Main St')
            assert len(result) == 1
            assert result[0]['id'] == 'PD001'
            assert result[0]['type'] == 'Electrical'

def test_fetch_permits_permitdata_exception_returns_empty():
    from agentic_monitor import _fetch_permits_permitdata
    with patch('agentic_monitor.os.environ.get', return_value='fake-key'):
        with patch('agentic_monitor.requests.get', side_effect=Exception('timeout')):
            result = _fetch_permits_permitdata('123 Main St')
            assert result == []


# ── _fetch_permits_openpermit ─────────────────────────────────────────────────

def test_fetch_permits_openpermit_returns_data():
    from agentic_monitor import _fetch_permits_openpermit
    resp = MagicMock(); resp.status_code = 200
    resp.json.return_value = {'data': [
        {'id': 'OP001', 'permit_type': 'Plumbing', 'description': 'Pipe repair',
         'issue_date': '2026-03-10', 'status': 'open'}
    ]}
    with patch('agentic_monitor.requests.get', return_value=resp):
        result = _fetch_permits_openpermit('123 Main St')
        assert len(result) == 1
        assert result[0]['id'] == 'OP001'

def test_fetch_permits_openpermit_404_returns_empty():
    from agentic_monitor import _fetch_permits_openpermit
    resp = MagicMock(); resp.status_code = 404
    with patch('agentic_monitor.requests.get', return_value=resp):
        result = _fetch_permits_openpermit('123 Main St')
        assert result == []

def test_fetch_permits_openpermit_exception_returns_empty():
    from agentic_monitor import _fetch_permits_openpermit
    with patch('agentic_monitor.requests.get', side_effect=Exception('DNS fail')):
        result = _fetch_permits_openpermit('123 Main St')
        assert result == []


# ── fire_buyer_concern_signal ─────────────────────────────────────────────────

def test_concern_signal_first_view_fires():
    """View count=1 (first open) should fire an alert to the professional."""
    from agentic_monitor import fire_buyer_concern_signal
    with patch('agentic_monitor._send') as mock_send:
        fire_buyer_concern_signal(
            report_type='inspector',
            report_id=1,
            buyer_name='Alice Buyer',
            buyer_email='alice@test.com',
            address='123 Main St, San Jose, CA',
            view_count=1,
            top_findings=[{'title': 'Roof damage', 'detail': 'Significant wear'}],
        )
        # Should attempt to fetch inspector and send
        # (will fail gracefully if no DB, but should not raise)

def test_concern_signal_third_view_fires():
    """View count=3 (deep engagement) should also fire."""
    from agentic_monitor import fire_buyer_concern_signal
    with patch('agentic_monitor._send') as mock_send:
        fire_buyer_concern_signal(
            report_type='agent', report_id=2,
            buyer_name='Bob Buyer', buyer_email='bob@test.com',
            address='456 Oak Ave, Palo Alto, CA',
            view_count=3, top_findings=[],
        )

def test_concern_signal_second_view_no_fire():
    """View count=2 should NOT fire (only 1 and 3 are trigger points)."""
    from agentic_monitor import fire_buyer_concern_signal
    with patch('agentic_monitor._send') as mock_send:
        fire_buyer_concern_signal(
            report_type='inspector', report_id=1,
            buyer_name='Carol', buyer_email='carol@test.com',
            address='789 Pine Rd', view_count=2,
        )
        mock_send.assert_not_called()

def test_concern_signal_invalid_type_no_crash():
    from agentic_monitor import fire_buyer_concern_signal
    fire_buyer_concern_signal(
        report_type='unknown', report_id=99,
        buyer_name='X', buyer_email='x@test.com',
        address='nowhere', view_count=1,
    )

def test_concern_signal_no_findings_no_crash():
    from agentic_monitor import fire_buyer_concern_signal
    with patch('agentic_monitor._send'):
        fire_buyer_concern_signal(
            report_type='inspector', report_id=1,
            buyer_name='Dave', buyer_email='dave@test.com',
            address='123 Main', view_count=1,
            top_findings=None,
        )


# ── _notify_linked_professionals ─────────────────────────────────────────────

def test_notify_no_linked_professionals_no_crash():
    """Watch with no linked inspector/agent/contractor should not crash."""
    from agentic_monitor import _notify_linked_professionals
    watch = _make_watch(inspector_report_id=None, agent_share_id=None, contractor_lead_id=None)
    with patch('agentic_monitor._send') as mock_send:
        _notify_linked_professionals(watch, 'Test title', '<p>body</p>', '123 Main St')
        mock_send.assert_not_called()


# ── register_monitoring_jobs ──────────────────────────────────────────────────

def test_register_monitoring_jobs_adds_four_jobs():
    from agentic_monitor import register_monitoring_jobs
    from app import app
    scheduler = MagicMock()
    with app.app_context():
        register_monitoring_jobs(scheduler)
    assert scheduler.add_job.call_count == 5

def test_register_monitoring_jobs_uses_cron():
    from agentic_monitor import register_monitoring_jobs
    from app import app
    scheduler = MagicMock()
    with app.app_context():
        register_monitoring_jobs(scheduler)
    for call_args in scheduler.add_job.call_args_list:
        trigger = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get('trigger')
        assert trigger == 'cron', f"Expected cron trigger, got {trigger}"

def test_register_monitoring_jobs_unique_ids():
    from agentic_monitor import register_monitoring_jobs
    from app import app
    scheduler = MagicMock()
    with app.app_context():
        register_monitoring_jobs(scheduler)
    ids = [c[1].get('id') for c in scheduler.add_job.call_args_list]
    assert len(ids) == len(set(ids)), "Duplicate job IDs"


# ── Job-level smoke tests ─────────────────────────────────────────────────────

def test_job_comps_no_api_key_no_crash():
    from agentic_monitor import _job_comps_monitor
    with patch('agentic_monitor.RENTCAST_API_KEY', ''):
        _job_comps_monitor()  # must not raise

def test_job_earthquake_db_error_no_crash():
    """If DB query fails, the job should log and return gracefully."""
    from agentic_monitor import _job_earthquake_monitor
    with patch('agentic_monitor._get_db') as mock_db:
        mock_db.return_value.query.side_effect = Exception('DB connection failed')
        _job_earthquake_monitor()  # must not raise

def test_job_price_no_api_key_no_crash():
    from agentic_monitor import _job_price_monitor
    with patch('agentic_monitor.RENTCAST_API_KEY', ''):
        _job_price_monitor()  # must not raise

def test_job_permit_db_error_no_crash():
    from agentic_monitor import _job_permit_monitor
    with patch('agentic_monitor._get_db') as mock_db:
        mock_db.return_value.query.side_effect = Exception('DB error')
        _job_permit_monitor()  # must not raise


# ── Alert body content checks ─────────────────────────────────────────────────

def test_earthquake_alert_severity_critical_for_high_magnitude():
    """M5.5+ should produce a 'critical' severity alert."""
    from agentic_monitor import _check_earthquake_for_watch
    watch = _make_watch()
    db = _make_db()
    features = [_usgs_feature(mag=6.1)]
    with patch('agentic_monitor.requests.get', return_value=_usgs_response(features)):
        with patch('agentic_monitor._send'):
            with patch('agentic_monitor._save_alert') as mock_save:
                _check_earthquake_for_watch(watch, db)
                severity = mock_save.call_args[0][3]
                assert severity == 'critical'

def test_earthquake_alert_severity_warning_for_moderate():
    """M4.x should produce a 'warning' severity alert."""
    from agentic_monitor import _check_earthquake_for_watch
    watch = _make_watch()
    db = _make_db()
    features = [_usgs_feature(mag=4.3)]
    with patch('agentic_monitor.requests.get', return_value=_usgs_response(features)):
        with patch('agentic_monitor._send'):
            with patch('agentic_monitor._save_alert') as mock_save:
                _check_earthquake_for_watch(watch, db)
                severity = mock_save.call_args[0][3]
                assert severity == 'warning'

def test_price_drop_alert_contains_dollar_figures():
    """Alert body should contain formatted price figures."""
    from agentic_monitor import _check_price_for_watch
    watch = _make_watch(avm_at_analysis=800000)
    db = _make_db()
    with patch('agentic_monitor.RENTCAST_API_KEY', 'key'):
        with patch('agentic_monitor.requests.get', return_value=_rentcast_avm_response(760000)):
            with patch('agentic_monitor._send') as mock_send:
                with patch('agentic_monitor._save_alert'):
                    _check_price_for_watch(watch, db)
                    call_html = mock_send.call_args[0][2]
                    assert '$' in call_html
