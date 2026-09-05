"""test_air_quality.py — centralized AirNow helper on the post-2026 service.

v5.89.344: the legacy fallback is gone (retires 2026-09-30, already returns []).
The important contract is schema normalization: the new service returns
nowcastAQI / aqiCategoryName / parameterName, and both consumers read
AQI / Category.Name / ParameterName. Without normalization every AQI read as 0.
"""
import air_quality

# A verbatim row from the live service (Frisco, TX, 2026-09-05, production key).
LIVE_ROW = {
    "dateObserved": "2026-09-05", "hourObserved": "08:00", "localTimeZone": "CDT",
    "reportingAreaName": "Dallas-Fort Worth", "siteID": "481210034",
    "siteName": "Denton Airport South C56", "parameterName": "PM2.5",
    "nowcastAQI": 46, "aqiCategoryName": "Good",
    "reportingAgency": "Texas Commission on Environmental Quality",
    "lookupBehavior": "Closest Reading By Pollutant", "consideredMonitors": "All",
    "lookupBoundary": "50 Miles",
}


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


def test_new_schema_is_normalized_to_legacy_fields(monkeypatch):
    monkeypatch.setenv('AIRNOW_API_KEY', 'k')
    calls = []
    monkeypatch.setattr(air_quality.requests, 'get',
                        lambda url, **kw: (calls.append((url, kw['params'])), _Resp([LIVE_ROW]))[1])
    data = air_quality.get_current_aqi(33.15, -96.82)
    assert calls[0][0] == air_quality.AIRNOW_CURRENT_URL
    assert calls[0][1]['latitude'] == 33.15 and calls[0][1]['longitude'] == -96.82
    row = data[0]
    assert row['AQI'] == 46                       # was 0 before normalization
    assert row['Category']['Name'] == 'Good'      # was 'Unknown'
    assert row['ParameterName'] == 'PM2.5'
    assert row['ReportingArea'] == 'Dallas-Fort Worth'
    assert row['nowcastAQI'] == 46                # original keys preserved


def test_legacy_schema_still_passes_through(monkeypatch):
    monkeypatch.setenv('AIRNOW_API_KEY', 'k')
    monkeypatch.setattr(air_quality.requests, 'get',
                        lambda url, **kw: _Resp([{'AQI': 88, 'Category': {'Name': 'Moderate'}, 'ParameterName': 'O3'}]))
    row = air_quality.get_current_aqi(37.0, -122.0)[0]
    assert (row['AQI'], row['Category']['Name'], row['ParameterName']) == (88, 'Moderate', 'O3')


def test_zip_code_path(monkeypatch):
    monkeypatch.setenv('AIRNOW_API_KEY', 'k')
    seen = {}
    monkeypatch.setattr(air_quality.requests, 'get',
                        lambda url, **kw: (seen.update(kw['params']), _Resp([LIVE_ROW]))[1])
    assert air_quality.get_current_aqi(None, None, zip_code='75035-1234')[0]['AQI'] == 46
    assert seen['zipCode'] == '75035' and 'latitude' not in seen


def test_no_location_returns_none(monkeypatch):
    monkeypatch.setenv('AIRNOW_API_KEY', 'k')
    assert air_quality.get_current_aqi(None, None) is None


def test_empty_or_non_list_body_returns_none(monkeypatch):
    monkeypatch.setenv('AIRNOW_API_KEY', 'k')
    monkeypatch.setattr(air_quality.requests, 'get', lambda url, **kw: _Resp([]))
    assert air_quality.get_current_aqi(37.0, -122.0) is None
    monkeypatch.setattr(air_quality.requests, 'get', lambda url, **kw: _Resp({'WebServiceError': [{'Message': 'x'}]}))
    assert air_quality.get_current_aqi(37.0, -122.0) is None


def test_failure_returns_none_and_only_new_endpoint_is_called(monkeypatch):
    monkeypatch.setenv('AIRNOW_API_KEY', 'k')
    calls = []

    def boom(url, **kw):
        calls.append(url)
        raise Exception('down')
    monkeypatch.setattr(air_quality.requests, 'get', boom)
    assert air_quality.get_current_aqi(37.0, -122.0) is None
    assert calls == [air_quality.AIRNOW_CURRENT_URL]
    assert not hasattr(air_quality, '_LEGACY_URL')


def test_consumers_read_the_normalized_fields():
    """The two consumers' parsing, applied to a normalized live row."""
    row = air_quality.normalize_reading(LIVE_ROW)
    worst = max([row], key=lambda x: x.get('AQI', 0))
    assert worst.get('AQI', 0) == 46
    assert worst.get('Category', {}).get('Name', 'Unknown') == 'Good'
    assert row.get('ParameterName', '') == 'PM2.5'
