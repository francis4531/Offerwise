"""
OfferWise Data Validation Module
Comprehensive validation to prevent bad data from reaching users
"""

import re
from typing import Dict, List, Optional, Tuple
from enum import Enum


class ValidationError(Exception):
    """Raised when validation fails"""
    pass


class CostValidator:
    """Validate cost estimates for realism"""
    
    # Minimum costs for CRITICAL severity issues (in dollars)
    CRITICAL_MINIMUMS = {
        'foundation_structure': 25000,
        'roof_exterior': 15000,
        'electrical': 8000,
        'plumbing': 10000,
        'hvac_systems': 6000,
        'environmental': 5000,
        'legal_title': 3000,
        'insurance_hoa': 2000
    }
    
    # Minimum costs for MAJOR severity issues
    MAJOR_MINIMUMS = {
        'foundation_structure': 10000,
        'roof_exterior': 8000,
        'electrical': 3000,
        'plumbing': 4000,
        'hvac_systems': 3000,
        'environmental': 2000,
        'legal_title': 1500,
        'insurance_hoa': 1000
    }
    
    @staticmethod
    def validate_cost_range(cost_low: float, cost_high: float, category: str, severity: str) -> Tuple[float, float]:
        """
        Validate and correct cost estimates
        
        Args:
            cost_low: Low estimate
            cost_high: High estimate
            category: Category name (e.g., 'foundation_structure')
            severity: Severity level ('critical', 'major', 'moderate', 'minor')
        
        Returns:
            Tuple of (validated_low, validated_high)
        
        Raises:
            ValidationError: If costs are completely invalid
        """
        # Check for inverted range
        if cost_low > cost_high and cost_high > 0:
            # Swap them
            cost_low, cost_high = cost_high, cost_low
        
        # Check for negative costs
        if cost_low < 0 or cost_high < 0:
            raise ValidationError(f"Negative costs not allowed: {cost_low}, {cost_high}")
        
        # Apply minimums for critical issues
        if severity.lower() == 'critical':
            minimum = CostValidator.CRITICAL_MINIMUMS.get(category.lower(), 5000)
            if cost_high < minimum:
                # Apply realistic minimum
                cost_low = minimum
                cost_high = minimum * 2
        
        # Apply minimums for major issues
        elif severity.lower() == 'major':
            minimum = CostValidator.MAJOR_MINIMUMS.get(category.lower(), 2000)
            if cost_high < minimum:
                cost_low = minimum
                cost_high = minimum * 2
        
        # Ensure reasonable ratio (high should be 1.5x to 3x low)
        if cost_low > 0 and cost_high > 0:
            ratio = cost_high / cost_low
            if ratio < 1.2:
                # Too narrow, widen it
                cost_high = cost_low * 1.8
            elif ratio > 5:
                # Too wide, narrow it
                cost_high = cost_low * 3
        
        return cost_low, cost_high
    
    @staticmethod
    def validate_total_costs(repair_costs: float, property_price: float) -> float:
        """Validate total repair costs are reasonable"""
        if repair_costs < 0:
            raise ValidationError("Negative repair costs")
        
        # Repair costs shouldn't exceed property value
        if repair_costs > property_price:
            raise ValidationError(f"Repair costs ${repair_costs:,.0f} exceed property value ${property_price:,.0f}")
        
        # Warning if repair costs are > 50% of property value
        if repair_costs > property_price * 0.5:
            # This is still possible (teardown), but log it
            pass
        
        return repair_costs


