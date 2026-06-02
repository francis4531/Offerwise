"""
OfferWise Risk Check Engine v2.1
================================
Calculates hidden risk exposure for any US property address.
Uses ONLY free government APIs — zero cost per lookup.

Sources (11):
  FEMA NFHL, USGS Earthquakes, OpenFEMA Disasters, CAL FIRE, CGS Faults,
  EPA AirNow, EPA Superfund (SEMS), EPA Toxic Release Inventory,
  EPA Hazardous Waste (RCRA), EPA Brownfields, EPA Radon Zones
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from math import radians, sin, cos, sqrt, atan2

import requests

logger = logging.getLogger(__name__)

# Simple in-memory cache (address_hash -> {result, timestamp})
_cache = {}
CACHE_TTL = 6 * 3600  # 6 hours

# ---------------------------------------------------------------------------
# CIRCUIT BREAKER (v5.61.0) — stop hammering downed APIs
# ---------------------------------------------------------------------------
class CircuitBreaker:
    """Simple circuit breaker: after N consecutive failures, skip API for cooldown period."""
    def __init__(self, threshold=3, cooldown=300):
        self.threshold = threshold   # failures before opening circuit
        self.cooldown = cooldown     # seconds to wait before retrying
        self._failures = {}          # api_name -> consecutive failure count
        self._open_until = {}        # api_name -> timestamp when circuit closes

    def is_open(self, name):
        """Return True if circuit is open (API should be skipped)."""
        if name in self._open_until:
            if time.time() < self._open_until[name]:
                return True
            # Cooldown expired — allow a retry (half-open)
            del self._open_until[name]
            self._failures[name] = self.threshold - 1  # one more failure re-opens
        return False

    def record_success(self, name):
        self._failures.pop(name, None)
        self._open_until.pop(name, None)

    def record_failure(self, name):
        self._failures[name] = self._failures.get(name, 0) + 1
        if self._failures[name] >= self.threshold:
            self._open_until[name] = time.time() + self.cooldown
            logger.warning(f"⚡ Circuit OPEN for {name} — {self.threshold} consecutive failures, cooldown {self.cooldown}s")

    def status(self):
        return {name: {'failures': self._failures.get(name, 0),
                       'open_until': self._open_until.get(name)}
                for name in set(list(self._failures) + list(self._open_until))}

_breaker = CircuitBreaker(threshold=3, cooldown=300)

# ---------------------------------------------------------------------------
# RISK COST ESTIMATES (based on industry averages / NFIP / FEMA data)
# ---------------------------------------------------------------------------
RISK_COSTS = {
    'flood_high': 42000,
    'flood_moderate': 8000,
    'earthquake_close': 35000,
    'earthquake_moderate': 15000,
    'earthquake_far': 5000,
    'wildfire_very_high': 25000,
    'wildfire_high': 12000,
    'wildfire_moderate': 5000,
    'fault_zone_high': 20000,
    'fault_zone_moderate': 10000,
    'disaster_heavy': 15000,
    'disaster_moderate': 8000,
    'disaster_light': 4000,
    'air_quality_high': 8000,
    'air_quality_moderate': 3000,
    # Phase 1 — EPA Environmental Hazards
    'superfund_close': 45000,       # NPL site within 1 mi — 10-15% value loss
    'superfund_moderate': 15000,    # NPL site within 3 mi
    'tri_high': 12000,              # 5+ TRI facilities within 3 mi
    'tri_moderate': 5000,           # 1-4 TRI facilities within 3 mi
    'hazwaste_high': 8000,          # Dense hazardous waste corridor
    'hazwaste_moderate': 3000,      # Moderate hazardous waste presence
    'brownfield_close': 10000,      # Brownfield within 1 mi
    'brownfield_moderate': 4000,    # Brownfield within 3 mi
    'radon_high': 2500,             # EPA Zone 1 (highest potential)
    'radon_moderate': 1200,         # EPA Zone 2 (moderate potential)
}


# ---------------------------------------------------------------------------
# GEOCODING
# ---------------------------------------------------------------------------
def geocode_address(address):
    """Geocode address. Try Google Maps → US Census Bureau → Nominatim."""

    # --- 1) Google Maps (if key available) ---
    google_key = os.environ.get('GOOGLE_MAPS_API_KEY')
    if google_key:
        try:
            resp = requests.get(
                'https://maps.googleapis.com/maps/api/geocode/json',
                params={'address': address, 'key': google_key},
                headers={'User-Agent': 'OfferWise/1.0 (risk-check)'},
                timeout=10
            )
            data = resp.json()
            if data.get('results'):
                result = data['results'][0]
                loc = result['geometry']['location']

                components = {}
                for comp in result.get('address_components', []):
                    for t in comp['types']:
                        components[t] = comp.get('short_name', comp.get('long_name', ''))

                return {
                    'lat': loc['lat'],
                    'lng': loc['lng'],
                    'formatted': result.get('formatted_address', address),
                    'state': components.get('administrative_area_level_1', ''),
                    'county': components.get('administrative_area_level_2', '').replace(' County', ''),
                    'zip': components.get('postal_code', ''),
                    'city': components.get('locality', components.get('sublocality', '')),
                }
        except Exception as e:
            logger.warning(f"Google geocoding failed: {e}")

    # --- 2) US Census Bureau Geocoder (FREE, no key, very reliable for US) ---
    try:
        resp = requests.get(
            'https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress',
            params={
                'address': address,
                'benchmark': 'Public_AR_Current',
                'vintage': 'Current_Current',
                'format': 'json'
            },
            headers={'User-Agent': 'OfferWise/1.0 (risk-check)'},
            timeout=15
        )
        data = resp.json()
        matches = data.get('result', {}).get('addressMatches', [])
        if matches:
            match = matches[0]
            coords = match.get('coordinates', {})
            addr_parts = match.get('addressComponents', {})
            geo_info = match.get('geographies', {})

            # Extract state abbreviation
            state_abbr = addr_parts.get('state', '')

            # Extract county from geographies
            county = ''
            counties = geo_info.get('Counties', [])
            if counties:
                county = counties[0].get('BASENAME', '')

            city = addr_parts.get('city', '')
            zip_code = addr_parts.get('zip', '')

            formatted = match.get('matchedAddress', address)

            return {
                'lat': float(coords.get('y', 0)),
                'lng': float(coords.get('x', 0)),
                'formatted': formatted,
                'state': state_abbr,
                'county': county.replace(' County', ''),
                'zip': zip_code,
                'city': city,
            }
        else:
            logger.warning(f"Census geocoder: no matches for '{address}'")
    except Exception as e:
        logger.warning(f"Census geocoding failed: {e}")

    # --- 3) Nominatim fallback (free, 1 req/sec) ---
    try:
        resp = requests.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': address, 'format': 'json', 'limit': 1, 'addressdetails': 1, 'countrycodes': 'us'},
            headers={'User-Agent': 'OfferWise/1.0 (contact@getofferwise.ai)'},
            timeout=10
        )
        data = resp.json()
        if data:
            result = data[0]
            addr = result.get('address', {})
            return {
                'lat': float(result['lat']),
                'lng': float(result['lon']),
                'formatted': result.get('display_name', address),
                'state': addr.get('state', ''),
                'county': addr.get('county', '').replace(' County', ''),
                'zip': addr.get('postcode', ''),
                'city': addr.get('city', addr.get('town', addr.get('village', ''))),
            }
    except Exception as e:
        logger.warning(f"Nominatim geocoding failed: {e}")

    return None


# ---------------------------------------------------------------------------
# RISK CHECKS (all free APIs)
# ---------------------------------------------------------------------------

def _haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def check_fema_flood(lat, lng):
    """FEMA National Flood Hazard Layer. FREE, no key."""
    try:
        resp = requests.get(
            'https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query',
            params={
                'geometry': f'{lng},{lat}',
                'geometryType': 'esriGeometryPoint',
                'inSR': '4326',
                'spatialRel': 'esriSpatialRelIntersects',
                'outFields': 'FLD_ZONE,ZONE_SUBTY,SFHA_TF',
                'returnGeometry': 'false',
                'f': 'json'
            },
            headers={'User-Agent': 'OfferWise/1.0 (risk-check)'},
            timeout=15
        )
        data = resp.json()
        features = data.get('features', [])

        if not features:
            return {'zone': 'X', 'level': 'MINIMAL', 'in_sfha': False,
                    'detail': 'Not in a mapped FEMA flood hazard area.'}

        attrs = features[0].get('attributes', {})
        zone = attrs.get('FLD_ZONE', 'X')
        sfha = attrs.get('SFHA_TF', 'F') == 'T'

        if zone in ('A', 'AE', 'AH', 'AO', 'AR', 'V', 'VE'):
            return {'zone': zone, 'level': 'HIGH', 'in_sfha': True,
                    'detail': f'FEMA Zone {zone} — Special Flood Hazard Area. Flood insurance is REQUIRED for federally-backed mortgages. Average flood claim: $42,000.'}
        elif zone in ('B', 'X500') or 'SHADED' in str(attrs.get('ZONE_SUBTY', '')).upper():
            return {'zone': zone, 'level': 'MODERATE', 'in_sfha': False,
                    'detail': f'FEMA Zone {zone} — Moderate flood risk (0.2% annual chance). Insurance recommended.'}
        else:
            return {'zone': zone, 'level': 'MINIMAL', 'in_sfha': False,
                    'detail': 'Minimal flood risk based on current FEMA mapping.'}
    except Exception as e:
        logger.warning(f"FEMA flood check failed: {e}")
        return None


def check_earthquakes(lat, lng):
    """USGS Earthquake Catalog — significant quakes within 50km, last 30 years. FREE."""
    try:
        resp = requests.get(
            'https://earthquake.usgs.gov/fdsnws/event/1/query',
            params={
                'format': 'geojson',
                'latitude': lat,
                'longitude': lng,
                'maxradiuskm': 50,
                'minmagnitude': 3.0,
                'starttime': '1994-01-01',
                'orderby': 'magnitude',
                'limit': 20
            },
            headers={'User-Agent': 'OfferWise/1.0 (risk-check)'},
            timeout=15
        )
        data = resp.json()
        quakes = data.get('features', [])

        if not quakes:
            return {'count': 0, 'level': 'MINIMAL', 'largest_magnitude': None,
                    'nearest_dist_km': None, 'notable': [],
                    'detail': 'No significant earthquakes (M3.0+) within 50km in the last 30 years.'}

        largest = max(quakes, key=lambda q: q['properties'].get('mag', 0))
        largest_mag = largest['properties'].get('mag', 0)

        nearest_dist = 999
        for q in quakes:
            coords = q['geometry']['coordinates']
            dist = _haversine(lat, lng, coords[1], coords[0])
            nearest_dist = min(nearest_dist, dist)

        count = len(quakes)
        if nearest_dist < 10 or largest_mag >= 5.0:
            level = 'HIGH'
        elif nearest_dist < 25 or largest_mag >= 4.0:
            level = 'MODERATE'
        else:
            level = 'LOW'

        notable = []
        for q in sorted(quakes, key=lambda q: -q['properties'].get('mag', 0))[:5]:
            p = q['properties']
            notable.append({'magnitude': p.get('mag'), 'place': p.get('place', ''),
                            'year': datetime.fromtimestamp(p['time'] / 1000).year if p.get('time') else None})

        return {
            'count': count, 'level': level,
            'largest_magnitude': largest_mag,
            'nearest_dist_km': round(nearest_dist, 1),
            'notable': notable,
            'detail': f'{count} earthquakes (M3.0+) within 50km since 1994. Largest: M{largest_mag}. Nearest: {round(nearest_dist, 1)}km.'
        }
    except Exception as e:
        logger.warning(f"Earthquake check failed: {e}")
        return None


def check_disaster_history(state, county):
    """OpenFEMA Disaster Declarations. FREE, no key."""
    try:
        params = {
            '$filter': f"state eq '{state}'",
            '$orderby': 'declarationDate desc',
            '$top': 100
        }
        resp = requests.get(
            'https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries',
            params=params,
            headers={'User-Agent': 'OfferWise/1.0 (risk-check)'},
            timeout=15
        )
        data = resp.json()
        declarations = data.get('DisasterDeclarationsSummaries', [])

        county_hits = []
        for d in declarations:
            area = d.get('designatedArea', '')
            if county and county.lower() in area.lower():
                county_hits.append({
                    'type': d.get('incidentType', ''),
                    'year': d.get('declarationDate', '')[:4],
                    'title': d.get('declarationTitle', ''),
                })

        # Fall back to statewide if no county matches
        relevant = county_hits
        scope = 'county'
        if not county_hits:
            scope = 'state'
            seen = set()
            for d in declarations[:30]:
                title = d.get('declarationTitle', '')
                if title not in seen:
                    seen.add(title)
                    relevant.append({
                        'type': d.get('incidentType', ''),
                        'year': d.get('declarationDate', '')[:4],
                        'title': title,
                    })

        count = len(relevant)
        types = {}
        for d in relevant:
            t = d['type']
            types[t] = types.get(t, 0) + 1

        if count > 10:
            level = 'HIGH'
        elif count > 4:
            level = 'MODERATE'
        elif count > 0:
            level = 'LOW'
        else:
            level = 'MINIMAL'

        type_summary = ', '.join(f'{v} {k}' for k, v in sorted(types.items(), key=lambda x: -x[1])[:3])
        return {
            'count': count, 'level': level, 'types': types, 'scope': scope,
            'recent': relevant[:6],
            'detail': f'{count} FEMA disaster declarations in this {scope}. Includes: {type_summary}.'
        }
    except Exception as e:
        logger.warning(f"Disaster history check failed: {e}")
        return None


def check_california_hazards(lat, lng, state):
    """CAL FIRE severity zones + CGS fault map. FREE. CA only."""
    if state not in ('CA', 'California'):
        return None

    results = {'wildfire': None, 'fault_zone': None}

    # --- Wildfire severity (CAL FIRE FHSZ via FRAP) ---
    # Uses envelope query since point queries miss polygon boundaries
    try:
        delta = 0.005  # ~500m buffer
        resp = requests.get(
            'https://egis.fire.ca.gov/arcgis/rest/services/FRAP/HHZ_ref_FHSZ/MapServer/0/query',
            params={
                'geometry': f'{lng - delta},{lat - delta},{lng + delta},{lat + delta}',
                'geometryType': 'esriGeometryEnvelope',
                'inSR': '4326',
                'spatialRel': 'esriSpatialRelIntersects',
                'outFields': 'FHSZ9',
                'returnGeometry': 'false',
                'f': 'json',
                'resultRecordCount': 5
            },
            headers={'User-Agent': 'OfferWise/1.0 (risk-check)'},
            timeout=15
        )
        data = resp.json()
        features = data.get('features', [])
        if features:
            # Find worst severity among returned zones
            worst = ''
            for feat in features:
                fhsz = str(feat.get('attributes', {}).get('FHSZ9', ''))
                if 'VeryHigh' in fhsz:
                    worst = 'VeryHigh'
                    break
                elif 'High' in fhsz and worst != 'VeryHigh':
                    worst = 'High'
                elif 'Moderate' in fhsz and worst not in ('VeryHigh', 'High'):
                    worst = 'Moderate'

            if worst == 'VeryHigh':
                results['wildfire'] = {
                    'level': 'VERY HIGH',
                    'detail': 'Very High Fire Hazard Severity Zone (VHFHSZ). Special construction, brush clearance, and fire insurance required. Some insurers refuse coverage entirely.'
                }
            elif worst == 'High':
                results['wildfire'] = {
                    'level': 'HIGH',
                    'detail': 'High Fire Hazard Severity Zone. Fire-resistant construction and defensible space required. Insurance premiums significantly elevated.'
                }
            elif worst == 'Moderate':
                results['wildfire'] = {
                    'level': 'MODERATE',
                    'detail': 'Moderate Fire Hazard Severity Zone. Some elevated risk.'
                }
    except Exception as e:
        logger.warning(f"CA fire check failed: {e}")

    # --- Earthquake faults (CGS Fault Activity Map) ---
    try:
        delta = 0.1  # ~10km search radius
        resp = requests.get(
            'https://gis.conservation.ca.gov/server/rest/services/CGS/FAM_QFaults/FeatureServer/0/query',
            params={
                'geometry': f'{lng - delta},{lat - delta},{lng + delta},{lat + delta}',
                'geometryType': 'esriGeometryEnvelope',
                'inSR': '4326',
                'spatialRel': 'esriSpatialRelIntersects',
                'outFields': 'FLT_NAME,ZN_NAME',
                'returnGeometry': 'false',
                'f': 'json',
                'resultRecordCount': 10
            },
            headers={'User-Agent': 'OfferWise/1.0 (risk-check)'},
            timeout=15
        )
        data = resp.json()
        faults = data.get('features', [])
        if faults:
            names = list({f['attributes'].get('FLT_NAME') or f['attributes'].get('ZN_NAME', 'Unknown') for f in faults})
            # Filter out None/empty names
            names = [n for n in names if n and n != 'Unknown' and n != 'None']
            if not names:
                names = ['Unnamed fault']
            results['fault_zone'] = {
                'count': len(faults), 'names': names[:3],
                'level': 'HIGH' if len(faults) > 2 else 'MODERATE',
                'detail': f'{len(faults)} mapped fault(s) within ~10km: {", ".join(names[:3])}.'
            }
    except Exception as e:
        logger.warning(f"CA fault check failed: {e}")

    return results


def check_air_quality(lat, lng):
    """EPA AirNow current AQI. Needs AIRNOW_API_KEY."""
    api_key = os.environ.get('AIRNOW_API_KEY')
    if not api_key:
        return None
    try:
        resp = requests.get(
            'https://www.airnowapi.org/aq/observation/latLong/current/',
            params={
                'format': 'application/json',
                'latitude': lat, 'longitude': lng,
                'distance': 25, 'API_KEY': api_key
            },
            headers={'User-Agent': 'OfferWise/1.0 (risk-check)'},
            timeout=15
        )
        data = resp.json()
        if not data:
            return None
        worst = max(data, key=lambda x: x.get('AQI', 0))
        aqi = worst.get('AQI', 0)
        cat = worst.get('Category', {}).get('Name', 'Unknown')
        if aqi > 150:
            level = 'HIGH'
        elif aqi > 100:
            level = 'MODERATE'
        else:
            level = 'GOOD'
        return {'aqi': aqi, 'category': cat, 'level': level,
                'detail': f'Current Air Quality Index: {aqi} ({cat}).'}
    except Exception as e:
        logger.warning(f"Air quality check failed: {e}")
        return None


# ---------------------------------------------------------------------------
# PHASE 1: EPA ENVIRONMENTAL HAZARDS (Superfund, TRI, Hazardous Waste, Brownfields)
# ---------------------------------------------------------------------------

EPA_EMEF_BASE = 'https://geopub.epa.gov/arcgis/rest/services/EMEF/efpoints/MapServer'

# Layer IDs on the EPA EMEF ArcGIS service
EPA_LAYERS = {
    'superfund':  {'id': 0, 'radius_m': 4828, 'label': 'Superfund Sites'},    # 3 miles
    'tri':        {'id': 1, 'radius_m': 4828, 'label': 'Toxic Release Facilities'},  # 3 miles
    'hazwaste':   {'id': 4, 'radius_m': 1609, 'label': 'Hazardous Waste Handlers'},  # 1 mile
    'brownfields': {'id': 5, 'radius_m': 4828, 'label': 'Brownfield Sites'},   # 3 miles
}


def _query_epa_layer(layer_id, lat, lng, radius_m, max_results=20):
    """Query a single EPA EMEF ArcGIS layer by point + radius."""
    try:
        resp = requests.get(
            f'{EPA_EMEF_BASE}/{layer_id}/query',
            params={
                'geometry': f'{lng},{lat}',
                'geometryType': 'esriGeometryPoint',
                'inSR': '4326',
                'spatialRel': 'esriSpatialRelIntersects',
                'distance': radius_m,
                'units': 'esriSRUnit_Meter',
                'outFields': 'primary_name,city_name,latitude,longitude',
                'returnGeometry': 'false',
                'resultRecordCount': max_results,
                'f': 'json',
            },
            headers={'User-Agent': 'OfferWise/1.0 (risk-check)'},
            timeout=12,
        )
        data = resp.json()
        features = data.get('features', [])
        sites = []
        for f in features:
            a = f.get('attributes', {})
            name = a.get('primary_name', 'Unknown')
            city = a.get('city_name', '')
            site_lat = a.get('latitude')
            site_lng = a.get('longitude')
            dist_mi = None
            if site_lat and site_lng:
                dist_mi = round(_haversine_miles(lat, lng, float(site_lat), float(site_lng)), 1)
            sites.append({'name': name, 'city': city, 'distance_mi': dist_mi})
        return sites
    except Exception as e:
        logger.warning(f"EPA layer {layer_id} query failed: {e}")
        return []


def _count_epa_layer(layer_id, lat, lng, radius_m):
    """Get exact count from an EPA EMEF layer (no feature data)."""
    try:
        resp = requests.get(
            f'{EPA_EMEF_BASE}/{layer_id}/query',
            params={
                'geometry': f'{lng},{lat}',
                'geometryType': 'esriGeometryPoint',
                'inSR': '4326',
                'spatialRel': 'esriSpatialRelIntersects',
                'distance': radius_m,
                'units': 'esriSRUnit_Meter',
                'returnCountOnly': 'true',
                'f': 'json',
            },
            headers={'User-Agent': 'OfferWise/1.0 (risk-check)'},
            timeout=12,
        )
        return resp.json().get('count', 0)
    except Exception as e:
        logger.warning(f"EPA layer {layer_id} count failed: {e}")
        return 0


def _haversine_miles(lat1, lon1, lat2, lon2):
    """Distance in miles between two lat/lon points."""
    R = 3958.8  # Earth radius in miles
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))


def check_epa_environmental(lat, lng):
    """
    Query EPA EMEF for Superfund, TRI, Hazardous Waste, and Brownfield sites
    near the property. All 4 layers share the same ArcGIS endpoint.
    FREE — no API key required.
    """
    results = {}

    for key, config in EPA_LAYERS.items():
        # RCRA (hazwaste) has thousands of small handlers — only need count, not features
        if key == 'hazwaste':
            sites = []
        else:
            sites = _query_epa_layer(config['id'], lat, lng, config['radius_m'])
        radius_mi = round(config['radius_m'] / 1609.34, 1)

        if key == 'superfund':
            count = len(sites)
            closest = min((s['distance_mi'] for s in sites if s['distance_mi']), default=None) if sites else None
            if count > 0 and closest and closest <= 1.0:
                level = 'HIGH'
                detail = f'{count} EPA Superfund site{"s" if count > 1 else ""} within {radius_mi} miles. Closest is {closest} miles away — {sites[0]["name"]} in {sites[0]["city"]}. Properties within 1 mile of NPL sites lose 10-15% of value on average.'
            elif count > 0:
                level = 'MODERATE'
                detail = f'{count} EPA Superfund site{"s" if count > 1 else ""} within {radius_mi} miles. Nearest: {sites[0]["name"]} ({closest} mi). Contaminated land can affect groundwater and soil quality in surrounding areas.'
            else:
                level = 'NONE'
                detail = f'No EPA Superfund sites found within {radius_mi} miles.'
            results['superfund'] = {'count': count, 'level': level, 'detail': detail,
                                    'closest_mi': closest, 'sites': [s['name'] for s in sites[:3]]}

        elif key == 'tri':
            count = len(sites)
            # If we hit the result cap, get the real count
            if count >= 20:
                count = _count_epa_layer(config['id'], lat, lng, config['radius_m'])
            if count >= 5:
                level = 'HIGH'
                detail = f'{count} facilities reporting toxic chemical releases within {radius_mi} miles. This is a high-density industrial corridor. Facilities include: {", ".join(s["name"] for s in sites[:3])}.'
            elif count >= 1:
                level = 'MODERATE'
                names = ", ".join(s["name"] for s in sites[:3])
                detail = f'{count} TRI-reporting facilit{"ies" if count > 1 else "y"} within {radius_mi} miles: {names}. These facilities release EPA-tracked toxic chemicals annually.'
            else:
                level = 'NONE'
                detail = f'No toxic release facilities found within {radius_mi} miles.'
            results['tri'] = {'count': count, 'level': level, 'detail': detail,
                              'sites': [s['name'] for s in sites[:5]]}

        elif key == 'hazwaste':
            # RCRA has thousands of small handlers — use count-only query for accuracy
            count = _count_epa_layer(config['id'], lat, lng, config['radius_m'])
            if count >= 50:
                level = 'HIGH'
                detail = f'{count} hazardous waste handlers within 1 mile. This indicates a dense industrial zone with elevated contamination risk. Groundwater and soil quality concerns are common in these areas.'
            elif count >= 10:
                level = 'MODERATE'
                detail = f'{count} hazardous waste handlers within 1 mile. Moderate industrial activity in the immediate area. Includes generators, transporters, and treatment facilities regulated under RCRA.'
            else:
                level = 'NONE'
                detail = f'{count} hazardous waste handler{"s" if count != 1 else ""} within 1 mile. Low density — typical for residential areas.'
            results['hazwaste'] = {'count': count, 'level': level, 'detail': detail}

        elif key == 'brownfields':
            count = len(sites)
            closest = min((s['distance_mi'] for s in sites if s['distance_mi']), default=None) if sites else None
            if count > 0 and closest and closest <= 1.0:
                level = 'HIGH'
                detail = f'{count} EPA Brownfield site{"s" if count > 1 else ""} within {radius_mi} miles. Closest is {closest} miles away. Brownfields are former industrial or commercial properties with known or suspected contamination undergoing cleanup.'
            elif count > 0:
                level = 'MODERATE'
                detail = f'{count} Brownfield site{"s" if count > 1 else ""} within {radius_mi} miles. These are properties with environmental contamination in various stages of assessment or remediation.'
            else:
                level = 'NONE'
                detail = f'No EPA Brownfield sites found within {radius_mi} miles.'
            results['brownfields'] = {'count': count, 'level': level, 'detail': detail,
                                      'closest_mi': closest, 'sites': [s['name'] for s in sites[:3]]}

    return results


# ---------------------------------------------------------------------------
# PHASE 1: EPA RADON ZONE LOOKUP (county-level)
# ---------------------------------------------------------------------------

# EPA Radon Zone classification by California county
# Zone 1 = Highest potential (predicted avg > 4 pCi/L)
# Zone 2 = Moderate potential (predicted avg 2-4 pCi/L)
# Zone 3 = Low potential (predicted avg < 2 pCi/L)
# Source: https://www.epa.gov/radon/epa-map-radon-zones
CA_RADON_ZONES = {
    # Zone 1 (Highest)
    'Santa Barbara': 1, 'Ventura': 1,
    # Zone 2 (Moderate)
    'Fresno': 2, 'Kern': 2, 'Kings': 2, 'Los Angeles': 2, 'Madera': 2,
    'Merced': 2, 'Orange': 2, 'Riverside': 2, 'San Bernardino': 2,
    'San Luis Obispo': 2, 'Stanislaus': 2, 'Tulare': 2,
    # Zone 3 (Low) — all others default to 3
}

# Common high-radon counties outside California (for future expansion)
HIGH_RADON_STATES = {
    'IA', 'ND', 'SD', 'NE', 'KS', 'MN', 'WI', 'OH', 'PA', 'CO', 'MT', 'WY', 'ID', 'UT'
}


def check_radon_zone(county, state):
    """
    Look up EPA radon zone for the property's county.
    Currently detailed data for California. Other states get a general estimate.
    FREE — static lookup, no API call.
    """
    try:
        if not county or not state:
            return None

        # Clean county name
        clean_county = county.replace(' County', '').replace(' Parish', '').strip()

        if state in ('CA', 'California'):
            zone = CA_RADON_ZONES.get(clean_county, 3)
        elif state in HIGH_RADON_STATES or (len(state) > 2 and state[:2] in HIGH_RADON_STATES):
            zone = 1  # Conservative for known high-radon states
        else:
            zone = 3  # Default to low

        if zone == 1:
            level = 'HIGH'
            detail = f'EPA Radon Zone 1 (highest potential) for {clean_county} County. Indoor radon levels predicted to average above 4 pCi/L — the EPA action level. Radon is the #2 cause of lung cancer. Testing costs $15-$30 and mitigation runs $800-$2,500.'
        elif zone == 2:
            level = 'MODERATE'
            detail = f'EPA Radon Zone 2 (moderate potential) for {clean_county} County. Indoor levels predicted at 2-4 pCi/L. EPA recommends testing all homes regardless of zone. Mitigation may be needed if levels exceed 4 pCi/L.'
        else:
            level = 'LOW'
            detail = f'EPA Radon Zone 3 (low potential) for {clean_county} County. Predicted average below 2 pCi/L, though elevated levels have been found in all 3 zones.'

        return {'zone': zone, 'level': level, 'detail': detail, 'county': clean_county}
    except Exception as e:
        logger.warning(f"Radon zone check failed: {e}")
        return None

def calculate_risk_exposure(flood, earthquakes, disasters, ca_hazards, air_quality,
                            epa_environmental=None, radon=None):
    """Aggregate all risk checks into a total dollar exposure + risk cards."""
    total = 0
    risks = []

    # --- Flood ---
    if flood and flood['level'] in ('HIGH', 'MODERATE'):
        cost = RISK_COSTS['flood_high'] if flood['level'] == 'HIGH' else RISK_COSTS['flood_moderate']
        total += cost
        risks.append({
            'category': 'flood', 'icon': '🌊', 'title': 'Flood Zone',
            'level': flood['level'], 'cost': cost,
            'detail': flood['detail'],
            'seller_hide': 'Sellers routinely check "No" on flooding questions even when the property sits in a FEMA Special Flood Hazard Area. They may not know — or may not want you to.'
        })

    # --- Seismic activity ---
    if earthquakes and earthquakes['level'] not in ('MINIMAL', None):
        cost = {'HIGH': RISK_COSTS['earthquake_close'],
                'MODERATE': RISK_COSTS['earthquake_moderate'],
                'LOW': RISK_COSTS['earthquake_far']}.get(earthquakes['level'], 0)
        if cost:
            total += cost
            risks.append({
                'category': 'earthquake', 'icon': '🌍', 'title': 'Seismic Activity',
                'level': earthquakes['level'], 'cost': cost,
                'detail': earthquakes['detail'],
                'seller_hide': 'Sellers rarely disclose seismic history or whether the home has been retrofitted. Foundation cracks may be concealed behind drywall or fresh paint.'
            })

    # --- Wildfire (CA) ---
    if ca_hazards and ca_hazards.get('wildfire'):
        wf = ca_hazards['wildfire']
        cost = {'VERY HIGH': RISK_COSTS['wildfire_very_high'],
                'HIGH': RISK_COSTS['wildfire_high'],
                'MODERATE': RISK_COSTS['wildfire_moderate']}.get(wf['level'], 0)
        if cost:
            total += cost
            risks.append({
                'category': 'wildfire', 'icon': '🔥', 'title': 'Wildfire Zone',
                'level': wf['level'], 'cost': cost,
                'detail': wf['detail'],
                'seller_hide': 'Wildfire zones can make homeowner insurance unavailable or 5-10x more expensive. Sellers almost never volunteer this information on disclosure forms.'
            })

    # --- Fault zones (CA) ---
    if ca_hazards and ca_hazards.get('fault_zone'):
        fz = ca_hazards['fault_zone']
        cost = RISK_COSTS['fault_zone_high'] if fz['level'] == 'HIGH' else RISK_COSTS['fault_zone_moderate']
        total += cost
        risks.append({
            'category': 'fault_zone', 'icon': '⚡', 'title': 'Earthquake Fault Zone',
            'level': fz['level'], 'cost': cost,
            'detail': fz['detail'],
            'seller_hide': 'California law requires Alquist-Priolo fault zone disclosure, but many sellers skip or minimize it. Properties near active faults need seismic retrofitting that can cost $10,000-$40,000.'
        })

    # --- Disaster history ---
    if disasters and disasters['level'] not in ('MINIMAL', None):
        cost = {'HIGH': RISK_COSTS['disaster_heavy'],
                'MODERATE': RISK_COSTS['disaster_moderate'],
                'LOW': RISK_COSTS['disaster_light']}.get(disasters['level'], 0)
        if cost:
            total += cost
            risks.append({
                'category': 'disasters', 'icon': '⚠️', 'title': 'Disaster History',
                'level': disasters['level'], 'cost': cost,
                'detail': disasters['detail'],
                'seller_hide': 'Repeated disasters mean higher insurance premiums, potential undisclosed prior damage, and elevated risk of future events. Sellers rarely volunteer this context.'
            })

    # --- Air quality ---
    if air_quality and air_quality['level'] in ('HIGH', 'MODERATE'):
        cost = RISK_COSTS['air_quality_high'] if air_quality['level'] == 'HIGH' else RISK_COSTS['air_quality_moderate']
        total += cost
        risks.append({
            'category': 'air_quality', 'icon': '💨', 'title': 'Air Quality Concern',
            'level': air_quality['level'], 'cost': cost,
            'detail': air_quality['detail'],
            'seller_hide': 'Poor air quality depresses property values and increases long-term health costs. This is never mentioned on seller disclosures.'
        })

    # --- EPA Superfund Sites ---
    if epa_environmental and epa_environmental.get('superfund'):
        sf = epa_environmental['superfund']
        if sf['level'] in ('HIGH', 'MODERATE'):
            cost = RISK_COSTS['superfund_close'] if sf['level'] == 'HIGH' else RISK_COSTS['superfund_moderate']
            total += cost
            risks.append({
                'category': 'superfund', 'icon': '☣️', 'title': 'Superfund Toxic Site',
                'level': sf['level'], 'cost': cost,
                'detail': sf['detail'],
                'seller_hide': 'Superfund sites contain hazardous substances that contaminate soil, groundwater, and air. Sellers almost never disclose proximity to these sites. Properties near NPL sites face depressed values, difficult-to-obtain insurance, and potential health risks.'
            })

    # --- EPA Toxic Release Inventory ---
    if epa_environmental and epa_environmental.get('tri'):
        tri = epa_environmental['tri']
        if tri['level'] in ('HIGH', 'MODERATE'):
            cost = RISK_COSTS['tri_high'] if tri['level'] == 'HIGH' else RISK_COSTS['tri_moderate']
            total += cost
            risks.append({
                'category': 'tri', 'icon': '🏭', 'title': 'Toxic Chemical Releases',
                'level': tri['level'], 'cost': cost,
                'detail': tri['detail'],
                'seller_hide': 'Over 650 toxic chemicals are tracked by EPA. Nearby facilities release these into the air, water, and soil annually. Sellers never disclose industrial chemical exposure risks on disclosure forms.'
            })

    # --- EPA Hazardous Waste ---
    if epa_environmental and epa_environmental.get('hazwaste'):
        hw = epa_environmental['hazwaste']
        if hw['level'] in ('HIGH', 'MODERATE'):
            cost = RISK_COSTS['hazwaste_high'] if hw['level'] == 'HIGH' else RISK_COSTS['hazwaste_moderate']
            total += cost
            risks.append({
                'category': 'hazwaste', 'icon': '⚗️', 'title': 'Hazardous Waste Zone',
                'level': hw['level'], 'cost': cost,
                'detail': hw['detail'],
                'seller_hide': 'RCRA-regulated facilities handle materials that can contaminate groundwater and soil for decades. High density of hazardous waste handlers indicates industrial zoning that affects property values and livability.'
            })

    # --- EPA Brownfields ---
    if epa_environmental and epa_environmental.get('brownfields'):
        bf = epa_environmental['brownfields']
        if bf['level'] in ('HIGH', 'MODERATE'):
            cost = RISK_COSTS['brownfield_close'] if bf['level'] == 'HIGH' else RISK_COSTS['brownfield_moderate']
            total += cost
            risks.append({
                'category': 'brownfields', 'icon': '🏚️', 'title': 'Brownfield Contamination',
                'level': bf['level'], 'cost': cost,
                'detail': bf['detail'],
                'seller_hide': 'Brownfields are former gas stations, dry cleaners, factories, and industrial sites with known contamination. Adjacent properties face soil and groundwater concerns that sellers rarely disclose.'
            })

    # --- Radon ---
    if radon and radon['level'] in ('HIGH', 'MODERATE'):
        cost = RISK_COSTS['radon_high'] if radon['level'] == 'HIGH' else RISK_COSTS['radon_moderate']
        total += cost
        risks.append({
            'category': 'radon', 'icon': '☢️', 'title': 'Radon Exposure Risk',
            'level': radon['level'], 'cost': cost,
            'detail': radon['detail'],
            'seller_hide': 'Radon is an invisible, odorless radioactive gas — the #2 cause of lung cancer after smoking. Most sellers have never tested for it. In most states, radon disclosure is not required even if levels are known to be elevated.'
        })

    # Grade
    if total >= 60000:
        grade = 'F'
    elif total >= 40000:
        grade = 'D'
    elif total >= 25000:
        grade = 'C'
    elif total >= 10000:
        grade = 'B'
    elif total > 0:
        grade = 'B+'
    else:
        grade = 'A'

    return {
        'total_exposure': total,
        'grade': grade,
        'risk_count': len(risks),
        'risks': sorted(risks, key=lambda r: -r['cost']),
    }


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

def run_risk_check(address):
    """Run a full risk check on an address. Returns structured result dict."""
    # Check cache
    import hashlib
    cache_key = hashlib.md5(address.lower().strip().encode()).hexdigest()
    if cache_key in _cache:
        entry = _cache[cache_key]
        if time.time() - entry['ts'] < CACHE_TTL:
            logger.info(f"Risk check cache hit: {address}")
            return entry['result']

    start = time.time()

    # Geocode
    geo = geocode_address(address)
    if not geo:
        return {'error': 'Could not find that address. Please enter a full US street address with city and state.'}

    lat, lng = geo['lat'], geo['lng']
    state = geo['state']
    county = geo['county']

    # Parallel risk checks — 8 workers for all sources, with circuit breaker (v5.61.0)
    results = {}

    def guarded_call(name, fn, *args):
        """Wrap API call with circuit breaker."""
        if _breaker.is_open(name):
            logger.info(f"⚡ Skipping {name} — circuit open")
            return None
        try:
            result = fn(*args)
            _breaker.record_success(name)
            return result
        except Exception as e:
            _breaker.record_failure(name)
            raise

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(guarded_call, 'fema_flood', check_fema_flood, lat, lng): 'flood',
            pool.submit(guarded_call, 'usgs_earthquakes', check_earthquakes, lat, lng): 'earthquakes',
            pool.submit(guarded_call, 'openfema_disasters', check_disaster_history, state, county): 'disasters',
            pool.submit(guarded_call, 'epa_airnow', check_air_quality, lat, lng): 'air_quality',
            pool.submit(guarded_call, 'epa_environmental', check_epa_environmental, lat, lng): 'epa_environmental',
            pool.submit(guarded_call, 'epa_radon', check_radon_zone, county, state): 'radon',
        }
        if state in ('CA', 'California'):
            futures[pool.submit(guarded_call, 'cal_fire', check_california_hazards, lat, lng, state)] = 'ca_hazards'

        try:
            for future in as_completed(futures, timeout=30):
                key = futures[future]
                try:
                    results[key] = future.result()
                except Exception as e:
                    logger.warning(f"Risk check {key} error: {e}")
                    results[key] = None
        except TimeoutError:
            logger.warning("Risk check: some API calls timed out, returning partial results")
            for future, key in futures.items():
                if key not in results:
                    results[key] = None

    # Calculate exposure
    exposure = calculate_risk_exposure(
        flood=results.get('flood'),
        earthquakes=results.get('earthquakes'),
        disasters=results.get('disasters'),
        ca_hazards=results.get('ca_hazards'),
        air_quality=results.get('air_quality'),
        epa_environmental=results.get('epa_environmental'),
        radon=results.get('radon'),
    )

    # Count total data sources checked
    source_count = 6  # base: FEMA, USGS, OpenFEMA, AirNow, EPA EMEF (4 layers), EPA Radon
    source_list = ['FEMA NFHL', 'USGS Earthquakes', 'OpenFEMA Disasters', 'EPA AirNow',
                   'EPA Superfund (SEMS)', 'EPA Toxic Release Inventory',
                   'EPA Hazardous Waste (RCRA)', 'EPA Brownfields', 'EPA Radon Zones']
    if state in ('CA', 'California'):
        source_list.extend(['CAL FIRE', 'CGS Fault Zones'])

    duration = int((time.time() - start) * 1000)

    result = {
        'address': geo['formatted'],
        'city': geo['city'],
        'state': state,
        'county': county,
        'zip': geo['zip'],
        'latitude': lat,
        'longitude': lng,
        'risk_exposure': exposure['total_exposure'],
        'risk_grade': exposure['grade'],
        'risk_count': exposure['risk_count'],
        'risks': exposure['risks'],
        'disaster_summary': results.get('disasters'),
        'earthquake_summary': results.get('earthquakes'),
        'epa_environmental': results.get('epa_environmental'),
        'radon': results.get('radon'),
        'scan_time_ms': duration,
        'source_count': len(source_list),
        'sources': ', '.join(source_list),
    }

    # Cache
    _cache[cache_key] = {'result': result, 'ts': time.time()}

    logger.info(f"🔍 Risk check: {geo['formatted']} → ${exposure['total_exposure']:,} exposure, grade {exposure['grade']} ({duration}ms, {len(source_list)} sources)")
    return result
