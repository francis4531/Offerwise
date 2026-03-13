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
    2. EXECUTE: Run tools in parallel (web lookups, official APIs)
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
    
    # Census / Demographics (from ACS)
    zip_median_home_value: Optional[int] = None
    zip_median_income: Optional[int] = None
    zip_median_rent: Optional[int] = None
    zip_median_year_built: Optional[int] = None
    zip_population: Optional[int] = None
    zip_owner_occupied_pct: Optional[float] = None
    
    # Environmental
    air_quality_index: Optional[int] = None
    air_quality_category: Optional[str] = None
    
    # Disaster History
    disaster_declarations: List[Dict[str, str]] = field(default_factory=list)
    
    # Earthquake History
    recent_earthquakes: List[Dict[str, Any]] = field(default_factory=list)
    
    # Nearby Amenities (from OSM)
    nearby_amenities: Dict[str, int] = field(default_factory=dict)
    
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
            # NOTE: /arcgis/ path works, /gis/nfhl/ returns 404
            url = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
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
                subtype = attrs.get('ZONE_SUBTY', '')
                
                # Determine risk level from zone + SFHA + subtype
                if is_sfha == 'T':
                    risk_level = 'high'
                elif '0.2 PCT' in subtype.upper():
                    risk_level = 'moderate'  # 500-year floodplain
                elif zone in ('B', 'X500'):
                    risk_level = 'moderate'
                else:
                    risk_level = 'low'
                
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
        
        # Skip for non-California addresses (these APIs only cover CA)
        if profile.state and profile.state.upper() not in ('CA', 'CALIFORNIA'):
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SKIPPED,
                error="California-only tool (property is out of state)",
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
            # CAL FIRE - Fire Threat Level via identify endpoint
            # NOTE: FHSZ/FHSZ_SRA_LRA_Combined doesn't exist on egis.fire.ca.gov
            # FireThreat/MapServer/identify is the working alternative
            fire_url = "https://egis.fire.ca.gov/arcgis/rest/services/FRAP/FireThreat/MapServer/identify"
            fire_params = {
                'geometry': f'{profile.longitude},{profile.latitude}',
                'geometryType': 'esriGeometryPoint',
                'sr': '4326',
                'layers': 'all',
                'tolerance': '2',
                'mapExtent': f'{profile.longitude-0.01},{profile.latitude-0.01},{profile.longitude+0.01},{profile.latitude+0.01}',
                'imageDisplay': '400,400,96',
                'returnGeometry': 'false',
                'f': 'json'
            }
            fire_resp = self._make_request(fire_url, params=fire_params, timeout=15)
            fire_data = fire_resp.json()
            
            fire_results = fire_data.get('results', [])
            if fire_results:
                attrs = fire_results[0].get('attributes', {})
                # THREAT values: 0=Little/None, 1=Moderate, 2=High, 3=Very High, 4=Extreme
                threat_val = attrs.get('Raster.THREAT', '0')
                fuel_val = attrs.get('Raster.FUEL_RANK', '0')
                threat_map = {'0': 'Minimal', '1': 'Moderate', '2': 'High', '3': 'Very High', '4': 'Extreme'}
                threat_label = threat_map.get(str(threat_val), f'Level {threat_val}')
                profile.fire_hazard_zone = threat_label
                results['fire_threat'] = threat_label
                results['fire_fuel_rank'] = fuel_val
            else:
                profile.fire_hazard_zone = 'Minimal'
                results['fire_threat'] = 'Minimal'
        except Exception as e:
            results['fire_error'] = str(e)
        
        # Also check State Responsibility Area status
        try:
            sra_url = "https://egis.fire.ca.gov/arcgis/rest/services/FRAP/SRA/MapServer/0/query"
            sra_params = {
                'geometry': f'{profile.longitude},{profile.latitude}',
                'geometryType': 'esriGeometryPoint',
                'inSR': '4326',
                'spatialRel': 'esriSpatialRelIntersects',
                'outFields': 'SRA',
                'returnGeometry': 'false',
                'f': 'json'
            }
            sra_resp = self._make_request(sra_url, params=sra_params, timeout=10)
            sra_data = sra_resp.json()
            sra_features = sra_data.get('features', [])
            if sra_features:
                sra_type = sra_features[0].get('attributes', {}).get('SRA', '')
                # SRA = State Responsibility Area (wildland fire protection), LRA = Local
                results['responsibility_area'] = sra_type
        except Exception:
            pass  # Non-critical
        
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
# TOOL: Census ACS Demographics (free, no key!)
# ---------------------------------------------------------------------------
class CensusAcsTool(ResearchTool):
    """US Census American Community Survey — zip-level demographics"""
    
    name = "census_acs"
    description = "Median home value, income, rent, year built, population for the zip code"
    requires_geocoding = True
    priority = 20
    
    def execute(self, address: str, profile: PropertyProfile) -> ToolResult:
        start = time.time()
        zip_code = profile.zip_code
        if not zip_code:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SKIPPED,
                error="No zip code available",
                duration_ms=int((time.time() - start) * 1000)
            )
        
        try:
            fields = [
                'B25077_001E',  # Median home value
                'B19013_001E',  # Median household income
                'B01003_001E',  # Total population
                'B25064_001E',  # Median gross rent
                'B25035_001E',  # Median year structure built
                'B25003_002E',  # Owner-occupied units
                'B25003_003E',  # Renter-occupied units
            ]
            resp = self._make_request(
                'https://api.census.gov/data/2022/acs/acs5',
                params={
                    'get': ','.join(fields),
                    'for': f'zip code tabulation area:{zip_code}'
                },
                timeout=15
            )
            data = resp.json()
            
            if len(data) < 2:
                return ToolResult(
                    tool_name=self.name, status=ToolStatus.FAILED,
                    error="No data for this zip code",
                    duration_ms=int((time.time() - start) * 1000)
                )
            
            values = data[1]
            
            def safe_int(val):
                try: return int(val) if val and int(val) > 0 else None
                except Exception: return None
            
            median_value = safe_int(values[0])
            median_income = safe_int(values[1])
            population = safe_int(values[2])
            median_rent = safe_int(values[3])
            median_year_built = safe_int(values[4])
            owner_units = safe_int(values[5]) or 0
            renter_units = safe_int(values[6]) or 0
            
            total_units = owner_units + renter_units
            owner_pct = round(owner_units / total_units * 100, 1) if total_units > 0 else None
            
            profile.zip_median_home_value = median_value
            profile.zip_median_income = median_income
            profile.zip_median_rent = median_rent
            profile.zip_median_year_built = median_year_built
            profile.zip_population = population
            profile.zip_owner_occupied_pct = owner_pct
            
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SUCCESS,
                data={
                    'median_home_value': median_value,
                    'median_income': median_income,
                    'median_rent': median_rent,
                    'median_year_built': median_year_built,
                    'population': population,
                    'owner_occupied_pct': owner_pct,
                },
                duration_ms=int((time.time() - start) * 1000),
                source_url="https://data.census.gov"
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.FAILED,
                error=str(e), duration_ms=int((time.time() - start) * 1000)
            )


