"""
OfferWise External Cost Data Crawler
=====================================
Collects real-world repair cost data from public sources to train the
Repair Cost Predictor model.

Sources:
1. Public permit databases (LA, SF, NYC, Chicago, Seattle)
2. HomeAdvisor / Angi published cost guides
3. FEMA disaster repair cost data
4. State insurance department aggregate claims

All data is stored in MLCostData table with source attribution.
"""
import logging
import re
import json
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# SOURCE 1: PUBLIC PERMIT DATABASES
# ═══════════════════════════════════════════════════════════

# Socrata Open Data API endpoints (no auth required for public datasets)
# These return JSON; docs at https://dev.socrata.com/
PERMIT_SOURCES = {
    'san_francisco': {
        'url': 'https://data.sfgov.org/resource/i98e-djp9.json',
        'permit_type_field': 'permit_type_definition',
        'value_field': 'estimated_cost',
        'address_field': 'street_number',
        'zip_field': 'zipcode',
        'date_field': 'filed_date',
        'description_field': 'description',
    },
    'seattle': {
        'url': 'https://data.seattle.gov/resource/76t5-zqzr.json',
        'permit_type_field': 'permittype',
        'value_field': 'estproj_cost',
        'address_field': 'originaladdress1',
        'zip_field': 'originalzip',
        'date_field': 'issueddate',
        'description_field': 'description',
    },
    'chicago': {
        'url': 'https://data.cityofchicago.org/resource/ydr8-5enu.json',
        'permit_type_field': 'permit_type',
        'value_field': 'reported_cost',
        'address_field': 'street_name',
        'zip_field': 'contact_1_zipcode',
        'date_field': 'issue_date',
        'description_field': 'work_description',
    },
    'nyc': {
        'url': 'https://data.cityofnewyork.us/resource/ipu4-2q9a.json',
        'permit_type_field': 'job_type',
        'value_field': 'total_est_fee',
        'address_field': 'house__',
        'zip_field': 'zip_code',
        'date_field': 'job_start_date',
        'description_field': 'job_description',
    },
    'austin': {
        'url': 'https://data.austintexas.gov/resource/3syk-w9eu.json',
        'permit_type_field': 'permit_type_desc',
        'value_field': 'total_valuation',
        'address_field': 'original_address',
        'zip_field': 'original_zip',
        'date_field': 'issued_date',
        'description_field': 'work_description',
    },
}

# Violation/complaint datasets from major cities (Socrata API)
VIOLATION_SOURCES = {
    'nyc_hpd': {
        'url': 'https://data.cityofnewyork.us/resource/wvxf-dwi5.json',
        'desc_field': 'novdescription',
        'severity_field': 'class',
        'zip_field': 'zip',
        'date_field': 'inspectiondate',
        'severity_map': {'A': 'minor', 'B': 'moderate', 'C': 'major'},
    },
    'nyc_dob': {
        'url': 'https://data.cityofnewyork.us/resource/3h2n-5cm9.json',
        'desc_field': 'description',
        'severity_field': 'violation_category',
        'zip_field': None,
        'date_field': 'issue_date',
        'severity_map': {},  # parsed from text
    },
    'nyc_311_housing': {
        'url': 'https://data.cityofnewyork.us/resource/erm2-nwe9.json',
        'desc_field': 'descriptor',
        'severity_field': 'complaint_type',
        'zip_field': 'incident_zip',
        'date_field': 'created_date',
        'filter': "complaint_type in('HEAT/HOT WATER','PLUMBING','WATER SYSTEM','ELECTRIC','PAINT/PLASTER','DOOR/WINDOW','ELEVATOR','FLOORING/STAIRCASE','GENERAL CONSTRUCTION','SAFETY')",
        'severity_map': {},
    },
}

# Keyword patterns to classify permit descriptions into our categories
CATEGORY_KEYWORDS = {
    'roof_exterior': ['roof', 'shingle', 'siding', 'stucco', 'gutter', 'facade', 'exterior'],
    'foundation_structure': ['foundation', 'structural', 'retaining wall', 'basement wall', 'load bearing'],
    'electrical': ['electrical', 'panel', 'wiring', 'circuit', 'breaker', 'service upgrade'],
    'plumbing': ['plumbing', 'water heater', 'sewer', 'drain', 'pipe', 'fixture'],
    'hvac_systems': ['hvac', 'furnace', 'air condition', 'heat pump', 'ductwork', 'boiler'],
    'environmental': ['mold', 'asbestos', 'radon', 'lead', 'termite', 'pest'],
}


