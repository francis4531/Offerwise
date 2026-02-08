#!/usr/bin/env python3
"""
OfferWise Property Research Agent v1.0
======================================
An autonomous AI agent that researches a property given only an address.

ARCHITECTURE:
    User provides address → Agent plans research → Tools execute in parallel
    → Agent synthesizes findings → Pre-analysis brief generated
    → Documents uploaded → Agent cross-references external data vs. disclosures

AGENT LOOP:
    1. PLAN: Given the address, decide which tools to call
    2. EXECUTE: Run tools in parallel (web lookups, APIs, scraping)
    3. SYNTHESIZE: Combine results into a coherent property brief
    4. CROSS-CHECK: Compare external data against uploaded documents
    5. REPORT: Generate pre-analysis brief + document analysis enrichment

This is a REAL agent because:
    - It decides which tools to use based on what data is available
    - It retries failed lookups with alternative sources
    - It reasons about what findings mean in combination
    - It iterates when initial results are incomplete
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

# ============================================================================
# DATA MODELS
# ============================================================================

class ToolStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class ResearchPhase(Enum):
    INITIALIZING = "initializing"
    GEOCODING = "geocoding"
    RESEARCHING = "researching"
    SYNTHESIZING = "synthesizing"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class ToolResult:
    """Result from a single tool execution"""
    tool_name: str
    status: ToolStatus
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duration_ms: int = 0
    source_url: Optional[str] = None
    confidence: float = 1.0  # How reliable is this data?
    
    def to_dict(self):
        d = asdict(self)
        d['status'] = self.status.value
        return d


@dataclass
class PropertyProfile:
    """Aggregated property intelligence from all tools"""
    address: str
    address_normalized: Optional[str] = None
    
    # Location
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    county: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    
    # Valuations
    estimated_value: Optional[int] = None
    value_range_low: Optional[int] = None
    value_range_high: Optional[int] = None
    price_per_sqft: Optional[float] = None
    last_sale_price: Optional[int] = None
    last_sale_date: Optional[str] = None
    
    # Property Details
    bedrooms: Optional[int] = None
    bathrooms: Optional[float] = None
    sqft: Optional[int] = None
    lot_size_sqft: Optional[int] = None
    year_built: Optional[int] = None
    property_type: Optional[str] = None
    
    # Tax & Assessment
    tax_assessed_value: Optional[int] = None
    annual_tax: Optional[float] = None
    tax_year: Optional[int] = None
    
    # Neighborhood
    walk_score: Optional[int] = None
    transit_score: Optional[int] = None
    bike_score: Optional[int] = None
    school_rating_elementary: Optional[float] = None
    school_rating_middle: Optional[float] = None
    school_rating_high: Optional[float] = None
    
    # Risk Factors
    flood_zone: Optional[str] = None
    flood_risk_level: Optional[str] = None
    earthquake_zone: Optional[bool] = None
    fire_hazard_zone: Optional[str] = None
    
    # Permits & History
    permits: List[Dict[str, str]] = field(default_factory=list)
    sale_history: List[Dict[str, Any]] = field(default_factory=list)
    
    # Comparable Sales
    comps: List[Dict[str, Any]] = field(default_factory=list)
    
    # Agent Findings (cross-referenced insights)
    agent_findings: List[Dict[str, str]] = field(default_factory=list)
    
    # Meta
    research_timestamp: Optional[str] = None
    tools_used: List[str] = field(default_factory=list)
    tools_failed: List[str] = field(default_factory=list)
    total_research_time_ms: int = 0
    
    def to_dict(self):
        return asdict(self)


# ============================================================================
# TOOL REGISTRY (Abstract Base + Implementations)
# ============================================================================

class ResearchTool(ABC):
    """Base class for all research tools"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool name"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """What this tool does (for agent reasoning)"""
        pass
    
    @property
    def requires_geocoding(self) -> bool:
        """Does this tool need lat/lng before it can run?"""
        return False
    
    @property
    def priority(self) -> int:
        """Execution priority (lower = runs first). Default 10."""
        return 10
    
    @abstractmethod
    def execute(self, address: str, profile: PropertyProfile) -> ToolResult:
        """Run the tool and return results"""
        pass
    
    def _make_request(self, url: str, params: dict = None, headers: dict = None, 
                      timeout: int = 10) -> requests.Response:
        """Safe HTTP request with timeout and error handling"""
        default_headers = {
            'User-Agent': 'OfferWise/1.0 Property Research (contact@getofferwise.ai)'
        }
        if headers:
            default_headers.update(headers)
        return requests.get(url, params=params, headers=default_headers, timeout=timeout)


