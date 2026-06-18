"""test_air_quality.py — centralized AirNow helper + transition-window fallback."""
import air_quality


class _Resp:
    def __init__(self, data, ok=True):
        self._d, self._ok = data, ok

    def raise_for_status(self):
        if not self._ok:
            raise Exception('HTTP 404')

    def json(self):
        return self._d


def test_no_key(monkeypatch):
    monkeypatch.delenv('AIRNOW_API_KEY', raising=False)
    assert air_quality.get_current_aqi(37.0, -122.0) is None


def test_new_endpoint_used_first(monkeypatch):
    monkeypatch.setenv('AIRNOW_API_KEY', 'k')
    calls = []
    monkeypatch.setattr(air_quality.requests, 'get',
                        lambda url, **kw: (calls.append(url),
                                           _Resp([{'AQI': 42, 'Category': {'Name': 'Good'}}]))[1])
    data = air_quality.get_current_aqi(37.0, -122.0)
    assert data[0]['AQI'] == 42
    assert calls == [air_quality.AIRNOW_CURRENT_URL]  # legacy NOT called when new works


def test_falls_back_to_legacy(monkeypatch):
    monkeypatch.setenv('AIRNOW_API_KEY', 'k')
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if url == air_quality.AIRNOW_CURRENT_URL:
            return _Resp(None, ok=False)            # new endpoint errors (e.g. param mismatch)
        return _Resp([{'AQI': 88, 'Category': {'Name': 'Moderate'}}])

    monkeypatch.setattr(air_quality.requests, 'get', fake_get)
    data = air_quality.get_current_aqi(37.0, -122.0)
    assert data[0]['AQI'] == 88                       # still got data via legacy
    assert calls == [air_quality.AIRNOW_CURRENT_URL, air_quality._LEGACY_URL]


def test_non_list_body_falls_back(monkeypatch):
    monkeypatch.setenv('AIRNOW_API_KEY', 'k')

    def fake_get(url, **kw):
        if url == air_quality.AIRNOW_CURRENT_URL:
            return _Resp({'error': 'bad param'})      # 200 but not an observation list
        return _Resp([{'AQI': 10, 'Category': {'Name': 'Good'}}])

    monkeypatch.setattr(air_quality.requests, 'get', fake_get)
    assert air_quality.get_current_aqi(37.0, -122.0)[0]['AQI'] == 10


def test_both_fail_returns_none(monkeypatch):
    monkeypatch.setenv('AIRNOW_API_KEY', 'k')
    def boom(url, **kw):
        raise Exception('down')
    monkeypatch.setattr(air_quality.requests, 'get', boom)
    assert air_quality.get_current_aqi(37.0, -122.0) is None
