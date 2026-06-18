"""air_quality.py — single source of truth for EPA AirNow current-AQI lookups.

Both the Risk Check tool (risk_check_engine.check_air_quality) and the
full-analysis AirQualityTool call get_current_aqi(), so the endpoint lives in
exactly one place.

Migration note: AirNow is retiring /aq/observation/latLong/current/ on
2026-09-30 and replacing it with /aq/observation/current/ziplatlong/. Both run
in parallel until then, so we call the NEW endpoint first and fall back to the
retiring one if the new call errors or returns a non-observation body. That
keeps AQI flowing even if the new endpoint's parameter spec differs from what we
send — its per-service input docs sit behind an AirNow account login and were
not publicly confirmable at migration time. Once the new endpoint is confirmed
in the AirNow dashboard, delete _LEGACY_URL and the fallback loop before
2026-09-30.
"""
import os
import logging
import requests

logger = logging.getLogger(__name__)

# The single place the AirNow current-observation URL lives. Update here.
AIRNOW_CURRENT_URL = 'https://www.airnowapi.org/aq/observation/current/ziplatlong/'
_LEGACY_URL = 'https://www.airnowapi.org/aq/observation/latLong/current/'  # retires 2026-09-30
AIRNOW_RETIREMENT_DATE = '2026-09-30'

_DEFAULT_DISTANCE = 25
_TIMEOUT = 15


def get_current_aqi(lat, lng, user_agent='OfferWise/1.0'):
    """Return AirNow's current-observation list for a coordinate, or None.

    Tries the post-2026 endpoint first and falls back to the retiring one during
    the parallel-availability window, so a parameter mismatch can't silently
    drop air-quality data. Returns the parsed JSON list (callers do their own
    AQI parsing), or None on any failure, missing key, or non-observation body.
    """
    api_key = os.environ.get('AIRNOW_API_KEY')
    if not api_key:
        return None
    params = {
        'format': 'application/json',
        'latitude': lat,
        'longitude': lng,
        'distance': _DEFAULT_DISTANCE,
        'API_KEY': api_key,
    }
    headers = {'User-Agent': user_agent}
    for url in (AIRNOW_CURRENT_URL, _LEGACY_URL):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and data:
                return data
        except Exception as e:
            logger.warning(f"AirNow lookup failed at {url}: {e}")
    return None
