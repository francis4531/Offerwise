"""air_quality.py — single source of truth for EPA AirNow current-AQI lookups.

Both the Risk Check tool (risk_check_engine.check_air_quality) and the
full-analysis AirQualityTool call get_current_aqi(), so the endpoint lives in
exactly one place.

v5.89.344 — verified against the live API with the production key (Frisco, TX,
2026-09-05):

  * The new service, /aq/observation/current/ziplatlong/, accepts either
    latitude+longitude or zipCode (plus distance, format, API_KEY) and returns
    HTTP 200 with a list of readings.
  * Its schema is NOT the legacy schema. Legacy rows carried
    {AQI, Category: {Name}, ParameterName, ReportingArea, DateObserved, HourObserved}.
    The new rows carry {nowcastAQI, aqiCategoryName, parameterName,
    reportingAreaName, siteName, dateObserved, hourObserved, ...}.
  * The retiring endpoint (/aq/observation/latLong/current/) already returned []
    for the same coordinate.

Before this version the code called the new endpoint, accepted its non-empty
list, and both consumers then read reading['AQI'] -> 0 and Category.Name ->
'Unknown'. So AQI silently rendered as "no data" for every property while the
call itself succeeded. This module now normalizes the new rows to the legacy
field names the consumers read, and the retiring endpoint is gone (it is empty
today and retires 2026-09-30).
"""
import os
import logging
import requests

logger = logging.getLogger(__name__)

# The single place the AirNow current-observation URL lives. Update here.
AIRNOW_CURRENT_URL = 'https://www.airnowapi.org/aq/observation/current/ziplatlong/'
AIRNOW_RETIREMENT_DATE = '2026-09-30'   # legacy latLong/current retired; no longer called

_DEFAULT_DISTANCE = 25
_TIMEOUT = 15


def _to_int(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return 0


def normalize_reading(row):
    """Map a reading from either schema onto the legacy field names consumers use.

    Returns a dict with AQI (int), Category {Name}, ParameterName, ReportingArea,
    DateObserved, HourObserved, LocalTimeZone, SiteName, plus the original row's
    keys, so nothing downstream has to know which service answered.
    """
    if not isinstance(row, dict):
        return None
    out = dict(row)
    if 'AQI' not in out:
        out['AQI'] = _to_int(row.get('nowcastAQI', row.get('aqi', 0)))
    else:
        out['AQI'] = _to_int(row.get('AQI'))
    cat = row.get('Category')
    if not (isinstance(cat, dict) and cat.get('Name')):
        name = row.get('aqiCategoryName') or (cat if isinstance(cat, str) else '') or 'Unknown'
        out['Category'] = {'Name': name}
    out['ParameterName'] = row.get('ParameterName') or row.get('parameterName') or ''
    out['ReportingArea'] = row.get('ReportingArea') or row.get('reportingAreaName') or ''
    out['DateObserved'] = row.get('DateObserved') or row.get('dateObserved') or ''
    out['HourObserved'] = row.get('HourObserved') or row.get('hourObserved') or ''
    out['LocalTimeZone'] = row.get('LocalTimeZone') or row.get('localTimeZone') or ''
    out['SiteName'] = row.get('SiteName') or row.get('siteName') or ''
    return out


def get_current_aqi(lat, lng, user_agent='OfferWise/1.0', zip_code=None):
    """Return AirNow's current-observation list for a coordinate (or ZIP), or None.

    Rows are normalized to the legacy field names (AQI, Category.Name,
    ParameterName, ...). Returns None on any failure, missing key, or an empty /
    non-list body, so callers render "no data" rather than a wrong number.
    """
    api_key = os.environ.get('AIRNOW_API_KEY')
    if not api_key:
        return None
    params = {
        'format': 'application/json',
        'distance': _DEFAULT_DISTANCE,
        'API_KEY': api_key,
    }
    if lat is not None and lng is not None:
        params['latitude'] = lat
        params['longitude'] = lng
    elif zip_code:
        params['zipCode'] = str(zip_code)[:5]
    else:
        return None
    headers = {'User-Agent': user_agent}
    try:
        resp = requests.get(AIRNOW_CURRENT_URL, params=params, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"AirNow lookup failed at {AIRNOW_CURRENT_URL}: {e}")
        return None
    if not isinstance(data, list) or not data:
        return None
    rows = [r for r in (normalize_reading(x) for x in data) if r]
    return rows or None