# ---------------------------------------------------------------------------
# TOOL: OpenFEMA Disaster Declarations (free, no key)
# ---------------------------------------------------------------------------
class DisasterHistoryTool(ResearchTool):
    """FEMA disaster declarations for the county"""
    
    name = "disaster_history"
    description = "Historical disaster declarations (floods, fires, storms) for the county"
    requires_geocoding = True
    priority = 30
    
    def execute(self, address: str, profile: PropertyProfile) -> ToolResult:
        start = time.time()
        county = profile.county
        state = profile.state  # FEMA API uses abbreviations (CA, not California)
        
        if not county or not state:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SKIPPED,
                error="No county/state available",
                duration_ms=int((time.time() - start) * 1000)
            )
        
        try:
            resp = self._make_request(
                'https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries',
                params={
                    '$filter': f"state eq '{state.upper()}' and designatedArea eq '{county} (County)'",
                    '$top': 10,
                    '$orderby': 'declarationDate desc',
                    '$select': 'declarationDate,declarationTitle,incidentType,incidentBeginDate,incidentEndDate'
                },
                timeout=15
            )
            data = resp.json()
            records = data.get('DisasterDeclarationsSummaries', [])
            
            declarations = []
            for r in records:
                declarations.append({
                    'date': (r.get('declarationDate', '') or '')[:10],
                    'title': r.get('declarationTitle', ''),
                    'type': r.get('incidentType', ''),
                })
            
            profile.disaster_declarations = declarations
            
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SUCCESS,
                data={
                    'declarations': declarations,
                    'total_recent': len(declarations),
                },
                duration_ms=int((time.time() - start) * 1000),
                source_url="https://www.fema.gov/api/open"
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.FAILED,
                error=str(e), duration_ms=int((time.time() - start) * 1000)
            )
    
    @staticmethod
    def _state_abbrev_to_name(abbrev: str) -> Optional[str]:
        """Convert state abbreviation to full name for FEMA API"""
        states = {
            'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas',
            'CA': 'California', 'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware',
            'FL': 'Florida', 'GA': 'Georgia', 'HI': 'Hawaii', 'ID': 'Idaho',
            'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa', 'KS': 'Kansas',
            'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland',
            'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi',
            'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada',
            'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico', 'NY': 'New York',
            'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma',
            'OR': 'Oregon', 'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina',
            'SD': 'South Dakota', 'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah',
            'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington', 'WV': 'West Virginia',
            'WI': 'Wisconsin', 'WY': 'Wyoming', 'DC': 'District of Columbia',
        }
        return states.get(abbrev.upper() if abbrev else '', None)