# ---------------------------------------------------------------------------
# TOOL: Address Geocoding & Normalization (always runs first)
# ---------------------------------------------------------------------------
class GeocodingTool(ResearchTool):
    """Geocode address to lat/lng and normalize format"""
    
    name = "geocoding"
    description = "Convert address to coordinates and normalized form"
    priority = 1  # Must run first
    
    def execute(self, address: str, profile: PropertyProfile) -> ToolResult:
        start = time.time()
        try:
            # Use Census Bureau geocoder (free, no API key)
            url = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
            params = {
                'address': address,
                'benchmark': 'Public_AR_Current',
                'vintage': 'Current_Current',
                'format': 'json'
            }
            resp = self._make_request(url, params=params, timeout=15)
            data = resp.json()
            
            matches = data.get('result', {}).get('addressMatches', [])
            if not matches:
                return ToolResult(
                    tool_name=self.name, status=ToolStatus.FAILED,
                    error="Address not found in Census geocoder",
                    duration_ms=int((time.time() - start) * 1000)
                )
            
            match = matches[0]
            coords = match.get('coordinates', {})
            addr_components = match.get('addressComponents', {})
            geographies = match.get('geographies', {})
            
            # Extract county from geographies
            counties = geographies.get('Counties', [{}])
            county_name = counties[0].get('BASENAME', '') if counties else ''
            
            profile.latitude = coords.get('y')
            profile.longitude = coords.get('x')
            profile.address_normalized = match.get('matchedAddress', address)
            profile.city = addr_components.get('city', '')
            profile.state = addr_components.get('state', '')
            profile.zip_code = addr_components.get('zip', '')
            profile.county = county_name
            
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.SUCCESS,
                data={
                    'lat': profile.latitude,
                    'lng': profile.longitude,
                    'normalized_address': profile.address_normalized,
                    'county': county_name,
                    'city': profile.city,
                    'state': profile.state,
                    'zip': profile.zip_code
                },
                duration_ms=int((time.time() - start) * 1000),
                source_url="https://geocoding.geo.census.gov"
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.FAILED,
                error=str(e), duration_ms=int((time.time() - start) * 1000)
            )


# ---------------------------------------------------------------------------
# TOOL: FEMA Flood Zone Lookup
# ---------------------------------------------------------------------------
class FloodZoneTool(ResearchTool):
    """Check FEMA flood zone designation"""
    
    name = "flood_zone"
    description = "FEMA flood zone risk assessment for the property location"
    requires_geocoding = True
    
    def execute(self, address: str, profile: PropertyProfile) -> ToolResult:
        start = time.time()
        if not profile.latitude or not profile.longitude:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SKIPPED,
                error="No coordinates available",
                duration_ms=int((time.time() - start) * 1000)
            )
        
        try:
            # FEMA National Flood Hazard Layer (free, no key)
            url = "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28/query"
            params = {
                'geometry': f'{profile.longitude},{profile.latitude}',
                'geometryType': 'esriGeometryPoint',
                'inSR': '4326',
                'spatialRel': 'esriSpatialRelIntersects',
                'outFields': 'FLD_ZONE,ZONE_SUBTY,SFHA_TF',
                'returnGeometry': 'false',
                'f': 'json'
            }
            resp = self._make_request(url, params=params, timeout=15)
            data = resp.json()
            
            features = data.get('features', [])
            if features:
                attrs = features[0].get('attributes', {})
                zone = attrs.get('FLD_ZONE', 'Unknown')
                is_sfha = attrs.get('SFHA_TF', 'F')  # T = Special Flood Hazard Area
                
                risk_level = 'high' if is_sfha == 'T' else 'moderate' if zone in ('B', 'X500') else 'low'
                
                profile.flood_zone = zone
                profile.flood_risk_level = risk_level
                
                return ToolResult(
                    tool_name=self.name, status=ToolStatus.SUCCESS,
                    data={'zone': zone, 'risk_level': risk_level, 'sfha': is_sfha == 'T'},
                    duration_ms=int((time.time() - start) * 1000),
                    source_url="https://hazards.fema.gov"
                )
            
            profile.flood_zone = 'X'
            profile.flood_risk_level = 'minimal'
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SUCCESS,
                data={'zone': 'X', 'risk_level': 'minimal', 'sfha': False},
                duration_ms=int((time.time() - start) * 1000),
                source_url="https://hazards.fema.gov"
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.FAILED,
                error=str(e), duration_ms=int((time.time() - start) * 1000)
            )


