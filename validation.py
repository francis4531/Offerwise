"""
OfferWise Data Validation Module
Comprehensive validation to prevent bad data from reaching users
"""

import re
import logging
from typing import List, Dict, Optional, Tuple
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
    
    # Maximum reasonable costs to detect inflated estimates (Bug #33)
    MAXIMUM_REASONABLE_COSTS = {
        'foundation_structure': 150000,  # Even major foundation work
        'roof_exterior': 75000,          # Even large complex roof
        'electrical': 50000,             # Even full rewire
        'plumbing': 40000,               # Even full repipe
        'hvac_systems': 30000,           # Even high-end system
        'environmental': 100000,         # Extensive remediation
        'legal_title': 50000,            # Even complex title issues
        'insurance_hoa': 25000           # Even high special assessments
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
        
        # CRITICAL: Cap at maximum reasonable costs (Bug #33 - inflated estimates)
        category_lower = category.lower()
        for key, max_cost in CostValidator.MAXIMUM_REASONABLE_COSTS.items():
            if key in category_lower:
                if cost_high > max_cost:
                    # Inspector inflated costs - cap them
                    logging.warning(f"Capping inflated {category} cost from ${cost_high:,} to ${max_cost:,}")
                    cost_high = max_cost
                    cost_low = min(cost_low, max_cost * 0.6)
                break
        
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
    
    # Price thresholds for special handling
    LUXURY_THRESHOLD = 3_000_000  # $3M+
    FIXER_THRESHOLD = 800_000     # <$800K
    
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
        # CRITICAL: Handle None/null values
        if discount is None or str(discount).lower() == 'nan':
            discount = 0
        
        # Convert to float if string
        try:
            discount = float(discount)
        except (ValueError, TypeError):
            discount = 0
        
        if discount < 0:
            raise ValidationError("Negative discount not allowed")
        
        # EDGE CASE: Repair costs > 50% of value = walk away territory
        if discount > asking_price * 0.5:
            # Property is likely not worth buying
            # Cap discount at 50% and flag for user
            discount = asking_price * 0.5
        
        # Discount should not exceed asking price
        if discount > asking_price:
            raise ValidationError(f"Discount ${discount:,} exceeds asking price ${asking_price:,}")
        
        # EDGE CASE: Very expensive properties (Bug #10)
        # Percentage-based discounts can be unrealistic at high prices
        if asking_price > OfferValidator.LUXURY_THRESHOLD:
            # Cap maximum discount at 25% for luxury properties
            max_discount = asking_price * 0.25
            if discount > max_discount:
                discount = max_discount
        
        # EDGE CASE: Very cheap properties
        # Minimum $10K discount if any issues found
        if asking_price < OfferValidator.FIXER_THRESHOLD and 0 < discount < 10000:
            discount = 10000
        
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
        # CRITICAL: Handle None/null values
        if offer is None or str(offer).lower() == 'nan':
            # Calculate from asking price and discount
            offer = asking_price - discount
        
        # Convert to float if string
        try:
            offer = float(offer)
        except (ValueError, TypeError):
            # If can't convert, calculate from scratch
            offer = asking_price - discount
        
        if offer < 0:
            raise ValidationError("Negative offer not allowed")
        
        # Offer should equal asking_price - discount (roughly)
        expected_offer = asking_price - discount
        
        # Allow small rounding differences
        if abs(offer - expected_offer) > 1000:
            # Significant mismatch, use calculated value
            offer = expected_offer
        
        # EDGE CASE: Offer too low (Bug #12)
        # If offer < 50% of asking, recommend walking away
        if offer < asking_price * 0.5:
            # Flag this as a walk-away property
            # But still return the calculated offer
            pass
        
        # EDGE CASE: Offer exceeds asking (bidding war)
        # Only valid if property is in good condition
        if offer > asking_price * 1.05:
            # Cap at 5% over asking
            offer = asking_price * 1.05
        
        return max(0, offer)  # Never negative
    
    @staticmethod
    def should_walk_away(asking_price: float, total_repair_costs: float, risk_score: float) -> bool:
        """
        Determine if buyer should walk away from property
        
        Args:
            asking_price: Property asking price
            total_repair_costs: Estimated repair costs
            risk_score: Overall risk score (0-100)
        
        Returns:
            True if should walk away
        """
        # Walk away if:
        # 1. Repairs > 50% of price
        if total_repair_costs > asking_price * 0.5:
            return True
        
        # 2. High risk (80+) AND repairs > 30% of price
        if risk_score >= 80 and total_repair_costs > asking_price * 0.3:
            return True
        
        # 3. Repairs > asking price (tear-down)
        if total_repair_costs > asking_price:
            return True
        
        return False


# Validation registry for easy access
VALIDATORS = {
    'cost': CostValidator,
    'text': TextValidator,
    'price': PriceValidator,
    'risk': RiskScoreValidator,
    'offer': OfferValidator
}


class CategoryValidator:
    """Validate category scoring and completeness"""
    
    # All categories that should be checked
    REQUIRED_CATEGORIES = [
        'foundation_structure',
        'roof_exterior',
        'electrical',
        'plumbing',
        'hvac_systems',
        'environmental',
        'legal_title',
        'insurance_hoa'
    ]
    
    @staticmethod
    def validate_categories(category_scores: List[Dict]) -> List[Dict]:
        """
        Ensure all categories present, handle empty categories
        
        Args:
            category_scores: List of category score dictionaries
        
        Returns:
            Validated category scores with all categories present
        """
        if not category_scores:
            return []
        
        # Check for empty categories (Bug #9)
        for cat_score in category_scores:
            score = cat_score.get('score', 0)
            
            # If score is 0 or None, ensure proper defaults
            if score == 0 or score is None:
                cat_score['score'] = 0
                cat_score['severity'] = 'none'
                cat_score['estimated_cost_low'] = 0
                cat_score['estimated_cost_high'] = 0
                cat_score['key_issues'] = []
                cat_score['severity_breakdown'] = {
                    'critical': 0,
                    'major': 0,
                    'moderate': 0,
                    'minor': 0
                }
        
        return category_scores
    
    @staticmethod
    def validate_environmental_category(category_scores: List[Dict]) -> bool:
        """
        Check if environmental issues are properly categorized (Bug #14)
        
        Returns:
            True if environmental category exists and is properly scored
        """
        for cat_score in category_scores:
            category = cat_score.get('category', '')
            if 'environmental' in category.lower():
                return True
        return False
    
    @staticmethod
    def validate_legal_category(category_scores: List[Dict]) -> bool:
        """
        Check if legal issues are properly categorized (Bug #15, #24)
        
        Returns:
            True if legal category exists and is properly scored
        """
        for cat_score in category_scores:
            category = cat_score.get('category', '')
            if 'legal' in category.lower() or 'title' in category.lower():
                return True
        return False


class WalkAwayRecommendation:
    """Determine if property should be walked away from"""
    
    @staticmethod
    def should_recommend_walk_away(
        asking_price: float,
        total_repair_costs: float,
        risk_score: float,
        critical_issue_count: int,
        transparency_score: float
    ) -> Tuple[bool, str]:
        """
        Comprehensive walk-away analysis (Bug #12)
        
        Returns:
            (should_walk_away, reason)
        """
        reasons = []
        
        # Repair costs > 50% of value
        if total_repair_costs > asking_price * 0.5:
            reasons.append(f"Repair costs (${total_repair_costs:,.0f}) exceed 50% of asking price")
        
        # Repairs > asking price (tear-down)
        if total_repair_costs > asking_price:
            reasons.append(f"Repair costs (${total_repair_costs:,.0f}) exceed asking price - likely tear-down")
        
        # Multiple critical issues + high repair costs
        if critical_issue_count >= 3 and total_repair_costs > asking_price * 0.3:
            reasons.append(f"{critical_issue_count} critical issues with substantial repair costs")
        
        # Extremely high risk + major costs
        if risk_score >= 85 and total_repair_costs > asking_price * 0.25:
            reasons.append("Extremely high risk combined with significant repair costs")
        
        # Very low transparency + high costs
        if transparency_score < 30 and total_repair_costs > asking_price * 0.3:
            reasons.append("Seller hiding issues + major repair costs = high risk")
        
        if reasons:
            return True, " | ".join(reasons)
        
        return False, ""


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
        # Validate property price - CRITICAL for display
        if 'property_price' in validated:
            validated['property_price'] = PriceValidator.validate_price(validated['property_price'])
        else:
            # CRITICAL: property_price is missing - this should not happen!
            logging.error("property_price missing from analysis result - this is a bug!")
            validated['property_price'] = 0  # Set to 0 so validation doesn't crash
        
        asking_price = validated.get('property_price', 0)
        
        # Validate risk score
        if 'overall_risk_score' in validated:
            validated['overall_risk_score'] = RiskScoreValidator.validate_risk_score(
                validated['overall_risk_score']
            )
        
        # Validate and fix category scores (Bug #9, #14, #15, #24)
        if 'category_scores' in validated:
            validated['category_scores'] = CategoryValidator.validate_categories(
                validated['category_scores']
            )
            
            # Count critical issues
            critical_count = sum(
                1 for cat in validated['category_scores'] 
                if cat.get('score', 0) >= 75
            )
            
            # Calculate total repair costs
            total_repair_costs = sum(
                cat.get('estimated_cost_high', 0)
                for cat in validated['category_scores']
            )
            
            # Validate costs for each category
            for cat_score in validated['category_scores']:
                if 'estimated_cost_low' in cat_score and 'estimated_cost_high' in cat_score:
                    cat_score['estimated_cost_low'], cat_score['estimated_cost_high'] = \
                        CostValidator.validate_cost_range(
                            cat_score['estimated_cost_low'],
                            cat_score['estimated_cost_high'],
                            cat_score.get('category', ''),
                            cat_score.get('severity', 'moderate')
                        )
        else:
            critical_count = 0
            total_repair_costs = 0
        
        # Validate critical issues text (Bug #11 - handle empty state)
        if 'critical_issues' in validated:
            if validated['critical_issues']:
                validated['critical_issues'] = [
                    TextValidator.clean_issue_text(issue)
                    for issue in validated['critical_issues']
                    if TextValidator.validate_issue_text(issue)
                ]
            else:
                # Empty state - property in good condition
                validated['critical_issues'] = []
        
        # Validate offer strategy
        if 'offer_strategy' in validated:
            strategy = validated['offer_strategy']
            
            # Validate discount (Bug #10, #12)
            if 'discount_from_ask' in strategy:
                try:
                    strategy['discount_from_ask'] = OfferValidator.validate_discount(
                        strategy['discount_from_ask'],
                        asking_price
                    )
                except (ValidationError, ValueError, TypeError) as e:
                    logging.warning(f"Discount validation error: {e}")
                    # Set to 0 if validation fails
                    strategy['discount_from_ask'] = 0
            
            # Validate offer (Bug #10, #12)
            if 'recommended_offer' in strategy:
                try:
                    strategy['recommended_offer'] = OfferValidator.validate_offer(
                        strategy['recommended_offer'],
                        asking_price,
                        strategy.get('discount_from_ask', 0)
                    )
                except (ValidationError, ValueError, TypeError) as e:
                    logging.warning(f"Offer validation error: {e}")
                    # Calculate from asking price and discount
                    discount = strategy.get('discount_from_ask', 0)
                    strategy['recommended_offer'] = max(0, asking_price - discount)
            
            # If recommended_offer is STILL None/invalid, force calculation
            if strategy.get('recommended_offer') is None or str(strategy.get('recommended_offer', '')).lower() == 'nan':
                discount = strategy.get('discount_from_ask', 0) or 0
                strategy['recommended_offer'] = max(0, asking_price - discount)
                logging.warning(f"Forced offer calculation: ${strategy['recommended_offer']:,}")
            
            # Check if should walk away (Bug #12)
            risk_score = validated.get('overall_risk_score', 50)
            transparency_score = validated.get('cross_reference', {}).get('transparency_score', 80)
            
            try:
                should_walk, reason = WalkAwayRecommendation.should_recommend_walk_away(
                    asking_price,
                    total_repair_costs,
                    risk_score,
                    critical_count,
                    transparency_score
                )
                
                # Add walk-away recommendation to strategy
                strategy['walk_away_recommended'] = should_walk
                if should_walk:
                    strategy['walk_away_reason'] = reason
                    # Also add warning to critical issues
                    if 'critical_issues' not in validated:
                        validated['critical_issues'] = []
                    validated['critical_issues'].insert(0,
                        f"⚠️ RECOMMENDATION: Consider walking away from this property. {reason}"
                    )
            except Exception as e:
                logging.warning(f"Walk-away recommendation error: {e}")
                # Continue without walk-away recommendation
                strategy['walk_away_recommended'] = False
        
        # Validate risk tier (Bug #11 - perfect properties)
        # Only change to LOW if there's actually NO risk premium calculated
        if critical_count == 0 and validated.get('overall_risk_score', 0) < 30:
            # Check if there's a significant risk premium already calculated
            risk_premium = 0
            if 'offer_strategy' in validated and 'discount_breakdown' in validated['offer_strategy']:
                risk_premium = validated['offer_strategy']['discount_breakdown'].get('risk_premium', 0)
            
            # Only set to LOW if risk premium is also 0 or very small
            if risk_premium < 1000:  # Less than $1,000 risk premium
                validated['risk_tier'] = 'LOW'
            # Otherwise keep the original risk_tier that justified the premium
        
    except ValidationError as e:
        # Log validation error but don't fail the whole analysis
        print(f"Validation warning: {e}")
    
    return validated