# ---------------------------------------------------------------------------
# TOOL: USGS Earthquake History (free, no key)
# ---------------------------------------------------------------------------
class EarthquakeHistoryTool(ResearchTool):
    """USGS earthquake catalog — recent significant quakes near the property"""
    
    name = "earthquake_history"
    description = "Recent M3.0+ earthquakes within 50km of the property"
    requires_geocoding = True
    priority = 35
    
    def execute(self, address: str, profile: PropertyProfile) -> ToolResult:
        start = time.time()
        if not profile.latitude or not profile.longitude:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SKIPPED,
                error="No coordinates available",
                duration_ms=int((time.time() - start) * 1000)
            )
        
        try:
            resp = self._make_request(
                'https://earthquake.usgs.gov/fdsnws/event/1/query',
                params={
                    'format': 'geojson',
                    'latitude': profile.latitude,
                    'longitude': profile.longitude,
                    'maxradiuskm': 50,
                    'minmagnitude': 3.0,
                    'starttime': '2019-01-01',
                    'limit': 10,
                    'orderby': 'magnitude'
                },
                timeout=15
            )
            data = resp.json()
            features = data.get('features', [])
            
            quakes = []
            for f in features:
                props = f.get('properties', {})
                quakes.append({
                    'magnitude': props.get('mag', 0),
                    'place': props.get('place', ''),
                    'time': props.get('time', 0),  # epoch ms
                })
            
            profile.recent_earthquakes = quakes
            
            max_mag = max((q['magnitude'] for q in quakes), default=0)
            
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SUCCESS,
                data={
                    'quake_count': len(quakes),
                    'max_magnitude': max_mag,
                    'quakes': quakes[:5],
                },
                duration_ms=int((time.time() - start) * 1000),
                source_url="https://earthquake.usgs.gov"
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.FAILED,
                error=str(e), duration_ms=int((time.time() - start) * 1000)
            )


# ---------------------------------------------------------------------------
# TOOL: Nearby Amenities via OpenStreetMap Overpass (free, no key)
# ---------------------------------------------------------------------------
class NearbyAmenitiesTool(ResearchTool):
    """OpenStreetMap Overpass — nearby schools, hospitals, grocery, parks, etc."""
    
    name = "nearby_amenities"
    description = "Nearby schools, hospitals, grocery stores, parks, police/fire stations"
    requires_geocoding = True
    priority = 40
    
    def execute(self, address: str, profile: PropertyProfile) -> ToolResult:
        start = time.time()
        if not profile.latitude or not profile.longitude:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SKIPPED,
                error="No coordinates available",
                duration_ms=int((time.time() - start) * 1000)
            )
        
        lat, lng = profile.latitude, profile.longitude
        
        try:
            query = f"""[out:json][timeout:10];
            (
              nwr["amenity"="school"](around:2000,{lat},{lng});
              nwr["amenity"="hospital"](around:5000,{lat},{lng});
              nwr["amenity"="fire_station"](around:3000,{lat},{lng});
              nwr["amenity"="police"](around:3000,{lat},{lng});
              nwr["shop"="supermarket"](around:1500,{lat},{lng});
              nwr["amenity"="pharmacy"](around:1500,{lat},{lng});
              nwr["leisure"="park"]["name"](around:1500,{lat},{lng});
            );
            out body 60;"""
            
            # Try primary, then fallback Overpass server
            overpass_urls = [
                'https://overpass-api.de/api/interpreter',
                'https://overpass.kumi.systems/api/interpreter',
            ]
            
            resp = None
            for overpass_url in overpass_urls:
                try:
                    resp = self._make_request(
                        overpass_url,
                        method='POST',
                        data={'data': query},
                        timeout=12
                    )
                    if resp.status_code == 200:
                        break
                except Exception:
                    continue
            
            if not resp or resp.status_code != 200:
                return ToolResult(
                    tool_name=self.name, status=ToolStatus.FAILED,
                    error="Overpass API unavailable",
                    duration_ms=int((time.time() - start) * 1000)
                )
            
            data = resp.json()
            elements = data.get('elements', [])
            
            # Count by category
            counts = {}
            names_by_type = {}
            for el in elements:
                tags = el.get('tags', {})
                cat = tags.get('amenity', tags.get('shop', tags.get('leisure', 'other')))
                counts[cat] = counts.get(cat, 0) + 1
                name = tags.get('name', '')
                if name and cat not in names_by_type:
                    names_by_type[cat] = []
                if name:
                    names_by_type[cat].append(name)
            
            profile.nearby_amenities = counts
            
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SUCCESS,
                data={
                    'total_pois': len(elements),
                    'counts': counts,
                    'sample_names': {k: v[:3] for k, v in names_by_type.items()},
                },
                duration_ms=int((time.time() - start) * 1000),
                source_url="https://www.openstreetmap.org"
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.FAILED,
                error=str(e), duration_ms=int((time.time() - start) * 1000)
            )
    
    def _make_request(self, url, params=None, timeout=10, method='GET', data=None, **kwargs):
        """Override to support POST for Overpass"""
        import requests as _req
        headers = {'User-Agent': 'OfferWise/1.0 (property-research)'}
        if method == 'POST':
            return _req.post(url, data=data, headers=headers, timeout=timeout)
        return _req.get(url, params=params, headers=headers, timeout=timeout)