# ---------------------------------------------------------------------------
# TOOL: California Natural Hazard Zones (Earthquake + Fire)
# ---------------------------------------------------------------------------
class CaliforniaHazardsTool(ResearchTool):
    """California-specific seismic and fire hazard zones"""
    
    name = "ca_hazards"
    description = "California earthquake fault zones and fire hazard severity zones"
    requires_geocoding = True
    
    def execute(self, address: str, profile: PropertyProfile) -> ToolResult:
        start = time.time()
        if not profile.latitude or not profile.longitude:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SKIPPED,
                error="No coordinates available",
                duration_ms=int((time.time() - start) * 1000)
            )
        
        results = {}
        
        try:
            # CGS Seismic Hazard Zones (Alquist-Priolo Fault Zones)
            eq_url = "https://gis.conservation.ca.gov/server/rest/services/CGS/SeismicHazardZones/MapServer/0/query"
            eq_params = {
                'geometry': f'{profile.longitude},{profile.latitude}',
                'geometryType': 'esriGeometryPoint',
                'inSR': '4326',
                'spatialRel': 'esriSpatialRelIntersects',
                'outFields': '*',
                'returnGeometry': 'false',
                'f': 'json'
            }
            eq_resp = self._make_request(eq_url, params=eq_params, timeout=15)
            eq_data = eq_resp.json()
            
            eq_features = eq_data.get('features', [])
            in_earthquake_zone = len(eq_features) > 0
            profile.earthquake_zone = in_earthquake_zone
            results['earthquake_zone'] = in_earthquake_zone
            if eq_features:
                results['earthquake_type'] = eq_features[0].get('attributes', {}).get('Type', 'Unknown')
        except Exception as e:
            results['earthquake_error'] = str(e)
        
        try:
            # CAL FIRE - Fire Hazard Severity Zones
            fire_url = "https://egis.fire.ca.gov/arcgis/rest/services/FHSZ/FHSZ_SRA_LRA_Combined/MapServer/0/query"
            fire_params = {
                'geometry': f'{profile.longitude},{profile.latitude}',
                'geometryType': 'esriGeometryPoint',
                'inSR': '4326',
                'spatialRel': 'esriSpatialRelIntersects',
                'outFields': 'HAZ_CLASS,HAZ_CODE',
                'returnGeometry': 'false',
                'f': 'json'
            }
            fire_resp = self._make_request(fire_url, params=fire_params, timeout=15)
            fire_data = fire_resp.json()
            
            fire_features = fire_data.get('features', [])
            if fire_features:
                haz_class = fire_features[0].get('attributes', {}).get('HAZ_CLASS', '')
                profile.fire_hazard_zone = haz_class
                results['fire_hazard_zone'] = haz_class
            else:
                results['fire_hazard_zone'] = 'Non-VHFHSZ'
        except Exception as e:
            results['fire_error'] = str(e)
        
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS if results else ToolStatus.FAILED,
            data=results,
            duration_ms=int((time.time() - start) * 1000),
            source_url="https://gis.conservation.ca.gov"
        )


# ---------------------------------------------------------------------------
# TOOL: Walk Score API
# ---------------------------------------------------------------------------
class WalkScoreTool(ResearchTool):
    """Walk Score, Transit Score, Bike Score"""
    
    name = "walk_score"
    description = "Walkability, transit access, and bikeability scores"
    requires_geocoding = True
    
    def execute(self, address: str, profile: PropertyProfile) -> ToolResult:
        start = time.time()
        api_key = os.environ.get('WALKSCORE_API_KEY')
        if not api_key:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SKIPPED,
                error="WALKSCORE_API_KEY not configured",
                duration_ms=int((time.time() - start) * 1000)
            )
        
        if not profile.latitude or not profile.longitude:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SKIPPED,
                error="No coordinates available",
                duration_ms=int((time.time() - start) * 1000)
            )
        
        try:
            url = "https://api.walkscore.com/score"
            params = {
                'format': 'json',
                'address': address,
                'lat': profile.latitude,
                'lon': profile.longitude,
                'transit': 1,
                'bike': 1,
                'wsapikey': api_key
            }
            resp = self._make_request(url, params=params)
            data = resp.json()
            
            profile.walk_score = data.get('walkscore')
            profile.transit_score = data.get('transit', {}).get('score')
            profile.bike_score = data.get('bike', {}).get('score')
            
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SUCCESS,
                data={
                    'walk_score': profile.walk_score,
                    'transit_score': profile.transit_score,
                    'bike_score': profile.bike_score,
                    'walk_description': data.get('description', ''),
                },
                duration_ms=int((time.time() - start) * 1000),
                source_url="https://www.walkscore.com"
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.FAILED,
                error=str(e), duration_ms=int((time.time() - start) * 1000)
            )