class TextValidator:
    """Validate text quality"""
    
    # Severity keywords to strip from beginning of text
    SEVERITY_PATTERN = r'^(CRITICAL|MAJOR|MODERATE|MINOR|Critical|Major|Moderate|Minor)[\s:-]*'
    
    # Generic phrases that indicate non-specific text
    GENERIC_PHRASES = [
        'well-maintained', 'single-family residence', 'overall condition',
        'executive summary', 'property summary', 'age-related maintenance',
        'typical for', 'normal wear', 'this property', 'the property',
        'inspection report', 'disclosure statement'
    ]
    
    @staticmethod
    def clean_issue_text(text: str) -> str:
        """
        Clean issue text of common problems
        
        Args:
            text: Raw issue text
        
        Returns:
            Cleaned text
        """
        if not text:
            return ""
        
        text = text.strip()
        
        # Strip severity keywords from start
        text = re.sub(TextValidator.SEVERITY_PATTERN, '', text).strip()
        
        # Remove duplicate words
        text = re.sub(r'\b(\w+)\s+\1\b', r'\1', text, flags=re.IGNORECASE)
        
        # Ensure first letter is lowercase (unless it's a proper noun)
        if text and text[0].isupper() and not text.startswith(('Federal', 'National', 'Pacific')):
            text = text[0].lower() + text[1:]
        
        return text
    
    @staticmethod
    def validate_issue_text(text: str, category: str = None) -> bool:
        """
        Validate issue text is specific and useful
        
        Args:
            text: Issue text to validate
            category: Optional category for category-specific validation
        
        Returns:
            True if valid, False otherwise
        """
        if not text or len(text) < 30:
            return False
        
        text_lower = text.lower()
        
        # Reject generic phrases
        if any(phrase in text_lower for phrase in TextValidator.GENERIC_PHRASES):
            return False
        
        # Reject if starts with generic words
        if text_lower.startswith(('this is', 'the property', 'overall', 'summary', 'executive')):
            return False
        
        # Reject incomplete sentences
        if text.endswith(('requiring.', 'with.', 'and.', 'or.')):
            return False
        
        # Must mention something specific
        specific_indicators = [
            'crack', 'leak', 'damage', 'deteriorat', 'corrosi', 'rot', 'fail',
            'defect', 'issue', 'concern', 'hazard', 'risk', 'problem',
            'replace', 'repair', 'service', 'inspect', 'evaluat'
        ]
        if not any(indicator in text_lower for indicator in specific_indicators):
            return False
        
        return True


class PriceValidator:
    """Validate property prices"""
    
    MIN_PRICE = 1
    MAX_PRICE = 100_000_000  # $100M
    
    @staticmethod
    def validate_price(price: any) -> int:
        """
        Validate and parse property price
        
        Args:
            price: Price as string, int, or float
        
        Returns:
            Validated price as integer
        
        Raises:
            ValidationError: If price is invalid
        """
        try:
            # Handle string or number
            if isinstance(price, str):
                # Remove any non-numeric characters
                price = re.sub(r'[^\d.]', '', price)
            
            # Convert to float then int
            price_value = int(float(price))
            
            # Validate range
            if price_value < PriceValidator.MIN_PRICE:
                raise ValidationError(f"Price ${price_value:,} below minimum ${PriceValidator.MIN_PRICE:,}")
            
            if price_value > PriceValidator.MAX_PRICE:
                raise ValidationError(f"Price ${price_value:,} exceeds maximum ${PriceValidator.MAX_PRICE:,}")
            
            return price_value
            
        except (ValueError, TypeError) as e:
            raise ValidationError(f"Invalid price format: {price}")


class RiskScoreValidator:
    """Validate risk scoring"""
    
    @staticmethod
    def validate_risk_score(score: float) -> float:
        """Ensure risk score is in valid range"""
        if score < 0:
            return 0.0
        if score > 100:
            return 100.0
        return score
    
    @staticmethod
    def validate_risk_tier(tier: str, critical_count: int, major_count: int) -> str:
        """
        Validate risk tier matches issue severity
        
        Args:
            tier: Assigned risk tier
            critical_count: Number of critical issues
            major_count: Number of major issues
        
        Returns:
            Validated (possibly corrected) risk tier
        """
        # Multiple critical issues should always be CRITICAL tier
        if critical_count >= 2 and tier != 'CRITICAL':
            return 'CRITICAL'
        
        # Single critical issue should be at least HIGH
        if critical_count >= 1 and tier not in ['CRITICAL', 'HIGH']:
            return 'HIGH'
        
        # Multiple major issues should be at least HIGH
        if major_count >= 3 and tier not in ['CRITICAL', 'HIGH']:
            return 'HIGH'
        
        return tier