# ---------------------------------------------------------------------------
# TOOL: AirNow EPA Air Quality (free key)
# ---------------------------------------------------------------------------
class AirQualityTool(ResearchTool):
    """EPA AirNow — current air quality index and pollutant levels"""
    
    name = "air_quality"
    description = "Current air quality index (AQI), PM2.5, ozone levels"
    requires_geocoding = True
    priority = 45
    
    def execute(self, address: str, profile: PropertyProfile) -> ToolResult:
        start = time.time()
        api_key = os.environ.get('AIRNOW_API_KEY')
        if not api_key:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SKIPPED,
                error="AIRNOW_API_KEY not configured",
                duration_ms=int((time.time() - start) * 1000)
            )
        
        if not profile.latitude or not profile.longitude:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SKIPPED,
                error="No coordinates available",
                duration_ms=int((time.time() - start) * 1000)
            )
        
        try:
            resp = self._make_request(
                'https://www.airnowapi.org/aq/observation/latLong/current/',
                params={
                    'format': 'application/json',
                    'latitude': profile.latitude,
                    'longitude': profile.longitude,
                    'distance': 25,
                    'API_KEY': api_key
                },
                timeout=15
            )
            data = resp.json()
            
            if not data:
                return ToolResult(
                    tool_name=self.name, status=ToolStatus.SUCCESS,
                    data={'aqi': None, 'category': 'No data available'},
                    duration_ms=int((time.time() - start) * 1000),
                    source_url="https://www.airnow.gov"
                )
            
            # Find the highest AQI reading
            max_aqi = 0
            category = 'Good'
            pollutants = {}
            for reading in data:
                aqi = reading.get('AQI', 0)
                if aqi > max_aqi:
                    max_aqi = aqi
                    category = reading.get('Category', {}).get('Name', 'Unknown')
                param = reading.get('ParameterName', '')
                pollutants[param] = aqi
            
            profile.air_quality_index = max_aqi
            profile.air_quality_category = category
            
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SUCCESS,
                data={
                    'aqi': max_aqi,
                    'category': category,
                    'pollutants': pollutants,
                },
                duration_ms=int((time.time() - start) * 1000),
                source_url="https://www.airnow.gov"
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.FAILED,
                error=str(e), duration_ms=int((time.time() - start) * 1000)
            )