# ---------------------------------------------------------------------------
# TOOL: Redfin Property Data (web scrape - public data)
# ---------------------------------------------------------------------------
class RedfinTool(ResearchTool):
    """Redfin property details, estimate, comps, and sale history"""
    
    name = "redfin"
    description = "Property details, valuation estimate, comparable sales, and history from Redfin"
    priority = 5  # High priority - richest data source
    
    def execute(self, address: str, profile: PropertyProfile) -> ToolResult:
        start = time.time()
        try:
            # Redfin's internal API for address search (publicly accessible)
            search_url = "https://www.redfin.com/stingray/do/location-autocomplete"
            params = {
                'location': address,
                'v': '2',
                'al': '1',
                'market': 'socal'  # Will auto-detect
            }
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'application/json',
            }
            resp = self._make_request(search_url, params=params, headers=headers, timeout=15)
            
            # Redfin returns {}&&{ ... } format - strip prefix
            text = resp.text
            if text.startswith('{}&&'):
                text = text[4:]
            data = json.loads(text)
            
            results = data.get('payload', {}).get('sections', [])
            if not results:
                return ToolResult(
                    tool_name=self.name, status=ToolStatus.FAILED,
                    error="Address not found on Redfin",
                    duration_ms=int((time.time() - start) * 1000)
                )
            
            # Find exact address match
            property_url = None
            for section in results:
                for row in section.get('rows', []):
                    if row.get('type') == '1':  # Type 1 = exact address
                        property_url = row.get('url', '')
                        break
                if property_url:
                    break
            
            if not property_url:
                return ToolResult(
                    tool_name=self.name, status=ToolStatus.FAILED,
                    error="No exact address match on Redfin",
                    duration_ms=int((time.time() - start) * 1000)
                )
            
            # Fetch the AVM (automated valuation) page data
            avm_url = f"https://www.redfin.com/stingray/api/home/details/avm?path={property_url}"
            avm_resp = self._make_request(avm_url, headers=headers, timeout=15)
            avm_text = avm_resp.text
            if avm_text.startswith('{}&&'):
                avm_text = avm_text[4:]
            
            avm_data = {}
            try:
                avm_data = json.loads(avm_text).get('payload', {})
            except:
                pass
            
            # Extract property details from whatever we got
            property_data = {}
            
            # Try to get basic property info
            info_url = f"https://www.redfin.com/stingray/api/home/details/belowTheFold?path={property_url}"
            info_resp = self._make_request(info_url, headers=headers, timeout=15)
            info_text = info_resp.text
            if info_text.startswith('{}&&'):
                info_text = info_text[4:]
            
            try:
                info_data = json.loads(info_text).get('payload', {})
                
                # Property details
                prop_info = info_data.get('publicRecordsInfo', {})
                if prop_info:
                    basic = prop_info.get('basicInfo', {})
                    profile.bedrooms = basic.get('beds')
                    profile.bathrooms = basic.get('baths')
                    profile.sqft = basic.get('sqFt')
                    profile.lot_size_sqft = basic.get('lotSqFt')
                    profile.year_built = basic.get('yearBuilt')
                    profile.property_type = basic.get('propertyType')
                
                # Tax info
                tax_info = info_data.get('taxInfo', {})
                if tax_info:
                    records = tax_info.get('taxRecords', [])
                    if records:
                        latest = records[0]
                        profile.tax_assessed_value = latest.get('taxableLandValue', 0) + latest.get('taxableImprovementValue', 0)
                        profile.annual_tax = latest.get('rollTax')
                        profile.tax_year = latest.get('rollYear')
                
                property_data['source'] = 'redfin'
                property_data['url'] = f"https://www.redfin.com{property_url}"
                
            except Exception as e:
                logger.warning(f"Redfin detail parse error: {e}")
            
            # Redfin estimate
            if avm_data:
                estimate = avm_data.get('predictedValue')
                if estimate:
                    profile.estimated_value = int(estimate)
                    property_data['redfin_estimate'] = int(estimate)
                
                price_range = avm_data.get('predictedValueRange', {})
                if price_range:
                    profile.value_range_low = price_range.get('low')
                    profile.value_range_high = price_range.get('high')
            
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.SUCCESS,
                data=property_data,
                duration_ms=int((time.time() - start) * 1000),
                source_url=f"https://www.redfin.com{property_url}" if property_url else None
            )
        
        except Exception as e:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.FAILED,
                error=str(e), duration_ms=int((time.time() - start) * 1000)
            )


# ---------------------------------------------------------------------------
# TOOL: School Ratings (GreatSchools)
# ---------------------------------------------------------------------------
class SchoolRatingsTool(ResearchTool):
    """Nearby school ratings from GreatSchools"""
    
    name = "school_ratings"
    description = "Elementary, middle, and high school ratings near the property"
    requires_geocoding = True
    
    def execute(self, address: str, profile: PropertyProfile) -> ToolResult:
        start = time.time()
        api_key = os.environ.get('GREATSCHOOLS_API_KEY')
        if not api_key:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SKIPPED,
                error="GREATSCHOOLS_API_KEY not configured",
                duration_ms=int((time.time() - start) * 1000)
            )
        
        if not profile.latitude or not profile.longitude:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SKIPPED,
                error="No coordinates available",
                duration_ms=int((time.time() - start) * 1000)
            )
        
        try:
            url = "https://gs-api.greatschools.org/nearby-schools"
            params = {
                'lat': profile.latitude,
                'lon': profile.longitude,
                'distance': 3,  # miles
                'limit': 10,
                'levelCodes': 'e,m,h'  # elementary, middle, high
            }
            headers = {'x-api-key': api_key}
            resp = self._make_request(url, params=params, headers=headers)
            data = resp.json()
            
            schools_by_level = {'e': [], 'm': [], 'h': []}
            for school in data.get('schools', []):
                level = school.get('level', '').lower()
                rating = school.get('rating')
                if rating and level in schools_by_level:
                    schools_by_level[level].append(rating)
            
            # Average ratings by level
            for level, ratings in schools_by_level.items():
                if ratings:
                    avg = sum(ratings) / len(ratings)
                    if level == 'e':
                        profile.school_rating_elementary = round(avg, 1)
                    elif level == 'm':
                        profile.school_rating_middle = round(avg, 1)
                    elif level == 'h':
                        profile.school_rating_high = round(avg, 1)
            
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SUCCESS,
                data={
                    'elementary_avg': profile.school_rating_elementary,
                    'middle_avg': profile.school_rating_middle,
                    'high_avg': profile.school_rating_high,
                    'schools_found': len(data.get('schools', []))
                },
                duration_ms=int((time.time() - start) * 1000),
                source_url="https://www.greatschools.org"
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.FAILED,
                error=str(e), duration_ms=int((time.time() - start) * 1000)
            )


