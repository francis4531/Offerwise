"""
OfferWise Property Comparison Service
Compare 3 properties side-by-side and rank them

This uses our existing intelligence modules but in "quick scan" mode
"""

import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import re

logger = logging.getLogger(__name__)


@dataclass
class PropertyQuickScan:
    """Quick scan results for a single property"""
    address: str
    listing_url: str
    listing_price: int
    
    # Quick scores (0-100)
    offer_score: int
    grade: str  # A+, A, B, C, D, F
    
    # Key metrics
    estimated_repair_cost_low: int
    estimated_repair_cost_high: int
    true_cost: int  # listing_price + avg repairs
    
    # Red flags
    critical_issues_count: int
    safety_concerns_count: int
    transparency_score: int
    
    # Future value estimate
    estimated_5yr_value: int
    estimated_roi_percent: float
    
    def to_dict(self):
        return {
            'address': self.address,
            'listing_url': self.listing_url,
            'listing_price': self.listing_price,
            'offer_score': self.offer_score,
            'grade': self.grade,
            'estimated_repair_cost_low': self.estimated_repair_cost_low,
            'estimated_repair_cost_high': self.estimated_repair_cost_high,
            'true_cost': self.true_cost,
            'critical_issues_count': self.critical_issues_count,
            'safety_concerns_count': self.safety_concerns_count,
            'transparency_score': self.transparency_score,
            'estimated_5yr_value': self.estimated_5yr_value,
            'estimated_roi_percent': self.estimated_roi_percent
        }


@dataclass
class ComparisonResult:
    """Results of comparing 3 properties"""
    property1: PropertyQuickScan
    property2: PropertyQuickScan
    property3: Optional[PropertyQuickScan]
    
    # Rankings
    rankings: List[Dict[str, Any]]  # [{rank: 1, property_num: 2, score: 87}, ...]
    winner_property_num: int
    winner_reason: str
    
    def to_dict(self):
        return {
            'property1': self.property1.to_dict(),
            'property2': self.property2.to_dict(),
            'property3': self.property3.to_dict() if self.property3 else None,
            'rankings': self.rankings,
            'winner_property_num': self.winner_property_num,
            'winner_reason': self.winner_reason
        }


class ComparisonService:
    """
    Service for comparing properties
    """
    
    def __init__(self):
        logger.info("🏆 Comparison Service initialized")
    
    @staticmethod
    def extract_address_from_url(url: str) -> Optional[str]:
        """Extract address from Zillow/Redfin URL"""
        # Example: https://www.zillow.com/homedetails/123-Main-St-San-Francisco-CA-94110/12345_zpid/
        # Example: https://www.redfin.com/CA/San-Francisco/123-Main-St-94110/home/12345
        
        if not url:
            return None
        
        # Try to extract from URL path
        url = url.strip()
        
        # Zillow pattern
        zillow_match = re.search(r'/homedetails/([^/]+)/\d+_zpid', url)
        if zillow_match:
            address_slug = zillow_match.group(1)
            # Convert slug back to address
            address = address_slug.replace('-', ' ')
            return address
        
        # Redfin pattern
        redfin_match = re.search(r'/([^/]+)/home/\d+$', url)
        if redfin_match:
            address_slug = redfin_match.group(1)
            address = address_slug.replace('-', ' ')
            return address
        
        # If no pattern matches, return None
        logger.warning(f"Could not extract address from URL: {url}")
        return None
    
    @staticmethod
    def extract_price_from_url(url: str) -> Optional[int]:
        """Try to extract price from URL if present"""
        # Some URLs contain price info, but most don't
        # This is a placeholder - in reality we'd scrape the listing
        return None
    
    @staticmethod
    def quick_scan_property(address: str, listing_url: str, price: Optional[int] = None) -> PropertyQuickScan:
        """
        Perform quick scan of a property without deep analysis
        
        DISABLED: This feature requires real property data integration.
        Current implementation would generate fake scores, which would damage credibility.
        
        To properly implement, we need:
        1. Zillow/Redfin API integration OR web scraping
        2. Integration with existing OfferWise analysis engine
        3. Real OfferScore™ calculations based on actual property data
        """
        
        raise NotImplementedError(
            "Property comparison is temporarily disabled. "
            "This feature requires integration with property listing APIs "
            "to provide accurate, real data. "
            "Please use the deep analysis feature (upload disclosure + inspection) "
            "for accurate property evaluation."
        )
    
    def compare_properties(
        self,
        property1_url: str,
        property2_url: str,
        property3_url: Optional[str] = None,
        property1_price: Optional[int] = None,
        property2_price: Optional[int] = None,
        property3_price: Optional[int] = None
    ) -> ComparisonResult:
        """
        Compare 2-3 properties and return ranked results
        
        Args:
            property1_url: Zillow/Redfin URL for property 1
            property2_url: Zillow/Redfin URL for property 2
            property3_url: Optional URL for property 3
            property1_price: Optional price override
            property2_price: Optional price override
            property3_price: Optional price override
        
        Returns:
            ComparisonResult with rankings and winner
        """
        
        logger.info(f"🏆 Comparing properties: {property1_url}, {property2_url}, {property3_url}")
        
        # Extract addresses
        address1 = self.extract_address_from_url(property1_url) or "Property 1"
        address2 = self.extract_address_from_url(property2_url) or "Property 2"
        address3 = self.extract_address_from_url(property3_url) if property3_url else None
        
        # Quick scan each property
        scan1 = self.quick_scan_property(address1, property1_url, property1_price)
        scan2 = self.quick_scan_property(address2, property2_url, property2_price)
        scan3 = self.quick_scan_property(address3, property3_url, property3_price) if property3_url and address3 else None
        
        # Rank properties by OfferScore (higher is better)
        properties = [
            {'num': 1, 'scan': scan1},
            {'num': 2, 'scan': scan2}
        ]
        if scan3:
            properties.append({'num': 3, 'scan': scan3})
        
        # Sort by offer_score (descending)
        properties.sort(key=lambda x: x['scan'].offer_score, reverse=True)
        
        # Build rankings
        rankings = []
        for rank, prop in enumerate(properties, 1):
            rankings.append({
                'rank': rank,
                'property_num': prop['num'],
                'score': prop['scan'].offer_score,
                'grade': prop['scan'].grade,
                'address': prop['scan'].address
            })
        
        # Winner is rank 1
        winner = properties[0]
        winner_property_num = winner['num']
        winner_scan = winner['scan']
        
        # Generate winner reason
        if winner_scan.offer_score >= 85:
            winner_reason = f"Best overall value with grade {winner_scan.grade} and strong ROI of {winner_scan.estimated_roi_percent:.1f}%"
        elif winner_scan.critical_issues_count == 0:
            winner_reason = f"No critical issues and lowest true cost at ${winner_scan.true_cost:,}"
        else:
            winner_reason = f"Highest OfferScore™ of {winner_scan.offer_score}/100"
        
        return ComparisonResult(
            property1=scan1,
            property2=scan2,
            property3=scan3,
            rankings=rankings,
            winner_property_num=winner_property_num,
            winner_reason=winner_reason
        )


# Singleton instance
comparison_service = ComparisonService()