# ---------------------------------------------------------------------------
# TOOL: RentCast Property Details + AVM (free tier: 50/month)
# ---------------------------------------------------------------------------
class RentCastTool(ResearchTool):
    """RentCast — property details, automated valuation, AND comparable sales (Phase 2)"""
    
    name = "rentcast"
    description = "Property details (beds/bath/sqft), valuation estimate, comparable sales"
    priority = 8  # High priority — best property detail source
    
    def execute(self, address: str, profile: PropertyProfile) -> ToolResult:
        start = time.time()
        api_key = os.environ.get('RENTCAST_API_KEY')
        if not api_key:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SKIPPED,
                error="RENTCAST_API_KEY not configured",
                duration_ms=int((time.time() - start) * 1000)
            )
        
        try:
            # Phase 2: Use /avm/value instead of /properties
            # Gets property details + AVM estimate + comparable sales in ONE call
            resp = self._make_request(
                'https://api.rentcast.io/v1/avm/value',
                params={
                    'address': address,
                    'compCount': 15,
                    'maxRadius': 5,
                    'daysOld': 180
                },
                headers={'X-Api-Key': api_key, 'Accept': 'application/json'},
                timeout=15
            )
            
            if resp.status_code == 404:
                return ToolResult(
                    tool_name=self.name, status=ToolStatus.SUCCESS,
                    data={'found': False},
                    duration_ms=int((time.time() - start) * 1000),
                    source_url="https://www.rentcast.io"
                )
            
            if resp.status_code != 200:
                return ToolResult(
                    tool_name=self.name, status=ToolStatus.FAILED,
                    error=f"RentCast API returned HTTP {resp.status_code}",
                    duration_ms=int((time.time() - start) * 1000)
                )
            
            data = resp.json()
            
            # Extract subject property details
            subject = data.get('subjectProperty', data)
            profile.bedrooms = subject.get('bedrooms')
            profile.bathrooms = subject.get('bathrooms')
            profile.sqft = subject.get('squareFootage')
            profile.lot_size_sqft = subject.get('lotSize')
            profile.year_built = subject.get('yearBuilt')
            profile.property_type = subject.get('propertyType')
            profile.tax_assessed_value = subject.get('assessedValue')
            
            # AVM estimate
            est = data.get('price', 0) or 0
            if est:
                profile.estimated_value = int(est)
            
            # Phase 2: Extract comparable sales for MarketIntelligence
            comps_raw = data.get('comparables', []) or []
            comps_data = []
            for comp in comps_raw:
                price = int(comp.get('price', 0) or 0)
                sq = int(comp.get('squareFootage', 0) or 0)
                if price <= 0:
                    continue
                comps_data.append({
                    'address': comp.get('formattedAddress', comp.get('addressLine1', '')),
                    'price': price,
                    'sqft': sq,
                    'price_per_sqft': round(price / sq, 2) if sq > 0 else 0,
                    'bedrooms': int(comp.get('bedrooms', 0) or 0),
                    'bathrooms': float(comp.get('bathrooms', 0) or 0),
                    'year_built': int(comp.get('yearBuilt', 0) or 0),
                    'lot_size': int(comp.get('lotSize', 0) or 0),
                    'property_type': comp.get('propertyType', '') or '',
                    'days_on_market': int(comp.get('daysOnMarket', 0) or 0),
                    'distance_miles': round(float(comp.get('distance', 0) or 0), 2),
                    'correlation': round(float(comp.get('correlation', 0) or 0), 3),
                    'listing_type': comp.get('listingType', 'Standard') or 'Standard',
                    'status': comp.get('status', '') or '',
                    'listed_date': comp.get('listedDate', '') or '',
                    'sold_date': comp.get('removedDate', '') or '',
                    'last_seen': comp.get('lastSeenDate', '') or '',
                })
            
            logger.info(f"   🏠 RentCast: {profile.bedrooms}bd/{profile.bathrooms}ba, "
                        f"AVM=${profile.estimated_value:,}, {len(comps_data)} comps")
            
            # Distressed sale breakdown
            foreclosures = len([c for c in comps_data if c.get('listing_type') == 'Foreclosure'])
            short_sales = len([c for c in comps_data if c.get('listing_type') == 'Short Sale'])
            new_construction = len([c for c in comps_data if c.get('listing_type') == 'New Construction'])
            if foreclosures or short_sales:
                logger.info(f"   ⚠️  Distressed: {foreclosures} foreclosure, {short_sales} short sale in comps")
            
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SUCCESS,
                data={
                    'found': True,
                    'bedrooms': profile.bedrooms,
                    'bathrooms': profile.bathrooms,
                    'sqft': profile.sqft,
                    'year_built': profile.year_built,
                    'lot_size': profile.lot_size_sqft,
                    'property_type': profile.property_type,
                    'estimated_value': profile.estimated_value,
                    'assessed_value': profile.tax_assessed_value,
                    # AVM with confidence range
                    'avm_price': int(data.get('price', 0) or 0),
                    'avm_price_low': int(data.get('priceRangeLow', 0) or 0),
                    'avm_price_high': int(data.get('priceRangeHigh', 0) or 0),
                    # Full comparables
                    'comparables': comps_data,
                    # Distressed sale counts
                    'foreclosure_count': foreclosures,
                    'short_sale_count': short_sales,
                    'new_construction_count': new_construction,
                },
                duration_ms=int((time.time() - start) * 1000),
                source_url="https://www.rentcast.io"
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.FAILED,
                error=str(e), duration_ms=int((time.time() - start) * 1000)
            )
    
    def _make_request(self, url, params=None, timeout=10, headers=None, **kwargs):
        """Override to support custom headers"""
        import requests as _req
        hdrs = {'User-Agent': 'OfferWise/1.0 (property-research)'}
        if headers:
            hdrs.update(headers)
        return _req.get(url, params=params, headers=hdrs, timeout=timeout)