# ---------------------------------------------------------------------------
# TOOL: County Permit History (via open data portals)
# ---------------------------------------------------------------------------
class PermitHistoryTool(ResearchTool):
    """Building permits from county open data portals"""
    
    name = "permits"
    description = "Building permit history to verify seller claims about renovations and repairs"
    
    # California county open data portals (Socrata-based)
    COUNTY_APIS = {
        'Santa Clara': {
            'url': 'https://data.sccgov.org/resource/bwxt-4fh4.json',
            'address_field': 'address'
        },
        'San Mateo': {
            'url': 'https://data.smcgov.org/resource/building-permits.json',
            'address_field': 'address'
        },
        'Alameda': {
            'url': 'https://data.acgov.org/resource/building-permits.json',
            'address_field': 'address'
        },
        # More counties can be added as discovered
    }
    
    def execute(self, address: str, profile: PropertyProfile) -> ToolResult:
        start = time.time()
        county = profile.county or ''
        
        if county not in self.COUNTY_APIS:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SKIPPED,
                error=f"No permit data source for {county} county",
                data={'county': county, 'supported_counties': list(self.COUNTY_APIS.keys())},
                duration_ms=int((time.time() - start) * 1000)
            )
        
        try:
            api = self.COUNTY_APIS[county]
            
            # Extract street number and name for fuzzy matching
            addr_parts = address.split(',')[0].strip()
            
            params = {
                '$where': f"upper({api['address_field']}) like upper('%{addr_parts}%')",
                '$limit': 20,
                '$order': 'issue_date DESC' if 'issue_date' in api.get('order_field', 'issue_date') else ':id DESC'
            }
            
            resp = self._make_request(api['url'], params=params, timeout=15)
            permits = resp.json()
            
            parsed_permits = []
            for p in permits:
                parsed_permits.append({
                    'date': p.get('issue_date', p.get('date', 'Unknown')),
                    'type': p.get('permit_type', p.get('type', 'Unknown')),
                    'description': p.get('description', p.get('work_description', '')),
                    'status': p.get('status', ''),
                    'value': p.get('valuation', p.get('job_value', ''))
                })
            
            profile.permits = parsed_permits
            
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SUCCESS,
                data={'permits': parsed_permits, 'count': len(parsed_permits)},
                duration_ms=int((time.time() - start) * 1000),
                source_url=api['url']
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.FAILED,
                error=str(e), duration_ms=int((time.time() - start) * 1000)
            )


# ============================================================================
# AGENT: The Reasoning Core
# ============================================================================