def _classify_permit(description, permit_type=''):
    """Classify a permit into a category + estimate severity from cost."""
    text = (str(description) + ' ' + str(permit_type)).lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return cat
    return 'general'


def _estimate_severity(cost):
    """Map permit cost to severity based on BASELINE_COSTS ranges."""
    if cost < 1500:
        return 'minor'
    elif cost < 5000:
        return 'moderate'
    elif cost < 15000:
        return 'major'
    else:
        return 'critical'


def fetch_permit_data(source_name, limit=10000, since_days=365, archive_to_docrepo=True):
    """Fetch recent permits from a public source.

    Returns list of dicts with standardized keys: category, severity, cost_mid,
    zip_code, description, source_city.

    If archive_to_docrepo=True, saves raw JSON response to persistent disk.
    """
    import os
    if source_name not in PERMIT_SOURCES:
        raise ValueError(f'Unknown permit source: {source_name}')

    src = PERMIT_SOURCES[source_name]
    rows = []

    try:
        import requests
        since_date = (datetime.utcnow() - timedelta(days=since_days)).strftime('%Y-%m-%d')

        # Socrata query: filter by date and min cost, limit rows
        params = {
            '$limit': limit,
            '$where': f"{src['value_field']} > 500 AND {src['date_field']} > '{since_date}'",
            '$order': f"{src['date_field']} DESC",
        }
        url = f"{src['url']}?{urlencode(params)}"

        logger.info(f'Fetching permits from {source_name}: {url[:80]}...')
        resp = requests.get(url, timeout=120)
        if resp.status_code != 200:
            # Retry without date filter (some APIs don't support date filtering)
            params_simple = {
                '$limit': limit,
                '$where': f"{src['value_field']} > 500",
                '$order': f"{src['value_field']} DESC",
            }
            url_simple = f"{src['url']}?{urlencode(params_simple)}"
            logger.info(f'  Retrying {source_name} without date filter...')
            resp = requests.get(url_simple, timeout=120)
        if resp.status_code != 200:
            # Last resort: no filter at all
            resp = requests.get(f"{src['url']}?$limit={limit}", timeout=120)
        resp.raise_for_status()
        data = resp.json()

        # Archive raw JSON response to docrepo for audit
        if archive_to_docrepo and data:
            try:
                docrepo_root = os.environ.get('DOCREPO_PATH', '/var/data/docrepo')
                archive_dir = os.path.join(docrepo_root, 'crawled', 'permits',
                                           datetime.utcnow().strftime('%Y-%m-%d'))
                os.makedirs(archive_dir, exist_ok=True)
                archive_path = os.path.join(archive_dir, f'{source_name}.json')
                with open(archive_path, 'w', encoding='utf-8') as f:
                    json.dump({
                        'source': source_name,
                        'url': url,
                        'fetched_at': datetime.utcnow().isoformat(),
                        'record_count': len(data),
                        'data': data,
                    }, f)
            except Exception as arcerr:
                logger.warning(f'Could not archive {source_name} permits: {arcerr}')

        for item in data:
            try:
                cost_raw = item.get(src['value_field'])
                if not cost_raw:
                    continue
                cost = float(cost_raw)
                if cost < 500 or cost > 500000:
                    continue  # Skip outliers

                desc = item.get(src['description_field'], '') or ''
                permit_type = item.get(src['permit_type_field'], '') or ''
                zipcode = str(item.get(src['zip_field'], ''))[:5]

                if not desc or len(desc) < 5:
                    continue

                category = _classify_permit(desc, permit_type)
                severity = _estimate_severity(cost)

                # Skip generic "general" when we can't identify a system
                if category == 'general' and cost < 2000:
                    continue

                rows.append({
                    'finding_text': f'{desc[:200]} ({permit_type})',
                    'category': category,
                    'severity': severity,
                    'cost_low': cost * 0.85,
                    'cost_high': cost * 1.15,
                    'cost_mid': cost,
                    'zip_code': zipcode,
                    'source': f'permit_{source_name}',
                    'metadata': {
                        'permit_type': permit_type,
                        'date': item.get(src['date_field'], ''),
                    },
                })
            except (ValueError, TypeError, KeyError) as e:
                continue

        logger.info(f'  {source_name}: fetched {len(rows)} usable permits from {len(data)} raw')
        return rows

    except Exception as e:
        logger.warning(f'Failed to fetch {source_name} permits: {e}')
        return []