class MarketStatsTool(ResearchTool):
    """RentCast — ZIP-level market statistics (Phase 2)"""
    
    name = "market_stats"
    description = "ZIP-level market statistics: median price, DOM, inventory trends"
    priority = 9  # Run after RentCast property details
    requires_geocoding = False
    
    def execute(self, address: str, profile: PropertyProfile) -> ToolResult:
        start = time.time()
        api_key = os.environ.get('RENTCAST_API_KEY')
        if not api_key:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SKIPPED,
                error="RENTCAST_API_KEY not configured",
                duration_ms=int((time.time() - start) * 1000)
            )
        
        # Extract ZIP from address
        import re
        match = re.search(r'\b(\d{5})(?:-\d{4})?\b', address)
        if not match:
            # Try from profile
            zip_code = getattr(profile, 'zip_code', '') or ''
            if not zip_code:
                return ToolResult(
                    tool_name=self.name, status=ToolStatus.SKIPPED,
                    error="Could not extract ZIP code from address",
                    duration_ms=int((time.time() - start) * 1000)
                )
        else:
            zip_code = match.group(1)
        
        try:
            import requests as _req
            resp = _req.get(
                'https://api.rentcast.io/v1/markets',
                params={
                    'zipCode': zip_code,
                    'dataType': 'Sale',
                    'historyRange': 6
                },
                headers={
                    'X-Api-Key': api_key,
                    'Accept': 'application/json',
                    'User-Agent': 'OfferWise/1.0 (property-research)'
                },
                timeout=10
            )
            
            if resp.status_code != 200:
                return ToolResult(
                    tool_name=self.name, status=ToolStatus.FAILED,
                    error=f"Market stats API returned HTTP {resp.status_code}",
                    duration_ms=int((time.time() - start) * 1000)
                )
            
            data = resp.json()
            sale = data.get('saleData', data)
            
            market_data = {
                'zip_code': zip_code,
                # Aggregate stats
                'median_sale_price': int(sale.get('medianPrice', 0) or 0),
                'average_sale_price': int(sale.get('averagePrice', 0) or 0),
                'min_price': int(sale.get('minPrice', 0) or 0),
                'max_price': int(sale.get('maxPrice', 0) or 0),
                'median_price_per_sqft': float(sale.get('medianPricePerSquareFoot', 0) or 0),
                'avg_price_per_sqft': float(sale.get('averagePricePerSquareFoot', 0) or 0),
                'average_sqft': int(sale.get('averageSquareFootage', 0) or 0),
                'median_sqft': int(sale.get('medianSquareFootage', 0) or 0),
                'average_days_on_market': int(sale.get('averageDaysOnMarket', 0) or 0),
                'median_days_on_market': int(sale.get('medianDaysOnMarket', 0) or 0),
                'min_dom': int(sale.get('minDaysOnMarket', 0) or 0),
                'max_dom': int(sale.get('maxDaysOnMarket', 0) or 0),
                'total_listings': int(sale.get('totalListings', 0) or 0),
                'new_listings': int(sale.get('newListings', 0) or 0),
            }
            
            # Property-type-specific stats (e.g., Single Family vs Condo in same ZIP)
            data_by_type = sale.get('dataByPropertyType', []) or []
            prop_type = getattr(profile, 'property_type', '') or ''
            type_stats = {}
            for entry in data_by_type:
                if not isinstance(entry, dict):
                    continue
                pt = entry.get('propertyType', '')
                type_stats[pt] = {
                    'median_price': int(entry.get('medianPrice', 0) or 0),
                    'avg_price_per_sqft': float(entry.get('averagePricePerSquareFoot', 0) or 0),
                    'avg_dom': int(entry.get('averageDaysOnMarket', 0) or 0),
                    'total_listings': int(entry.get('totalListings', 0) or 0),
                }
                # If this matches the subject property type, highlight it
                if prop_type and pt and prop_type.lower().replace(' ', '') == pt.lower().replace(' ', ''):
                    market_data['type_match_median'] = type_stats[pt]['median_price']
                    market_data['type_match_ppsqft'] = type_stats[pt]['avg_price_per_sqft']
                    market_data['type_match_dom'] = type_stats[pt]['avg_dom']
                    market_data['type_match_listings'] = type_stats[pt]['total_listings']
            market_data['stats_by_type'] = type_stats
            
            # Bedroom-specific stats
            data_by_beds = sale.get('dataByBedrooms', []) or []
            beds = getattr(profile, 'bedrooms', 0) or 0
            bed_stats = {}
            for entry in data_by_beds:
                if not isinstance(entry, dict):
                    continue
                b = int(entry.get('bedrooms', 0) or 0)
                bed_stats[b] = {
                    'median_price': int(entry.get('medianPrice', 0) or 0),
                    'avg_price_per_sqft': float(entry.get('averagePricePerSquareFoot', 0) or 0),
                    'avg_dom': int(entry.get('averageDaysOnMarket', 0) or 0),
                    'total_listings': int(entry.get('totalListings', 0) or 0),
                }
                if beds and b == beds:
                    market_data['bed_match_median'] = bed_stats[b]['median_price']
                    market_data['bed_match_ppsqft'] = bed_stats[b]['avg_price_per_sqft']
                    market_data['bed_match_dom'] = bed_stats[b]['avg_dom']
                    market_data['bed_match_listings'] = bed_stats[b]['total_listings']
            market_data['stats_by_beds'] = bed_stats
            
            # Calculate trends from history
            history = sale.get('history', []) or data.get('history', []) or []
            monthly_history = []
            if len(history) >= 2:
                recent = history[-1] if isinstance(history[-1], dict) else {}
                prior = history[-2] if isinstance(history[-2], dict) else {}
                
                rp = float(recent.get('averagePrice', 0) or 0)
                pp = float(prior.get('averagePrice', 0) or 0)
                if pp > 0:
                    market_data['price_trend_pct'] = round((rp - pp) / pp * 100, 1)
                
                rd = float(recent.get('averageDaysOnMarket', 0) or 0)
                pd = float(prior.get('averageDaysOnMarket', 0) or 0)
                if pd > 0:
                    market_data['dom_trend_pct'] = round((rd - pd) / pd * 100, 1)
                
                rl = float(recent.get('totalListings', 0) or 0)
                pl = float(prior.get('totalListings', 0) or 0)
                if pl > 0:
                    market_data['inventory_trend_pct'] = round((rl - pl) / pl * 100, 1)
            
            # Store monthly history for trend charts
            for h in history:
                if isinstance(h, dict):
                    monthly_history.append({
                        'month': h.get('month', ''),
                        'avg_price': int(h.get('averagePrice', 0) or 0),
                        'median_price': int(h.get('medianPrice', 0) or 0),
                        'avg_dom': int(h.get('averageDaysOnMarket', 0) or 0),
                        'total_listings': int(h.get('totalListings', 0) or 0),
                        'avg_ppsqft': float(h.get('averagePricePerSquareFoot', 0) or 0),
                    })
            market_data['history'] = monthly_history
            
            logger.info(f"   📊 Market stats ({zip_code}): median=${market_data['median_sale_price']:,}, "
                        f"avg DOM={market_data['average_days_on_market']}, "
                        f"listings={market_data['total_listings']}")
            
            return ToolResult(
                tool_name=self.name, status=ToolStatus.SUCCESS,
                data=market_data,
                duration_ms=int((time.time() - start) * 1000),
                source_url="https://www.rentcast.io"
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.name, status=ToolStatus.FAILED,
                error=str(e), duration_ms=int((time.time() - start) * 1000)
            )