class OfferValidator:
    """Validate offer calculations"""
    
    @staticmethod
    def validate_discount(discount: float, asking_price: float) -> float:
        """
        Validate discount is reasonable
        
        Args:
            discount: Discount amount
            asking_price: Property asking price
        
        Returns:
            Validated discount
        
        Raises:
            ValidationError: If discount is invalid
        """
        if discount < 0:
            raise ValidationError("Negative discount not allowed")
        
        # Warn if discount > 50% of asking price (still possible, but unusual)
        if discount > asking_price * 0.5:
            # This is suspicious but not impossible (major issues)
            pass
        
        # Discount should not exceed asking price
        if discount > asking_price:
            raise ValidationError(f"Discount ${discount:,} exceeds asking price ${asking_price:,}")
        
        return discount
    
    @staticmethod
    def validate_offer(offer: float, asking_price: float, discount: float) -> float:
        """
        Validate recommended offer
        
        Args:
            offer: Recommended offer
            asking_price: Property asking price
            discount: Total discount
        
        Returns:
            Validated offer
        
        Raises:
            ValidationError: If offer is invalid
        """
        if offer < 0:
            raise ValidationError("Negative offer not allowed")
        
        # Offer should equal asking_price - discount (roughly)
        expected_offer = asking_price - discount
        
        # Allow small rounding differences
        if abs(offer - expected_offer) > 1000:
            # Significant mismatch, use calculated value
            offer = expected_offer
        
        return max(0, offer)  # Never negative


# Validation registry for easy access
VALIDATORS = {
    'cost': CostValidator,
    'text': TextValidator,
    'price': PriceValidator,
    'risk': RiskScoreValidator,
    'offer': OfferValidator
}


def validate_analysis_output(analysis: Dict) -> Dict:
    """
    Comprehensive validation of analysis output before sending to user
    
    Args:
        analysis: Raw analysis dictionary
    
    Returns:
        Validated analysis dictionary
    """
    validated = analysis.copy()
    
    try:
        # Validate property price
        if 'property_price' in validated:
            validated['property_price'] = PriceValidator.validate_price(validated['property_price'])
        
        # Validate risk score
        if 'overall_risk_score' in validated:
            validated['overall_risk_score'] = RiskScoreValidator.validate_risk_score(
                validated['overall_risk_score']
            )
        
        # Validate category scores
        if 'category_scores' in validated:
            for cat_score in validated['category_scores']:
                # Validate costs
                if 'estimated_cost_low' in cat_score and 'estimated_cost_high' in cat_score:
                    cat_score['estimated_cost_low'], cat_score['estimated_cost_high'] = \
                        CostValidator.validate_cost_range(
                            cat_score['estimated_cost_low'],
                            cat_score['estimated_cost_high'],
                            cat_score.get('category', ''),
                            cat_score.get('severity', 'moderate')
                        )
        
        # Validate critical issues text
        if 'critical_issues' in validated:
            validated['critical_issues'] = [
                TextValidator.clean_issue_text(issue)
                for issue in validated['critical_issues']
                if TextValidator.validate_issue_text(issue)
            ]
        
        # Validate offer strategy
        if 'offer_strategy' in validated:
            strategy = validated['offer_strategy']
            asking_price = validated.get('property_price', 0)
            
            if 'discount_from_ask' in strategy:
                strategy['discount_from_ask'] = OfferValidator.validate_discount(
                    strategy['discount_from_ask'],
                    asking_price
                )
            
            if 'recommended_offer' in strategy:
                strategy['recommended_offer'] = OfferValidator.validate_offer(
                    strategy['recommended_offer'],
                    asking_price,
                    strategy.get('discount_from_ask', 0)
                )
    
    except ValidationError as e:
        # Log validation error but don't fail the whole analysis
        print(f"Validation warning: {e}")
    
    return validated
