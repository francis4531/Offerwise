"""
Confidence Scoring System - Transparency for Users
Tells users how much to trust each analysis
"""

import logging
from typing import Dict, List, Any

class ConfidenceScorer:
    """
    Calculate confidence score for each analysis
    
    Users need to know:
    - How confident are we in this analysis?
    - What factors reduce confidence?
    - Should they get professional review?
    """
    
    # Thresholds
    CONFIDENCE_LEVELS = {
        'VERY_HIGH': (90, 100),   # 90-100%
        'HIGH': (80, 89),         # 80-89%
        'MEDIUM': (70, 79),       # 70-79%
        'LOW': (60, 69),          # 60-69%
        'VERY_LOW': (0, 59)       # <60%
    }
    
    def calculate(self, analysis: Dict[Any, Any], input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate confidence score
        
        Args:
            analysis: Complete analysis result
            input_data: Original inputs (inspection, disclosure, etc.)
        
        Returns:
            Confidence dict with score, level, breakdown
        """
        
        factors = []
        
        # Factor 1: Input Data Quality (35%)
        data_score = self._score_input_quality(input_data)
        factors.append(('input_quality', data_score, 0.35))
        
        # Factor 2: Extraction Quality (25%)
        extraction_score = self._score_extraction(analysis)
        factors.append(('extraction_quality', extraction_score, 0.25))
        
        # Factor 3: Cost Estimation Quality (20%)
        cost_score = self._score_cost_estimates(analysis)
        factors.append(('cost_estimates', cost_score, 0.20))
        
        # Factor 4: Edge Case Flags (15%)
        edge_score = self._score_edge_cases(analysis)
        factors.append(('edge_cases', edge_score, 0.15))
        
        # Factor 5: Internal Consistency (5%)
        consistency_score = self._score_consistency(analysis)
        factors.append(('consistency', consistency_score, 0.05))
        
        # Calculate weighted total
        total_confidence = sum(score * weight for _, score, weight in factors)
        
        # Determine level and message
        level = self._get_confidence_level(total_confidence)
        message = self._get_confidence_message(level, total_confidence)
        
        # Build result
        confidence_result = {
            'score': round(total_confidence, 1),
            'level': level,
            'message': message,
            'breakdown': {
                name: {
                    'score': round(score, 1),
                    'weight': f"{weight*100:.0f}%",
                    'contribution': round(score * weight, 1)
                }
                for name, score, weight in factors
            },
            'recommendations': self._get_recommendations(level, factors)
        }
        
        logging.info(f"Confidence score: {total_confidence:.1f}% ({level})")
        
        return confidence_result
    
    def _score_input_quality(self, input_data: Dict[str, Any]) -> float:
        """
        Score quality of input documents (0-100)
        
        Better data = higher confidence
        """
        inspection_text = input_data.get('inspection', '')
        disclosure_text = input_data.get('disclosure', '')
        
        score = 100.0
        
        # Inspection length
        inspection_len = len(inspection_text)
        if inspection_len < 300:
            score -= 40  # Very short
        elif inspection_len < 700:
            score -= 25  # Short
        elif inspection_len < 1500:
            score -= 10  # Adequate
        else:
            score += 5   # Detailed (bonus)
        
        # Disclosure length
        disclosure_len = len(disclosure_text)
        if disclosure_len < 100:
            score -= 30  # Very short
        elif disclosure_len < 300:
            score -= 15  # Short
        elif disclosure_len < 700:
            score -= 5   # Adequate
        
        # Check for missing data indicators
        combined_text = (inspection_text + ' ' + disclosure_text).lower()
        if any(phrase in combined_text for phrase in [
            'unable to inspect', 'access limited', 'could not access',
            'not available', 'no disclosure provided'
        ]):
            score -= 20
        
        return max(0, min(100, score))
    
    def _score_extraction(self, analysis: Dict[Any, Any]) -> float:
        """
        Score quality of data extraction (0-100)
        
        More findings extracted = better
        """
        score = 100.0
        
        # Check findings count
        findings_count = 0
        if 'risk_breakdown' in analysis:
            for category in analysis['risk_breakdown'].get('category_scores', []):
                findings_count += category.get('finding_count', 0)
        
        if findings_count == 0:
            score = 20  # No findings extracted
        elif findings_count < 3:
            score = 50  # Very few findings
        elif findings_count < 6:
            score = 75  # Some findings
        elif findings_count < 10:
            score = 90  # Good number of findings
        else:
            score = 100  # Comprehensive extraction
        
        # Check category coverage
        categories_with_scores = 0
        if 'risk_breakdown' in analysis:
            for category in analysis['risk_breakdown'].get('category_scores', []):
                if category.get('score', 0) > 0:
                    categories_with_scores += 1
        
        # Bonus for multiple categories
        if categories_with_scores >= 5:
            score += 5
        elif categories_with_scores < 2:
            score -= 10
        
        return max(0, min(100, score))
    
    def _score_cost_estimates(self, analysis: Dict[Any, Any]) -> float:
        """
        Score quality of cost estimates (0-100)
        
        More cost data = higher confidence
        """
        score = 100.0
        
        # Count findings with costs
        findings_with_costs = 0
        findings_without_costs = 0
        
        if 'risk_breakdown' in analysis:
            for category in analysis['risk_breakdown'].get('category_scores', []):
                cost_low = category.get('estimated_cost_low', 0)
                cost_high = category.get('estimated_cost_high', 0)
                
                if cost_low > 0 or cost_high > 0:
                    findings_with_costs += 1
                elif category.get('score', 0) > 0:
                    findings_without_costs += 1
        
        # Penalty for missing costs
        if findings_without_costs > 0:
            total_findings = findings_with_costs + findings_without_costs
            missing_percentage = findings_without_costs / total_findings
            score -= missing_percentage * 30
        
        # Check for unrealistic costs (already capped by validation, but check)
        if 'risk_breakdown' in analysis:
            total_repairs = sum(
                cat.get('estimated_cost_high', 0)
                for cat in analysis['risk_breakdown'].get('category_scores', [])
            )
            asking_price = analysis.get('property_price', 0)
            
            if asking_price > 0 and total_repairs > asking_price * 0.8:
                # Repairs over 80% of price - might be estimation error
                score -= 15
        
        return max(0, min(100, score))
    
    def _score_edge_cases(self, analysis: Dict[Any, Any]) -> float:
        """
        Score based on edge case flags (0-100)
        
        More edge cases = lower confidence
        """
        score = 100.0
        
        critical_issues = analysis.get('critical_issues', [])
        
        # Count warning flags
        warning_count = sum(1 for issue in critical_issues if '⚠️' in str(issue))
        
        # Penalties for specific edge cases
        combined_text = ' '.join(str(issue) for issue in critical_issues).upper()
        
        if 'FORECLOSURE' in combined_text or 'BANK-OWNED' in combined_text:
            score -= 15
        
        if 'LIMITED INSPECTION' in combined_text or 'UNABLE TO INSPECT' in combined_text:
            score -= 20
        
        if 'HOARDER' in combined_text:
            score -= 25
        
        if 'FIRE DAMAGE' in combined_text or 'WATER DAMAGE' in combined_text:
            score -= 10
        
        if 'UNPERMITTED' in combined_text:
            score -= 5
        
        # General warning penalty
        score -= (warning_count * 3)
        
        return max(0, min(100, score))
    
    def _score_consistency(self, analysis: Dict[Any, Any]) -> float:
        """
        Score internal consistency (0-100)
        
        Checks if analysis makes sense
        """
        score = 100.0
        
        risk_score = analysis.get('overall_risk_score', 50)
        risk_tier = analysis.get('risk_tier', 'MODERATE')
        
        # Check tier matches score
        tier_score_map = {
            'CRITICAL': (80, 100),
            'HIGH': (60, 85),
            'MODERATE': (35, 65),
            'LOW': (0, 40)
        }
        
        expected_range = tier_score_map.get(risk_tier, (0, 100))
        if not (expected_range[0] <= risk_score <= expected_range[1]):
            score -= 20  # Inconsistency
        
        # Check offer makes sense
        asking_price = analysis.get('property_price', 0)
        offer = analysis.get('offer_strategy', {}).get('recommended_offer', 0)
        
        if asking_price > 0:
            discount_pct = (asking_price - offer) / asking_price * 100
            
            # Walk-away but small discount = inconsistent
            if analysis.get('offer_strategy', {}).get('walk_away_recommended') and discount_pct < 15:
                score -= 15
            
            # Low risk but big discount = inconsistent
            if risk_tier == 'LOW' and discount_pct > 15:
                score -= 10
        
        return max(0, min(100, score))
    
    def _get_confidence_level(self, score: float) -> str:
        """Get confidence level from score"""
        for level, (min_score, max_score) in self.CONFIDENCE_LEVELS.items():
            if min_score <= score <= max_score:
                return level
        return 'MEDIUM'
    
    def _get_confidence_message(self, level: str, score: float) -> str:
        """Get user-friendly message"""
        messages = {
            'VERY_HIGH': f"Very high confidence ({score:.0f}%) - Analysis is reliable",
            'HIGH': f"High confidence ({score:.0f}%) - Analysis is trustworthy",
            'MEDIUM': f"Moderate confidence ({score:.0f}%) - Review carefully",
            'LOW': f"Lower confidence ({score:.0f}%) - Professional review recommended",
            'VERY_LOW': f"Low confidence ({score:.0f}%) - Professional review required"
        }
        return messages.get(level, f"Confidence: {score:.0f}%")
    
    def _get_recommendations(self, level: str, factors: List) -> List[str]:
        """Get recommendations based on confidence level"""
        recommendations = []
        
        if level in ['LOW', 'VERY_LOW']:
            recommendations.append("Consider getting a professional appraisal")
            recommendations.append("Consult with a real estate agent")
        
        if level == 'VERY_LOW':
            recommendations.append("This property requires expert review before proceeding")
        
        # Check specific factors
        for name, score, weight in factors:
            if score < 60:
                if name == 'input_quality':
                    recommendations.append("Input documents are limited - more detailed reports would improve confidence")
                elif name == 'extraction_quality':
                    recommendations.append("Limited information extracted - manual document review recommended")
                elif name == 'cost_estimates':
                    recommendations.append("Get contractor quotes for more accurate repair cost estimates")
                elif name == 'edge_cases':
                    recommendations.append("Property has special circumstances requiring expert evaluation")
        
        return recommendations
    
    def format_for_display(self, confidence: Dict[str, Any]) -> str:
        """Format confidence info for user display"""
        
        level = confidence['level']
        score = confidence['score']
        
        # Icon and color
        icon_map = {
            'VERY_HIGH': '✅',
            'HIGH': '✓',
            'MEDIUM': '⚠️',
            'LOW': '⚠️',
            'VERY_LOW': '🚨'
        }
        
        icon = icon_map.get(level, '○')
        
        # Format output
        output = f"\n{'='*60}\n"
        output += f"{icon} CONFIDENCE SCORE: {score:.1f}% ({level})\n"
        output += f"{'='*60}\n\n"
        output += f"{confidence['message']}\n\n"
        
        # Breakdown
        output += "Confidence Breakdown:\n"
        for name, details in confidence['breakdown'].items():
            name_display = name.replace('_', ' ').title()
            output += f"  • {name_display}: {details['score']:.0f}% (weight: {details['weight']})\n"
        
        # Recommendations
        if confidence.get('recommendations'):
            output += "\nRecommendations:\n"
            for rec in confidence['recommendations']:
                output += f"  • {rec}\n"
        
        output += f"\n{'='*60}\n"
        
        return output


# Example usage
if __name__ == '__main__':
    # Test confidence scorer
    scorer = ConfidenceScorer()
    
    # Example analysis
    test_analysis = {
        'property_price': 950000,
        'overall_risk_score': 72,
        'risk_tier': 'MODERATE',
        'offer_strategy': {
            'recommended_offer': 845000,
            'walk_away_recommended': False
        },
        'risk_breakdown': {
            'category_scores': [
                {'category': 'foundation', 'score': 65, 'estimated_cost_low': 15000, 'estimated_cost_high': 30000, 'finding_count': 2},
                {'category': 'roof', 'score': 80, 'estimated_cost_low': 18000, 'estimated_cost_high': 32000, 'finding_count': 1},
                {'category': 'electrical', 'score': 45, 'estimated_cost_low': 8000, 'estimated_cost_high': 15000, 'finding_count': 2},
            ]
        },
        'critical_issues': [
            "Foundation shows settlement cracks",
            "Roof past useful life"
        ]
    }
    
    test_input = {
        'inspection': "This is a detailed inspection report that covers all major systems and structural components of the property. Foundation shows some settlement cracks that should be monitored. Roof is original and approaching end of life. Electrical panel is older but functional." * 3,
        'disclosure': "Seller aware of roof age and foundation cracks. No other known issues. Property maintained regularly."
    }
    
    # Calculate confidence
    confidence = scorer.calculate(test_analysis, test_input)
    
    # Display
    print(scorer.format_for_display(confidence))