# ---------------------------------------------------------------------------
# TOOL: Redfin Property Data — DISABLED (v5.59.65)
# ---------------------------------------------------------------------------
# REMOVED: This tool was accessing Redfin's internal "stingray" API endpoints
# which are undocumented and not intended for third-party use. It also spoofed
# the User-Agent to pretend to be a browser. This violates our policy of only
# using official, authorized APIs.
#
# RentCast (priority=8) provides the same property data through their official
# API, so there is no data gap from removing this.
# ---------------------------------------------------------------------------
class RedfinTool(ResearchTool):
    """DISABLED — was using undocumented internal Redfin APIs"""
    
    name = "redfin"
    description = "DISABLED - Redfin does not offer a public API"
    priority = 0  # Disabled
    
    def execute(self, address: str, profile: PropertyProfile) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SKIPPED,
            error="Redfin tool disabled — no official public API available. Use RentCast instead.",
            duration_ms=0
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
    # NOTE: RedfinTool disabled — blocks server-side requests (403 CloudFront)
    # NOTE: PermitHistoryTool disabled — no reliable public API for county permits
    # NOTE: SchoolRatingsTool disabled — GreatSchools now paid ($49/mo minimum)
    TOOL_CLASSES = [
        GeocodingTool,
        RentCastTool,           # 🔑 RENTCAST_API_KEY (50/mo free) — property details + AVM + comps
        MarketStatsTool,        # 🔑 RENTCAST_API_KEY — ZIP-level market stats (Phase 2)
        FloodZoneTool,
        CaliforniaHazardsTool,
        WalkScoreTool,          # 🔑 WALKSCORE_API_KEY (5k/day free)
        CensusAcsTool,          # ✅ FREE — median value, income, rent, demographics
        DisasterHistoryTool,    # ✅ FREE — FEMA disaster declarations for county
        EarthquakeHistoryTool,  # ✅ FREE — USGS recent quakes nearby
        NearbyAmenitiesTool,    # ✅ FREE — OSM schools, hospitals, grocery, parks
        AirQualityTool,         # 🔑 AIRNOW_API_KEY (unlimited free)
        # RedfinTool,           # ❌ Redfin blocks server-side requests (403)
        # SchoolRatingsTool,    # ❌ GreatSchools API is paid ($49/mo)
        # PermitHistoryTool,    # ❌ No reliable county permit APIs found
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
                except Exception:
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
            if profile.flood_risk_level == 'high':
                flood_detail = (
                    'This property is in a Special Flood Hazard Area (SFHA). '
                    'Flood insurance is mandatory for federally-backed mortgages. '
                    'Typical cost: $1,500-$5,000/year depending on coverage.'
                )
            else:
                flood_detail = (
                    'This property is in or near a 500-year floodplain (0.2% annual chance). '
                    'Flood insurance is not required but recommended. '
                    'FEMA Zone X with moderate risk — check for past flood events in the area.'
                )
            findings.append({
                'type': 'risk',
                'severity': 'high' if profile.flood_risk_level == 'high' else 'medium',
                'title': f'FEMA Flood Zone: {profile.flood_zone} ({profile.flood_risk_level} risk)',
                'detail': flood_detail,
                'cross_check': 'Check seller disclosure for flood history and past claims'
            })
        
        # Finding: Fire hazard zone
        fire_zone = (profile.fire_hazard_zone or '').lower()
        if fire_zone in ('high', 'very high', 'extreme'):
            findings.append({
                'type': 'risk',
                'severity': 'high' if fire_zone in ('very high', 'extreme') else 'medium',
                'title': f'{profile.fire_hazard_zone} Fire Threat Zone',
                'detail': (
                    f'CAL FIRE designates this area as {profile.fire_hazard_zone} fire threat. '
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
        
        # Finding: Census area median home value context
        if profile.zip_median_home_value:
            findings.append({
                'type': 'insight',
                'severity': 'info',
                'title': f'Area median home value: ${profile.zip_median_home_value:,}',
                'detail': (
                    f'ZIP {profile.zip_code} median: ${profile.zip_median_home_value:,}. '
                    f'Median HH income: ${profile.zip_median_income:,}. '
                    f'{"Owner-dominated" if (profile.zip_owner_occupied_pct or 0) > 55 else "Renter-heavy"} area '
                    f'({profile.zip_owner_occupied_pct or 0}% owner-occupied).'
                ),
                'cross_check': 'Compare asking price against area median to assess relative value'
            })
        
        # Finding: Disaster history
        if profile.disaster_declarations:
            recent_count = len(profile.disaster_declarations)
            types = list(set(d.get('type', '') for d in profile.disaster_declarations))
            findings.append({
                'type': 'risk',
                'severity': 'medium' if recent_count >= 3 else 'low',
                'title': f'{recent_count} FEMA disaster declarations for {profile.county} County',
                'detail': (
                    f'Recent disaster types: {", ".join(types)}. '
                    f'Most recent: {profile.disaster_declarations[0].get("title", "")} '
                    f'({profile.disaster_declarations[0].get("date", "")}). '
                    f'History of natural disasters may affect insurance costs.'
                ),
                'cross_check': 'Ask seller about past damage claims and repairs from these events'
            })
        
        # Finding: Significant earthquake activity
        if profile.recent_earthquakes:
            max_quake = max(profile.recent_earthquakes, key=lambda q: q.get('magnitude', 0))
            max_mag = max_quake.get('magnitude', 0)
            if max_mag >= 4.0:
                findings.append({
                    'type': 'risk',
                    'severity': 'high' if max_mag >= 5.0 else 'medium',
                    'title': f'M{max_mag} earthquake recorded within 50km',
                    'detail': (
                        f'Location: {max_quake.get("place", "nearby")}. '
                        f'{len(profile.recent_earthquakes)} M3.0+ quakes since 2019 within 50km. '
                        f'Consider earthquake insurance and verify foundation retrofit status.'
                    ),
                    'cross_check': 'Check seller disclosure for earthquake damage history'
                })
        
        # Finding: Air quality concern
        if profile.air_quality_index and profile.air_quality_index > 100:
            findings.append({
                'type': 'risk',
                'severity': 'high' if profile.air_quality_index > 150 else 'medium',
                'title': f'Air quality: {profile.air_quality_category} (AQI {profile.air_quality_index})',
                'detail': (
                    f'Current AQI of {profile.air_quality_index} is in the '
                    f'{profile.air_quality_category} range. '
                    f'Consider proximity to highways, industrial areas, or wildfire smoke patterns.'
                ),
                'cross_check': None
            })
        
        # Finding: Limited nearby amenities
        amenities = profile.nearby_amenities
        if amenities:
            schools = amenities.get('school', 0)
            hospitals = amenities.get('hospital', 0)
            grocery = amenities.get('supermarket', 0)
            if grocery == 0:
                findings.append({
                    'type': 'insight',
                    'severity': 'low',
                    'title': 'No grocery stores within 1.5km',
                    'detail': 'No supermarkets found within walking distance. Car access to grocery shopping may be essential.',
                    'cross_check': None
                })
            if hospitals == 0:
                findings.append({
                    'type': 'insight',
                    'severity': 'low',
                    'title': 'No hospitals within 5km',
                    'detail': 'No hospitals found within 5km. Consider distance to emergency medical care.',
                    'cross_check': None
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

            _t0 = time.time()
            response = self.ai_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1000,
                temperature=0,
                messages=[{"role": "user", "content": prompt}]
            )
            try:
                try:
                    from app import app as _ow_app, db as _ow_db
                except Exception:
                    _ow_app, _ow_db = None, None
                from ai_cost_tracker import track_ai_call as _track
                _track(response, "property-research", (time.time() - _t0) * 1000, db=_ow_db, app=_ow_app)
            except Exception:
                pass
            
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