def fetch_all_permit_sources(limit_per_source=500):
    """Fetch from all configured permit sources."""
    all_rows = []
    for source_name in PERMIT_SOURCES.keys():
        rows = fetch_permit_data(source_name, limit=limit_per_source)
        all_rows.extend(rows)
        time.sleep(1)  # Rate limit politeness
    return all_rows


# ═══════════════════════════════════════════════════════════
# SOURCE 2: HOMEADVISOR / ANGI PUBLIC PRICING GUIDES
# ═══════════════════════════════════════════════════════════

# These pages publish average costs by repair type with explicit ranges
# Links are to their "True Cost Guide" pages which are public
HOMEADVISOR_GUIDES = {
    'roof_replacement': {'url': 'https://www.homeadvisor.com/cost/roofing/install-a-roof/', 'category': 'roof_exterior', 'severity': 'critical'},
    'roof_repair': {'url': 'https://www.homeadvisor.com/cost/roofing/repair-a-roof/', 'category': 'roof_exterior', 'severity': 'major'},
    'foundation_repair': {'url': 'https://www.homeadvisor.com/cost/foundations/repair-a-foundation/', 'category': 'foundation_structure', 'severity': 'critical'},
    'electrical_panel': {'url': 'https://www.homeadvisor.com/cost/electrical/install-an-electrical-panel/', 'category': 'electrical', 'severity': 'major'},
    'water_heater': {'url': 'https://www.homeadvisor.com/cost/plumbing/install-a-water-heater/', 'category': 'plumbing', 'severity': 'moderate'},
    'sewer_repair': {'url': 'https://www.homeadvisor.com/cost/plumbing/repair-sewer-main/', 'category': 'plumbing', 'severity': 'major'},
    'hvac_install': {'url': 'https://www.homeadvisor.com/cost/heating-and-cooling/install-a-hvac-system/', 'category': 'hvac_systems', 'severity': 'critical'},
    'furnace_repair': {'url': 'https://www.homeadvisor.com/cost/heating-and-cooling/repair-a-furnace/', 'category': 'hvac_systems', 'severity': 'moderate'},
    'mold_remediation': {'url': 'https://www.homeadvisor.com/cost/environmental-safety/remove-mold/', 'category': 'environmental', 'severity': 'major'},
    'termite_treatment': {'url': 'https://www.homeadvisor.com/cost/environmental-safety/termite-treatment/', 'category': 'environmental', 'severity': 'moderate'},
    'asbestos_removal': {'url': 'https://www.homeadvisor.com/cost/environmental-safety/remove-asbestos/', 'category': 'environmental', 'severity': 'critical'},
    'radon_mitigation': {'url': 'https://www.homeadvisor.com/cost/environmental-safety/install-a-radon-mitigation-system/', 'category': 'environmental', 'severity': 'moderate'},
}


def _extract_cost_range_from_html(html):
    """Extract a cost range like '$5,000 - $15,000' from HomeAdvisor HTML."""
    # HomeAdvisor displays costs like "Typical Range: $5,000 - $12,000"
    patterns = [
        r'Typical Range[:\s]*\$([\d,]+)\s*[-–to]+\s*\$([\d,]+)',
        r'Average Cost[:\s]*\$([\d,]+)',
        r'\$([\d,]+)\s*[-–]\s*\$([\d,]+)',
    ]
    for pattern in patterns:
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            try:
                if m.lastindex == 2:
                    low = int(m.group(1).replace(',', ''))
                    high = int(m.group(2).replace(',', ''))
                    if low > 0 and high > low:
                        return low, high
                else:
                    avg = int(m.group(1).replace(',', ''))
                    return avg * 0.7, avg * 1.3
            except (ValueError, IndexError):
                continue
    return None, None