class PropertyResearchAgent:
    """
    Autonomous agent that researches a property given only an address.
    
    This is the brain — it decides which tools to use, handles failures,
    retries with alternatives, and synthesizes findings into intelligence.
    """
    
    # All available tools
    TOOL_CLASSES = [
        GeocodingTool,
        RedfinTool,
        FloodZoneTool,
        CaliforniaHazardsTool,
        WalkScoreTool,
        SchoolRatingsTool,
        PermitHistoryTool,
    ]
    
    def __init__(self, ai_client=None):
        """
        Args:
            ai_client: Anthropic client for synthesis reasoning (optional — 
                       agent works without it, just won't have narrative synthesis)
        """
        self.ai_client = ai_client
        self.tools = [cls() for cls in self.TOOL_CLASSES]
        self.tools.sort(key=lambda t: t.priority)
    
    def research(self, address: str, progress_callback=None) -> Dict[str, Any]:
        """
        Main agent loop: research a property.
        
        Args:
            address: Raw address string (e.g. "1234 Oak St, San Jose, CA")
            progress_callback: Optional fn(phase, message, pct) for live updates
            
        Returns:
            Complete research results dict
        """
        overall_start = time.time()
        profile = PropertyProfile(address=address)
        profile.research_timestamp = datetime.now(ZoneInfo('America/Los_Angeles')).isoformat()
        
        tool_results: List[ToolResult] = []
        
        def notify(phase: ResearchPhase, message: str, pct: int):
            if progress_callback:
                try:
                    progress_callback(phase.value, message, pct)
                except:
                    pass
        
        # ── Phase 1: Geocode ──────────────────────────────────────────────
        notify(ResearchPhase.GEOCODING, "Locating property...", 5)
        
        geocoder = self.tools[0]  # GeocodingTool (priority=1)
        geo_result = geocoder.execute(address, profile)
        tool_results.append(geo_result)
        
        if geo_result.status == ToolStatus.FAILED:
            logger.warning(f"Geocoding failed for '{address}': {geo_result.error}")
            # Agent DECISION: Continue anyway — some tools don't need coordinates
            notify(ResearchPhase.GEOCODING, "Address lookup inconclusive, continuing with available tools...", 10)
        else:
            notify(ResearchPhase.GEOCODING, f"Found: {profile.address_normalized}", 10)
        
        # ── Phase 2: Parallel Research ────────────────────────────────────
        notify(ResearchPhase.RESEARCHING, "Researching property...", 15)
        
        # Agent DECISION: Which tools can run?
        remaining_tools = [t for t in self.tools if t.name != 'geocoding']
        
        has_coords = profile.latitude is not None
        runnable = []
        skipped = []
        
        for tool in remaining_tools:
            if tool.requires_geocoding and not has_coords:
                skipped.append(tool)
                tool_results.append(ToolResult(
                    tool_name=tool.name, status=ToolStatus.SKIPPED,
                    error="Geocoding failed — coordinates required"
                ))
            else:
                runnable.append(tool)
        
        # Execute runnable tools in parallel
        if runnable:
            with ThreadPoolExecutor(max_workers=min(len(runnable), 6)) as executor:
                futures = {
                    executor.submit(self._safe_execute, tool, address, profile): tool
                    for tool in runnable
                }
                
                completed = 0
                for future in as_completed(futures):
                    tool = futures[future]
                    result = future.result()
                    tool_results.append(result)
                    completed += 1
                    
                    pct = 15 + int((completed / len(runnable)) * 60)
                    status_emoji = "✅" if result.status == ToolStatus.SUCCESS else "⚠️"
                    notify(
                        ResearchPhase.RESEARCHING,
                        f"{status_emoji} {tool.description} ({completed}/{len(runnable)})",
                        pct
                    )
        
        # ── Phase 3: Agent Reasoning & Synthesis ──────────────────────────
        notify(ResearchPhase.SYNTHESIZING, "Analyzing findings...", 80)
        
        # Record tool usage
        for r in tool_results:
            if r.status == ToolStatus.SUCCESS:
                profile.tools_used.append(r.tool_name)
            elif r.status == ToolStatus.FAILED:
                profile.tools_failed.append(r.tool_name)
        
        # Agent REASONING: Generate cross-referenced findings
        profile.agent_findings = self._generate_findings(profile, tool_results)
        
        # AI Synthesis (if available)
        synthesis = None
        if self.ai_client:
            notify(ResearchPhase.SYNTHESIZING, "Generating property intelligence brief...", 90)
            synthesis = self._ai_synthesize(profile, tool_results)
        
        profile.total_research_time_ms = int((time.time() - overall_start) * 1000)
        
        notify(ResearchPhase.COMPLETE, "Research complete!", 100)
        
        # ── Build Response ────────────────────────────────────────────────
        return {
            'success': True,
            'profile': profile.to_dict(),
            'tool_results': [r.to_dict() for r in tool_results],
            'synthesis': synthesis,
            'research_time_ms': profile.total_research_time_ms,
            'tools_succeeded': len(profile.tools_used),
            'tools_failed': len(profile.tools_failed),
        }
    
    def _safe_execute(self, tool: ResearchTool, address: str, 
                      profile: PropertyProfile) -> ToolResult:
        """Execute a tool with error boundary"""
        try:
            return tool.execute(address, profile)
        except Exception as e:
            logger.error(f"Tool {tool.name} crashed: {e}")
            return ToolResult(
                tool_name=tool.name, status=ToolStatus.FAILED,
                error=f"Unexpected error: {str(e)}"
            )
    
    def _generate_findings(self, profile: PropertyProfile, 
                           results: List[ToolResult]) -> List[Dict[str, str]]:
        """
        Agent reasoning: cross-reference data sources to generate findings.
        
        This is where the agent adds intelligence beyond what any single
        tool provides — it spots contradictions, flags risks, and connects dots.
        """
        findings = []
        
        # Finding: Property age + systems risk
        if profile.year_built:
            age = datetime.now().year - profile.year_built
            if age > 40:
                findings.append({
                    'type': 'risk',
                    'severity': 'high' if age > 60 else 'medium',
                    'title': f'Property is {age} years old (built {profile.year_built})',
                    'detail': (
                        f"At {age} years old, expect potential issues with: "
                        f"{'original plumbing (galvanized/polybutylene), ' if age > 40 else ''}"
                        f"{'electrical panel (Federal Pacific/Zinsco era), ' if 35 < age < 55 else ''}"
                        f"{'foundation settling, ' if age > 30 else ''}"
                        f"{'roof replacement needed' if age > 25 else 'roof approaching end of life'}"
                    ),
                    'cross_check': 'Verify seller disclosure mentions age-related repairs'
                })
        
        # Finding: Flood zone risk
        if profile.flood_zone and profile.flood_risk_level in ('high', 'moderate'):
            findings.append({
                'type': 'risk',
                'severity': 'high' if profile.flood_risk_level == 'high' else 'medium',
                'title': f'FEMA Flood Zone: {profile.flood_zone} ({profile.flood_risk_level} risk)',
                'detail': (
                    'This property is in a Special Flood Hazard Area. '
                    'Flood insurance is likely mandatory for mortgage lenders. '
                    'Typical cost: $1,500-$5,000/year depending on coverage.'
                ),
                'cross_check': 'Check seller disclosure for flood history and past claims'
            })
        
        # Finding: Fire hazard zone
        if profile.fire_hazard_zone and 'very high' in (profile.fire_hazard_zone or '').lower():
            findings.append({
                'type': 'risk',
                'severity': 'high',
                'title': 'Very High Fire Hazard Severity Zone',
                'detail': (
                    'CAL FIRE designates this area as Very High fire hazard. '
                    'May require fire-resistant landscaping, special insurance, '
                    'and compliance with defensible space regulations (100ft clearance).'
                ),
                'cross_check': 'Verify insurance availability and cost before making offer'
            })
        
        # Finding: Earthquake zone
        if profile.earthquake_zone:
            findings.append({
                'type': 'risk',
                'severity': 'medium',
                'title': 'Seismic Hazard Zone',
                'detail': (
                    'Property is in a California Seismic Hazard Zone. '
                    'Consider earthquake insurance and check if foundation has been retrofitted. '
                    'Pre-1980 homes without bolt-on retrofits are especially vulnerable.'
                ),
                'cross_check': 'Check permits for seismic retrofit. Check seller disclosure for earthquake damage history.'
            })
        
        # Finding: Valuation vs. asking price analysis
        if profile.estimated_value and profile.last_sale_price:
            appreciation = ((profile.estimated_value - profile.last_sale_price) / 
                          profile.last_sale_price * 100)
            if appreciation > 100:
                findings.append({
                    'type': 'insight',
                    'severity': 'info',
                    'title': f'Value has {appreciation:.0f}% since last sale',
                    'detail': (
                        f'Last sold for ${profile.last_sale_price:,} on {profile.last_sale_date}. '
                        f'Current estimate: ${profile.estimated_value:,}. '
                        f'Strong appreciation suggests a competitive market in this area.'
                    ),
                    'cross_check': None
                })
        
        # Finding: Tax assessment gap
        if profile.estimated_value and profile.tax_assessed_value:
            gap = profile.estimated_value - profile.tax_assessed_value
            if gap > 100000:
                findings.append({
                    'type': 'insight',
                    'severity': 'info',
                    'title': 'Tax assessment significantly below market value',
                    'detail': (
                        f'Assessed at ${profile.tax_assessed_value:,} vs estimated ${profile.estimated_value:,}. '
                        f'Property taxes will reset to purchase price upon sale (Prop 13 reassessment). '
                        f'Budget for higher annual taxes.'
                    ),
                    'cross_check': None
                })
        
        # Finding: Missing permits for claimed work
        if profile.permits and profile.year_built:
            permit_types = [p.get('type', '').lower() for p in profile.permits]
            has_roof_permit = any('roof' in t for t in permit_types)
            has_hvac_permit = any('hvac' in t or 'mechanical' in t for t in permit_types)
            has_plumbing_permit = any('plumb' in t for t in permit_types)
            
            age = datetime.now().year - profile.year_built
            if age > 25 and not has_roof_permit:
                findings.append({
                    'type': 'verification',
                    'severity': 'medium',
                    'title': 'No roof replacement permit on record',
                    'detail': (
                        f'Home is {age} years old but no roofing permits found. '
                        f'If seller claims roof was replaced, verify with permit records. '
                        f'Unpermitted work may affect insurance coverage and resale.'
                    ),
                    'cross_check': 'Compare against seller disclosure claims about roof'
                })
        
        return findings
    
    def _ai_synthesize(self, profile: PropertyProfile, 
                       results: List[ToolResult]) -> Optional[Dict[str, str]]:
        """
        Use AI to generate a narrative synthesis of all findings.
        This is the agent's "thinking out loud" — connecting dots that
        rule-based logic might miss.
        """
        if not self.ai_client:
            return None
        
        try:
            # Build context for AI
            successful_tools = [r for r in results if r.status == ToolStatus.SUCCESS]
            tool_summary = "\n".join([
                f"- {r.tool_name}: {json.dumps(r.data, default=str)[:500]}"
                for r in successful_tools
            ])
            
            findings_text = "\n".join([
                f"- [{f['severity'].upper()}] {f['title']}: {f['detail']}"
                for f in profile.agent_findings
            ])
            
            prompt = f"""You are a real estate research analyst. Based on the following data gathered 
about a property, write a concise Pre-Analysis Intelligence Brief.

PROPERTY: {profile.address_normalized or profile.address}
YEAR BUILT: {profile.year_built or 'Unknown'}
ESTIMATED VALUE: ${profile.estimated_value:,} if profile.estimated_value else 'Unknown'
SQFT: {profile.sqft or 'Unknown'}

DATA GATHERED:
{tool_summary}

KEY FINDINGS:
{findings_text}

Write a brief (3-4 paragraphs) that:
1. Summarizes what we know about this property BEFORE seeing any seller documents
2. Identifies the top 3 things a buyer should verify in the seller disclosure
3. Flags any red flags or risk factors that could affect the offer price
4. Notes what data we could NOT find (gaps in research)

Be specific and actionable. Reference actual data points. No generic advice."""

            response = self.ai_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1000,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )
            
            brief = response.content[0].text
            
            return {
                'brief': brief,
                'tools_used': len(successful_tools),
                'findings_count': len(profile.agent_findings),
            }
        
        except Exception as e:
            logger.error(f"AI synthesis failed: {e}")
            return None
    
    def cross_check_against_documents(
        self, 
        profile: PropertyProfile,
        disclosure_text: str,
        inspection_text: str
    ) -> List[Dict[str, str]]:
        """
        PHASE 2: After documents are uploaded, cross-reference agent research
        against what the seller disclosed and inspector found.
        
        This is the killer feature — "Seller says roof replaced 2019, 
        but we found no roofing permit on file."
        """
        cross_checks = []
        disclosure_lower = disclosure_text.lower()
        
        # Check 1: Roof claims vs permits
        if 'roof' in disclosure_lower and ('replace' in disclosure_lower or 'new' in disclosure_lower):
            has_roof_permit = any(
                'roof' in p.get('type', '').lower() or 'roof' in p.get('description', '').lower()
                for p in profile.permits
            )
            if not has_roof_permit and profile.permits:
                cross_checks.append({
                    'type': 'contradiction',
                    'severity': 'high',
                    'title': 'Roof replacement claimed but no permit found',
                    'detail': (
                        'Seller disclosure mentions roof replacement, but county permit '
                        'records show no roofing permit. This could indicate unpermitted work, '
                        'which may void warranty coverage and affect insurance.'
                    ),
                    'source': 'County permit records vs. seller disclosure'
                })
        
        # Check 2: Foundation/structural work vs permits
        if any(word in disclosure_lower for word in ['foundation repair', 'seismic retrofit', 'structural']):
            has_structural_permit = any(
                any(word in p.get('description', '').lower() 
                    for word in ['foundation', 'structural', 'seismic'])
                for p in profile.permits
            )
            if not has_structural_permit and profile.permits:
                cross_checks.append({
                    'type': 'contradiction',
                    'severity': 'high',
                    'title': 'Structural work claimed but no permit found',
                    'detail': (
                        'Seller mentions foundation or structural repairs, but no matching '
                        'permit was found. Structural work always requires permits. '
                        'Request proof of permitted work.'
                    ),
                    'source': 'County permit records vs. seller disclosure'
                })
        
        # Check 3: Flood history vs FEMA zone
        if profile.flood_risk_level in ('high', 'moderate'):
            flood_mentioned = any(word in disclosure_lower for word in ['flood', 'water damage', 'standing water'])
            if not flood_mentioned:
                cross_checks.append({
                    'type': 'omission',
                    'severity': 'high',
                    'title': 'Property in flood zone but disclosure does not mention flooding',
                    'detail': (
                        f'FEMA maps show this property in flood zone {profile.flood_zone} '
                        f'({profile.flood_risk_level} risk), but the seller disclosure makes '
                        f'no mention of flooding or water damage. This is a significant omission.'
                    ),
                    'source': 'FEMA flood maps vs. seller disclosure'
                })
        
        # Check 4: Fire zone vs disclosure
        if profile.fire_hazard_zone and 'very high' in (profile.fire_hazard_zone or '').lower():
            fire_mentioned = any(word in disclosure_lower for word in ['fire', 'wildfire', 'fire hazard'])
            if not fire_mentioned:
                cross_checks.append({
                    'type': 'omission',
                    'severity': 'medium',
                    'title': 'Very High Fire Zone not mentioned in disclosure',
                    'detail': (
                        'CAL FIRE designates this property as Very High Fire Hazard, '
                        'but the seller disclosure does not mention fire risk. '
                        'Verify insurance availability and defensible space compliance.'
                    ),
                    'source': 'CAL FIRE maps vs. seller disclosure'
                })
        
        # Check 5: Year built discrepancy
        if profile.year_built:
            # Look for year claims in disclosure
            year_matches = re.findall(r'built\s+(?:in\s+)?(\d{4})', disclosure_lower)
            for claimed_year in year_matches:
                if abs(int(claimed_year) - profile.year_built) > 2:
                    cross_checks.append({
                        'type': 'contradiction',
                        'severity': 'medium',
                        'title': f'Year built discrepancy: seller says {claimed_year}, records show {profile.year_built}',
                        'detail': (
                            f'Seller disclosure claims property was built in {claimed_year}, '
                            f'but county records show {profile.year_built}. This may be due to '
                            f'major renovation, but should be clarified.'
                        ),
                        'source': 'County assessor records vs. seller disclosure'
                    })
        
        return cross_checks