def fetch_homeadvisor_costs(archive_to_docrepo=True):
    """Scrape HomeAdvisor public cost guides for national average ranges.

    If archive_to_docrepo=True, also saves raw HTML snapshots to the docrepo
    persistent disk for audit and future re-extraction with improved parsers.
    """
    import os
    rows = []
    archive_dir = None
    if archive_to_docrepo:
        # Use same persistent disk as docrepo
        docrepo_root = os.environ.get('DOCREPO_PATH', '/var/data/docrepo')
        archive_dir = os.path.join(docrepo_root, 'crawled', 'homeadvisor',
                                    datetime.utcnow().strftime('%Y-%m-%d'))
        try:
            os.makedirs(archive_dir, exist_ok=True)
        except Exception as mkerr:
            logger.warning(f'Could not create docrepo archive dir: {mkerr}')
            archive_dir = None

    try:
        import requests
        for name, info in HOMEADVISOR_GUIDES.items():
            try:
                resp = requests.get(info['url'], timeout=30, headers={
                    'User-Agent': 'Mozilla/5.0 OfferWise ML Training Data Collector'
                })
                if resp.status_code != 200:
                    continue

                # Save raw HTML to docrepo for audit/re-extraction
                archive_path = None
                if archive_dir:
                    try:
                        archive_path = os.path.join(archive_dir, f'{name}.html')
                        with open(archive_path, 'w', encoding='utf-8') as f:
                            f.write(resp.text)
                        # Save metadata sidecar
                        meta_path = os.path.join(archive_dir, f'{name}.meta.json')
                        with open(meta_path, 'w', encoding='utf-8') as f:
                            json.dump({
                                'source_url': info['url'],
                                'fetched_at': datetime.utcnow().isoformat(),
                                'category': info['category'],
                                'severity': info['severity'],
                                'guide_name': name,
                                'http_status': resp.status_code,
                                'content_length': len(resp.text),
                            }, f, indent=2)
                    except Exception as savrr:
                        logger.warning(f'Could not archive {name}: {savrr}')
                        archive_path = None

                low, high = _extract_cost_range_from_html(resp.text)
                if low and high:
                    rows.append({
                        'finding_text': name.replace('_', ' ').title() + ' (HomeAdvisor national avg)',
                        'category': info['category'],
                        'severity': info['severity'],
                        'cost_low': low,
                        'cost_high': high,
                        'cost_mid': (low + high) / 2,
                        'zip_code': '',
                        'source': 'homeadvisor',
                        'metadata': {
                            'url': info['url'],
                            'archive_path': archive_path,  # link back to raw HTML
                            'fetched_at': datetime.utcnow().isoformat(),
                        },
                    })
                time.sleep(2)  # Be polite
            except Exception as e:
                logger.warning(f'HomeAdvisor {name} failed: {e}')
                continue
        logger.info(f'HomeAdvisor: extracted {len(rows)} cost ranges'
                    + (f' (archived to {archive_dir})' if archive_dir else ''))
        return rows
    except Exception as e:
        logger.warning(f'HomeAdvisor scraper failed: {e}')
        return []


# ═══════════════════════════════════════════════════════════
# SOURCE 3: FEMA PUBLIC DISASTER REPAIR DATA
# ═══════════════════════════════════════════════════════════

def fetch_fema_claims(limit=5000, archive_to_docrepo=True):
    """Fetch FEMA Individual Assistance repair amounts.
    Public dataset with damage amounts by type and region.
    """
    import os
    try:
        import requests
        url = 'https://www.fema.gov/api/open/v1/IndividualAssistanceHousingRegistrantsLargeDisasters'
        params = {
            '$top': limit,
            '$filter': 'rpfvl gt 2000 and rpfvl lt 100000',
            '$select': 'damagedStateAbbreviation,damagedZipCode,rpfvl,repairAmount,foundationDamageAmount,roofDamageAmount,floodDamage,foundationDamage,roofDamage',
            '$orderby': 'id desc',
        }
        resp = requests.get(url, params=params, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        # Archive to docrepo
        if archive_to_docrepo:
            try:
                docrepo_root = os.environ.get('DOCREPO_PATH', '/var/data/docrepo')
                archive_dir = os.path.join(docrepo_root, 'crawled', 'fema',
                                           datetime.utcnow().strftime('%Y-%m-%d'))
                os.makedirs(archive_dir, exist_ok=True)
                with open(os.path.join(archive_dir, 'ia_claims.json'), 'w', encoding='utf-8') as f:
                    json.dump({'source': 'fema_ia', 'fetched_at': datetime.utcnow().isoformat(), 'data': data}, f)
            except Exception:
                pass

        rows = []
        items = data.get('IndividualAssistanceHousingRegistrantsLargeDisasters', [])
        for item in items:
            try:
                cost = float(item.get('rpfvl', 0) or 0)
                repair = float(item.get('repairAmount', 0) or 0)
                foundation_amt = float(item.get('foundationDamageAmount', 0) or 0)
                roof_amt = float(item.get('roofDamageAmount', 0) or 0)
                zipcode = str(item.get('damagedZipCode', ''))[:5]
                state = item.get('damagedStateAbbreviation', '') or ''

                if cost < 2000 or cost > 100000:
                    continue

                # Determine category from damage type fields
                has_flood = str(item.get('floodDamage', '')).lower() in ('true', '1', 'yes')
                has_foundation = str(item.get('foundationDamage', '')).lower() in ('true', '1', 'yes')
                has_roof = str(item.get('roofDamage', '')).lower() in ('true', '1', 'yes')

                if has_foundation and foundation_amt > 0:
                    category = 'foundation_structure'
                    desc = f'Foundation damage repair ${foundation_amt:,.0f} — FEMA IA ({state})'
                    cost = foundation_amt
                elif has_roof and roof_amt > 0:
                    category = 'roof_exterior'
                    desc = f'Roof damage repair ${roof_amt:,.0f} — FEMA IA ({state})'
                    cost = roof_amt
                elif has_flood:
                    category = 'environmental'
                    desc = f'Flood damage repair ${cost:,.0f} — FEMA IA ({state})'
                else:
                    category = 'general'
                    desc = f'Disaster damage repair ${cost:,.0f} — FEMA IA ({state})'

                severity = _estimate_severity(cost)
                rows.append({
                    'finding_text': desc,
                    'category': category,
                    'severity': severity,
                    'cost_low': cost * 0.85,
                    'cost_high': cost * 1.15,
                    'cost_mid': cost,
                    'zip_code': zipcode,
                    'source': 'fema_ia',
                    'metadata': {'state': state},
                })
            except (ValueError, TypeError):
                continue
        logger.info(f'FEMA IA: fetched {len(rows)} claims from {len(items)} raw')
        return rows
    except Exception as e:
        logger.warning(f'FEMA IA fetch failed: {e}')
        return []


# ═══════════════════════════════════════════════════════════
# SOURCE 4: NYC HPD HOUSING MAINTENANCE CODE VIOLATIONS
# Millions of pre-categorized housing violations with severity
# ═══════════════════════════════════════════════════════════

# NYC HPD violation classes: A=non-hazardous, B=hazardous, C=immediately hazardous
HPD_CLASS_TO_SEVERITY = {'A': 'minor', 'B': 'moderate', 'C': 'major'}

# Map HPD violation categories to our categories
HPD_CATEGORY_MAP = {
    'plumbing': 'plumbing', 'water supply': 'plumbing', 'sewage': 'plumbing',
    'electric': 'electrical', 'electrical': 'electrical', 'wiring': 'electrical',
    'fire escape': 'foundation_structure', 'structural': 'foundation_structure',
    'walls': 'foundation_structure', 'ceilings': 'foundation_structure',
    'floors': 'foundation_structure', 'stairs': 'foundation_structure',
    'elevator': 'foundation_structure',
    'heat': 'hvac_systems', 'ventilation': 'hvac_systems', 'hvac': 'hvac_systems',
    'boiler': 'hvac_systems', 'hot water': 'hvac_systems',
    'paint': 'environmental', 'lead': 'environmental', 'mold': 'environmental',
    'pest': 'environmental', 'vermin': 'environmental', 'roach': 'environmental',
    'mice': 'environmental', 'rat': 'environmental', 'bed bug': 'environmental',
    'roof': 'roof_exterior', 'exterior': 'roof_exterior', 'window': 'roof_exterior',
    'door': 'roof_exterior', 'facade': 'roof_exterior',
    'general': 'general', 'safety': 'general',
}


def _classify_hpd_violation(description):
    """Map HPD violation description to our category."""
    text = (description or '').lower()
    for keyword, cat in HPD_CATEGORY_MAP.items():
        if keyword in text:
            return cat
    return 'general'



def fetch_violations_unified(source_name, limit=50000):
    """Unified fetcher for violation/complaint Socrata datasets."""
    if source_name not in VIOLATION_SOURCES:
        return []
    src = VIOLATION_SOURCES[source_name]
    try:
        import requests
        params = {'$limit': limit}
        if src.get('date_field'):
            params['$order'] = f"{src['date_field']} DESC"
        if src.get('filter'):
            params['$where'] = src['filter']
        resp = requests.get(src['url'], params=params, timeout=120)
        if resp.status_code != 200:
            params.pop('$where', None)
            params.pop('$order', None)
            resp = requests.get(src['url'], params={'$limit': limit}, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        rows = []
        for item in data:
            desc = item.get(src['desc_field'], '') or ''
            if len(desc) < 5:
                continue
            sev_raw = item.get(src.get('severity_field', ''), '') or ''
            severity = src.get('severity_map', {}).get(sev_raw, '')
            if not severity or severity not in ('minor', 'moderate', 'major', 'critical'):
                severity = 'major' if 'immediately' in str(sev_raw).lower() or 'hazard' in str(sev_raw).lower() else 'moderate'
            category = _classify_hpd_violation(desc)
            zipcode = str(item.get(src['zip_field'], ''))[:5] if src.get('zip_field') else ''

            rows.append({
                'finding_text': desc[:300],
                'category': category,
                'severity': severity,
                'cost_low': None, 'cost_high': None, 'cost_mid': 0,
                'zip_code': zipcode,
                'source': source_name,
                'metadata': {},
                '_type': 'finding',
            })
        logger.info(f'{source_name}: fetched {len(rows)} violations from {len(data)} raw')
        return rows
    except Exception as e:
        logger.warning(f'{source_name} fetch failed: {e}')
        return []


def fetch_philly_violations(limit=50000):
    """Fetch Philadelphia L&I code violations via Carto SQL API. ~2M records."""
    try:
        import requests
        sql = f"SELECT violationcodetitle, violationdate, zip FROM violations ORDER BY violationdate DESC LIMIT {limit}"
        resp = requests.get('https://phl.carto.com/api/v2/sql', params={'q': sql}, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        rows = []
        for item in data.get('rows', []):
            code = item.get('violationcodetitle', '') or ''
            if len(code) < 3:
                continue
            category = _classify_hpd_violation(code)
            severity = 'moderate'
            if any(w in code.lower() for w in ['unsafe', 'hazard', 'imminent', 'condemned']):
                severity = 'critical'
            elif any(w in code.lower() for w in ['structural', 'foundation', 'fire', 'electrical']):
                severity = 'major'

            rows.append({
                'finding_text': code[:300], 'category': category, 'severity': severity,
                'cost_low': None, 'cost_high': None, 'cost_mid': 0,
                'zip_code': str(item.get('zip', ''))[:5],
                'source': 'philly_violations', 'metadata': {}, '_type': 'finding',
            })
        logger.info(f'Philadelphia: fetched {len(rows)} violations')
        return rows
    except Exception as e:
        logger.warning(f'Philadelphia violations failed: {e}')
        return []


def fetch_nfip_claims(limit=50000):
    """Fetch NFIP flood insurance claims from OpenFEMA. 2M+ records available."""
    try:
        import requests
        url = 'https://www.fema.gov/api/open/v2/FimaNfipClaims'
        params = {
            '$top': limit,
            '$filter': 'amountPaidOnBuildingClaim gt 1000 and amountPaidOnBuildingClaim lt 200000',
            '$select': 'amountPaidOnBuildingClaim,amountPaidOnContentsClaim,reportedZipCode,yearOfLoss,occupancyType',
            '$orderby': 'yearOfLoss desc',
        }
        resp = requests.get(url, params=params, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        items = data.get('FimaNfipClaims', [])
        rows = []
        for item in items:
            try:
                building_paid = float(item.get('amountPaidOnBuildingClaim', 0) or 0)
                contents_paid = float(item.get('amountPaidOnContentsClaim', 0) or 0)
                total = building_paid + contents_paid
                if total < 1000 or total > 200000:
                    continue
                occupancy = str(item.get('occupancyType', '') or '')
                if occupancy in ('4', '5', '6'):
                    continue
                zipcode = str(item.get('reportedZipCode', ''))[:5]
                year = item.get('yearOfLoss', '')
                severity = _estimate_severity(total)
                desc = f'Flood damage repair ${total:,.0f} (NFIP claim {year})'
                rows.append({
                    'finding_text': desc[:300], 'category': 'environmental', 'severity': severity,
                    'cost_low': total * 0.85, 'cost_high': total * 1.15, 'cost_mid': total,
                    'zip_code': zipcode, 'source': 'nfip_claims', 'metadata': {'year': year},
                })
            except (ValueError, TypeError):
                continue
        logger.info(f'NFIP Claims: fetched {len(rows)} from {len(items)} raw')
        return rows
    except Exception as e:
        logger.warning(f'NFIP claims fetch failed: {e}')
        return []


def fetch_hud_inspection_scores(limit=5000):
    """Fetch HUD REAC physical inspection scores. May fail if endpoints are down."""
    try:
        import requests
        for url in ['https://data.hud.gov/resource/8bxb-nmzg.json', 'https://data.hud.gov/resource/jcdv-fn3j.json']:
            try:
                resp = requests.get(url, params={'$limit': limit}, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    if data:
                        rows = []
                        for item in data:
                            score = item.get('inspection_score')
                            if score is None: continue
                            try: score = float(score)
                            except: continue
                            severity = 'critical' if score < 60 else 'major' if score < 70 else 'moderate' if score < 80 else 'minor'
                            state = item.get('state_name', '') or item.get('state', '') or ''
                            desc = f'HUD inspection score {score:.0f}/100 ({severity}) — {state}'
                            rows.append({
                                'finding_text': desc[:300], 'category': 'general', 'severity': severity,
                                'cost_low': None, 'cost_high': None, 'cost_mid': 0,
                                'zip_code': str(item.get('zip', ''))[:5],
                                'source': 'hud_inspection', 'metadata': {'score': score}, '_type': 'finding',
                            })
                        logger.info(f'HUD: fetched {len(rows)} inspection scores')
                        return rows
            except Exception:
                continue
        logger.info('HUD: all endpoints unreachable')
        return []
    except Exception as e:
        logger.warning(f'HUD fetch failed: {e}')
        return []


def get_iii_insurance_calibration():
    """III (Insurance Information Institute) published claim averages. Ground truth."""
    calibration = [
        {'finding_text': 'Fire and lightning damage — national avg insurance claim', 'category': 'electrical', 'severity': 'critical', 'cost_low': 70000, 'cost_high': 100000, 'cost_mid': 83991, 'source': 'iii_insurance'},
        {'finding_text': 'Wind and hail damage — national avg insurance claim', 'category': 'roof_exterior', 'severity': 'major', 'cost_low': 9000, 'cost_high': 16000, 'cost_mid': 12514, 'source': 'iii_insurance'},
        {'finding_text': 'Water damage and freezing — national avg insurance claim', 'category': 'plumbing', 'severity': 'major', 'cost_low': 10000, 'cost_high': 18000, 'cost_mid': 13954, 'source': 'iii_insurance'},
        {'finding_text': 'Theft — national avg insurance claim', 'category': 'general', 'severity': 'moderate', 'cost_low': 3500, 'cost_high': 7000, 'cost_mid': 5024, 'source': 'iii_insurance'},
        {'finding_text': 'Bodily injury liability — national avg', 'category': 'general', 'severity': 'critical', 'cost_low': 25000, 'cost_high': 40000, 'cost_mid': 31690, 'source': 'iii_insurance'},
        {'finding_text': 'All property damage — national avg claim', 'category': 'general', 'severity': 'major', 'cost_low': 14000, 'cost_high': 20000, 'cost_mid': 16857, 'source': 'iii_insurance'},
        {'finding_text': 'Roof storm damage repair (1 in 36 homes/year)', 'category': 'roof_exterior', 'severity': 'moderate', 'cost_low': 5000, 'cost_high': 15000, 'cost_mid': 10000, 'source': 'iii_insurance'},
        {'finding_text': 'Plumbing water damage (1 in 67 homes/year)', 'category': 'plumbing', 'severity': 'moderate', 'cost_low': 3000, 'cost_high': 12000, 'cost_mid': 7500, 'source': 'iii_insurance'},
        {'finding_text': 'Fire damage repair (1 in 430 homes/year)', 'category': 'electrical', 'severity': 'critical', 'cost_low': 50000, 'cost_high': 120000, 'cost_mid': 85000, 'source': 'iii_insurance'},
    ]
    for c in calibration:
        c['zip_code'] = ''
        c['metadata'] = {'source_year': '2018-2022'}
    logger.info(f'III Insurance: {len(calibration)} calibration entries')
    return calibration


# ═══════════════════════════════════════════════════════════
# ORCHESTRATOR
# ═══════════════════════════════════════════════════════════

def collect_all_external_data(permit_limit=10000, violation_limit=50000, progress_callback=None):
    """Run ALL external data collectors. Returns (all_rows, stats).

    Parameters:
      permit_limit: Max rows to fetch per permit source (5 cities). Default 10000.
        Cron uses 300; manual admin crawl uses 10000.
      violation_limit: Max rows to fetch per violation source AND for NFIP/Philly.
        Default 50000 preserves legacy "big fetch" behavior. v5.87.36 cron uses
        2000 to cap memory (the corpus is now ~121K rows; only the most-recent
        few thousand per source per night are genuinely new, and Socrata APIs
        return them first via $order=date DESC).
      progress_callback: optional function(source_name, status, count) called
        after each source.
    """
    all_data = []
    stats = {}
    errors = {}
    t_start = time.time()

    def _fetch_safe(name, fn, *args, **kwargs):
        """Run a fetch with timing and error capture."""
        t0 = time.time()
        try:
            if progress_callback:
                progress_callback(name, 'fetching', 0)
            rows = fn(*args, **kwargs)
            elapsed = time.time() - t0
            stats[name] = len(rows)
            all_data.extend(rows)
            logger.info(f'  ✓ {name}: {len(rows)} rows in {elapsed:.1f}s')
            if progress_callback:
                progress_callback(name, 'done', len(rows))
            return rows
        except Exception as e:
            elapsed = time.time() - t0
            stats[name] = 0
            errors[name] = str(e)[:200]
            logger.warning(f'  ✗ {name}: FAILED in {elapsed:.1f}s — {e}')
            if progress_callback:
                progress_callback(name, 'failed', 0)
            return []

    logger.info('═══ Starting external data collection ═══')

    # ── Permits (cost data, 5 cities) ──
    for source in PERMIT_SOURCES.keys():
        _fetch_safe(f'permits_{source}', fetch_permit_data, source, limit=permit_limit)
        time.sleep(0.5)

    # ── HomeAdvisor national averages ──
    _fetch_safe('homeadvisor', fetch_homeadvisor_costs)

    # ── FEMA Individual Assistance ──
    _fetch_safe('fema_ia', fetch_fema_claims)

    # ── NFIP flood claims (2M+ available) ──
    _fetch_safe('nfip_claims', fetch_nfip_claims, limit=violation_limit)

    # ── III Insurance calibration ──
    _fetch_safe('iii_insurance', get_iii_insurance_calibration)

    # ── Violation datasets (finding labels) ──
    for vname in VIOLATION_SOURCES.keys():
        _fetch_safe(vname, fetch_violations_unified, vname, limit=violation_limit)
        time.sleep(0.5)

    # ── Philadelphia (Carto API, ~2M records) ──
    _fetch_safe('philly_violations', fetch_philly_violations, limit=violation_limit)

    # ── HUD inspections (may fail) ──
    _fetch_safe('hud_inspections', fetch_hud_inspection_scores, limit=5000)

    cost_rows = [r for r in all_data if r.get('cost_mid', 0) > 0]
    finding_rows = [r for r in all_data if r.get('_type') == 'finding']
    stats['_total'] = len(all_data)
    stats['_cost_rows'] = len(cost_rows)
    stats['_finding_rows'] = len(finding_rows)
    stats['_errors'] = errors
    stats['_elapsed'] = round(time.time() - t_start, 1)

    logger.info(f'═══ Collection complete: {len(cost_rows)} cost + {len(finding_rows)} findings = {len(all_data)} total in {stats["_elapsed"]}s ═══')
    if errors:
        logger.warning(f'  Failed sources: {list(errors.keys())}')
    return all_data, stats


def collect_all_external_cost_data(permit_limit=10000, violation_limit=50000):
    """Legacy alias. Forwards both knobs."""
    return collect_all_external_data(permit_limit=permit_limit, violation_limit=violation_limit)