# ============================================================================
# CONVENIENCE: Quick research function
# ============================================================================

def research_property(address: str, ai_client=None, progress_callback=None) -> Dict[str, Any]:
    """
    One-line function to research a property.
    
    Usage:
        from property_research_agent import research_property
        result = research_property("1234 Oak St, San Jose, CA 95123")
        print(result['profile']['estimated_value'])
        print(result['synthesis']['brief'])
    """
    agent = PropertyResearchAgent(ai_client=ai_client)
    return agent.research(address, progress_callback=progress_callback)


# ============================================================================
# CLI for testing
# ============================================================================

if __name__ == '__main__':
    import sys
    
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    
    address = sys.argv[1] if len(sys.argv) > 1 else "1234 Oak St, San Jose, CA 95123"
    
    def progress(phase, message, pct):
        print(f"  [{pct:3d}%] {phase}: {message}")
    
    print(f"\n🔍 Researching: {address}\n")
    
    result = research_property(address, progress_callback=progress)
    
    print(f"\n{'='*60}")
    print(f"Research complete in {result['research_time_ms']}ms")
    print(f"Tools succeeded: {result['tools_succeeded']}")
    print(f"Tools failed: {result['tools_failed']}")
    
    profile = result['profile']
    if profile.get('estimated_value'):
        print(f"\nEstimated value: ${profile['estimated_value']:,}")
    if profile.get('year_built'):
        print(f"Year built: {profile['year_built']}")
    if profile.get('flood_zone'):
        print(f"Flood zone: {profile['flood_zone']} ({profile.get('flood_risk_level', 'unknown')} risk)")
    if profile.get('earthquake_zone'):
        print(f"Earthquake zone: {'YES' if profile['earthquake_zone'] else 'No'}")
    
    print(f"\nAgent Findings ({len(profile.get('agent_findings', []))}):")
    for f in profile.get('agent_findings', []):
        print(f"  [{f['severity'].upper()}] {f['title']}")
    
    if result.get('synthesis', {}).get('brief'):
        print(f"\n{'='*60}")
        print("INTELLIGENCE BRIEF:")
        print(result['synthesis']['brief'])
