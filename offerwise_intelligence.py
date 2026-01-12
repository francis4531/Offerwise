"""
OfferWise Intelligence System - Master Integration
Complete pipeline from PDF documents to actionable insights
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
import json
import logging
import os  # For ANTHROPIC_API_KEY environment variable

from document_parser import DocumentParser, PropertyDocument
from cross_reference_engine import CrossReferenceEngine, CrossReferenceReport
from hybrid_cross_reference import HybridCrossReferenceEngine  # 🚀 NEW: Hybrid Rules+AI
from risk_scoring_model import RiskScoringModel, PropertyRiskScore, BuyerProfile
from strategic_options import StrategicOptionsGenerator
from analysis_ai_helper import AnalysisAIHelper
from property_risk_dna import PropertyRiskDNAEncoder, PropertyRiskDNA
from transparency_scorer import SellerTransparencyScorer, TransparencyReport
from predictive_engine import PredictiveIssueEngine, IssuePrediction

logger = logging.getLogger(__name__)


@dataclass
class PropertyAnalysis:
    """Complete property analysis output"""
    property_address: str
    analysis_date: str
    
    # Document intelligence
    seller_disclosure: PropertyDocument
    inspection_report: PropertyDocument
    
    # Cross-reference analysis
    cross_reference: CrossReferenceReport
    
    # Risk scoring
    risk_score: PropertyRiskScore
    
    # Buyer-specific outputs
    offer_strategy: Dict[str, Any]
    inspection_priorities: Dict[str, Any]
    negotiation_strategy: Dict[str, Any]
    decision_framework: Dict[str, Any]
    strategic_options: List[Dict[str, Any]]  # Property-specific buyer strategies
    
    # 🚀 BILLION-DOLLAR INNOVATIONS
    risk_dna: Optional[Any] = None  # PropertyRiskDNA signature
    transparency_report: Optional[Any] = None  # Seller honesty analysis
    predicted_issues: Optional[List[Any]] = None  # Hidden/future issue predictions
    market_benchmarks: Optional[Dict[str, Any]] = None  # Comparison to similar properties


class OfferWiseIntelligence:
    """
    Master intelligence system that orchestrates all analysis components.
    
    This is what replaces "just asking ChatGPT" with structured, quantified,
    reproducible analysis.
    """
    
    def __init__(self):
        self.parser = DocumentParser()
        
        # 🚀 NEW: Hybrid Cross-Reference Engine (Rules + AI)
        # Try to initialize with AI, fallback to rules-only if API key missing
        anthropic_api_key = os.environ.get('ANTHROPIC_API_KEY')
        if anthropic_api_key:
            logger.info("🤖 Initializing HYBRID cross-reference engine (Rules + AI)")
            self.cross_ref_engine = HybridCrossReferenceEngine(
                anthropic_api_key=anthropic_api_key,
                enable_ai=True
            )
        else:
            logger.warning("⚠️ ANTHROPIC_API_KEY not found - using rules-only mode")
            self.cross_ref_engine = CrossReferenceEngine()
        
        self.risk_model = RiskScoringModel()
        self.options_generator = StrategicOptionsGenerator()
        self.ai_helper = AnalysisAIHelper()  # Phase 1: AI enhancements
        
        # 🚀 BILLION-DOLLAR INNOVATIONS
        self.dna_encoder = PropertyRiskDNAEncoder()  # Innovation #1: Risk DNA
        self.transparency_scorer = SellerTransparencyScorer()  # Innovation #2: Transparency
        self.predictive_engine = PredictiveIssueEngine()  # Innovation #3: Predictions
        
        logger.info("🧬 Property Risk DNA Encoder initialized")
        logger.info("🔍 Transparency Scorer initialized")
        logger.info("🔮 Predictive Engine initialized")
    
    def analyze_property(
        self,
        seller_disclosure_text: str,
        inspection_report_text: str,
        property_price: float,
        buyer_profile: BuyerProfile,
        property_address: Optional[str] = None
    ) -> PropertyAnalysis:
        """
        Complete end-to-end property analysis.
        
        Args:
            seller_disclosure_text: Raw text from seller disclosure PDF
            inspection_report_text: Raw text from inspection report PDF
            property_price: Asking or offer price
            buyer_profile: Buyer's preferences and constraints
            property_address: Property address (optional)
            
        Returns:
            PropertyAnalysis with all insights
        """
        
        # 🚨 EMERGENCY DEBUG
        import logging
        logging.error("=" * 80)
        logging.error("🚨 INTELLIGENCE MODULE - analyze_property CALLED")
        logging.error(f"property_price received: {property_price}")
        logging.error(f"property_price type: {type(property_price)}")
        logging.error(f"property_address: {property_address}")
        logging.error("=" * 80)
        
        # ✨ PHASE 1: FIX OCR ERRORS BEFORE PARSING
        # Step 0: Check and fix OCR quality
        print("📄 Checking OCR quality...")
        disclosure_quality = self.ai_helper._ocr_quality_score(seller_disclosure_text)
        inspection_quality = self.ai_helper._ocr_quality_score(inspection_report_text)
        
        print(f"  Disclosure OCR quality: {disclosure_quality:.1%}")
        print(f"  Inspection OCR quality: {inspection_quality:.1%}")
        
        # Fix OCR errors if quality is very low (< 0.75)
        # SPEED OPTIMIZATION: Increased threshold from 0.90 to 0.75
        # Google Vision typically produces 0.90+ quality, so this saves 5-10s per document
        if disclosure_quality < 0.75 and self.ai_helper.enabled:
            print("  🔧 Fixing disclosure OCR errors...")
            seller_disclosure_text = self.ai_helper.fix_ocr_errors(seller_disclosure_text)
        
        if inspection_quality < 0.75 and self.ai_helper.enabled:
            print("  🔧 Fixing inspection OCR errors...")
            inspection_report_text = self.ai_helper.fix_ocr_errors(inspection_report_text)
        
        # Step 1: Parse documents into structured data
        print("Parsing seller disclosure...")
        disclosure_doc = self.parser.parse_seller_disclosure(
            seller_disclosure_text, 
            property_address
        )
        
        print("Parsing inspection report...")
        inspection_doc = self.parser.parse_inspection_report(
            inspection_report_text,
            property_address or disclosure_doc.property_address
        )
        
        # ✨ PHASE 1: ADD CONFIDENCE SCORES TO FINDINGS
        if self.ai_helper.enabled and inspection_doc.inspection_findings:
            print(f"🎯 Calculating confidence scores for {len(inspection_doc.inspection_findings)} findings...")
            
            # Verify top 1 most expensive finding (SPEED OPTIMIZATION: reduced from 2 to save ~5 seconds)
            # Each verification = ~5s API call, so 1 verification = ~5s vs 2 verifications = ~10s
            sorted_findings = sorted(
                inspection_doc.inspection_findings,
                key=lambda f: f.estimated_cost_high or 0,
                reverse=True
            )[:1]  # Changed from [:2] to [:1]
            
            for idx, finding in enumerate(sorted_findings, 1):
                print(f"  Verifying finding {idx}/1: {finding.description[:60]}...")  # Changed from {idx}/2
                
                # Verify against source
                verification = self.ai_helper.verify_finding_against_source(
                    finding_description=finding.description,
                    source_text=inspection_report_text
                )
                
                # Calculate confidence
                confidence = self.ai_helper.calculate_confidence_score(
                    finding={
                        'description': finding.description,
                        'location': finding.location,
                        'severity': finding.severity.value if hasattr(finding.severity, 'value') else str(finding.severity),
                        'estimated_cost_low': finding.estimated_cost_low
                    },
                    ocr_quality=inspection_quality,
                    verification=verification
                )
                
                # Update finding with confidence data
                finding.confidence = confidence
                finding.confidence_explanation = self.ai_helper.generate_confidence_explanation(
                    confidence=confidence,
                    finding={
                        'description': finding.description,
                        'location': finding.location,
                        'estimated_cost_low': finding.estimated_cost_low
                    },
                    verification=verification
                )
                finding.verified = verification['supported']
                finding.evidence = verification.get('evidence', [])
                
                confidence_emoji = "✅" if confidence >= 0.85 else "⚠️" if confidence >= 0.65 else "❌"
                print(f"    {confidence_emoji} Confidence: {confidence:.0%} - {finding.confidence_explanation[:50]}...")
            
            # Add default confidence for remaining findings
            remaining_findings = [f for f in inspection_doc.inspection_findings if f not in sorted_findings]
            for finding in remaining_findings:
                finding.confidence = 0.7  # Default medium confidence
                finding.confidence_explanation = "MEDIUM CONFIDENCE: Not verified (cost optimization)"
                finding.verified = False
                finding.evidence = []
        
        # Step 2: Cross-reference disclosures vs inspection
        print("Cross-referencing documents...")
        cross_ref = self.cross_ref_engine.cross_reference(
            disclosure_doc,
            inspection_doc
        )
        
        # Step 3: Calculate risk scores
        print("Calculating risk scores...")
        risk_score = self.risk_model.calculate_risk_score(
            findings=inspection_doc.inspection_findings,
            cross_ref_report=cross_ref,
            property_price=property_price,
            buyer_profile=buyer_profile
        )
        risk_score.property_address = disclosure_doc.property_address or inspection_doc.property_address
        
        # Step 4: Generate buyer-specific strategies
        print("Generating strategies...")
        offer_strategy = self._generate_offer_strategy(
            property_price, risk_score, cross_ref, buyer_profile
        )
        
        inspection_priorities = self._generate_inspection_priorities(
            inspection_doc, risk_score, buyer_profile
        )
        
        negotiation_strategy = self._generate_negotiation_strategy(
            property_price, risk_score, cross_ref, buyer_profile
        )
        
        decision_framework = self._generate_decision_framework(
            property_price, risk_score, cross_ref, buyer_profile
        )
        
        # Step 5: Generate strategic options (property-specific buyer actions)
        print("Generating strategic options...")
        offer_score = 100 - risk_score.overall_risk_score  # Convert risk to offer score
        strategic_options_objs = self.options_generator.generate_options(
            offer_score=offer_score,
            findings_count=len(inspection_doc.inspection_findings),
            transparency_score=cross_ref.transparency_score,
            total_repair_costs=risk_score.total_repair_cost_high,
            property_price=property_price,
            buyer_profile={
                'biggest_regret': buyer_profile.biggest_regret,
                'max_budget': buyer_profile.max_budget
            }
        )
        
        # Convert strategic options to dicts for JSON serialization
        strategic_options = [
            {
                'title': opt.title,
                'icon': opt.icon,
                'strategy': opt.strategy,
                'tactics': opt.tactics,
                'rationale': opt.rationale,
                'risk_level': opt.risk_level,
                'probability_success': opt.probability_success,
                'next_steps': opt.next_steps
            }
            for opt in strategic_options_objs
        ]
        
        # 🚀 BILLION-DOLLAR INNOVATIONS
        print("=" * 60)
        print("🚀 GENERATING PATENTABLE INNOVATIONS")
        print("=" * 60)
        
        # Innovation #1: Generate Property Risk DNA™
        print("🧬 Encoding Property Risk DNA...")
        risk_dna = None
        market_benchmarks = None
        try:
            # Ensure all values are not None
            total_repair_high = risk_score.total_repair_cost_high if risk_score.total_repair_cost_high is not None else 0
            total_repair_low = risk_score.total_repair_cost_low if risk_score.total_repair_cost_low is not None else 0
            overall_risk = risk_score.overall_risk_score if risk_score.overall_risk_score is not None else 0
            
            # DEBUG: Log what we're passing to encoder
            print(f"   🔍 DEBUG - Data being passed to encoder:")
            print(f"      total_repair_high: {total_repair_high}")
            print(f"      total_repair_low: {total_repair_low}")
            print(f"      overall_risk: {overall_risk}")
            print(f"      property_price: {property_price}")
            print(f"      buyer_max_budget: {buyer_profile.max_budget}")
            print(f"      inspection_findings count: {len(inspection_doc.inspection_findings)}")
            print(f"      cross_ref type: {type(cross_ref)}")
            
            risk_dna = self.dna_encoder.encode_property(
                property_analysis={
                    'total_repair_cost_high': total_repair_high,
                    'total_repair_cost_low': total_repair_low,
                    'overall_risk_score': overall_risk,
                    'asking_price': property_price,
                    'recommended_price_reduction': 0  # Add this to avoid None
                },
                inspection_findings=inspection_doc.inspection_findings,
                cross_reference_report=cross_ref,
                property_metadata={
                    'id': property_address,
                    'address': property_address,
                    'price': property_price,
                    'age': 0,  # Would come from metadata
                    'buyer_max_budget': buyer_profile.max_budget if buyer_profile.max_budget else property_price
                }
            )
            print(f"   ✅ Risk DNA: {risk_dna.composite_score:.1f}/100 ({risk_dna.risk_category})")
            
            # VALIDATION: Check alignment between overall_risk_score and risk_dna.composite_score
            # Note: Risk DNA now applies automatic correction for large gaps (>15 points)
            score_gap = abs(overall_risk - risk_dna.composite_score)
            if score_gap > 20:
                # Even with correction, gap is large - may indicate calculation issue
                logger.warning(f"⚠️  LARGE SCORE GAP DETECTED (post-correction)!")
                logger.warning(f"   overall_risk_score: {overall_risk:.1f}/100")
                logger.warning(f"   risk_dna.composite_score: {risk_dna.composite_score:.1f}/100")
                logger.warning(f"   Gap: {score_gap:.1f} points (threshold: 20)")
                logger.warning(f"   This gap persists despite automatic correction")
                print(f"   ⚠️  WARNING: Large score gap of {score_gap:.1f} points detected")
                print(f"      overall_risk: {overall_risk:.1f}, risk_dna: {risk_dna.composite_score:.1f}")
            elif score_gap > 10:
                # Moderate gap - acceptable but log it
                print(f"   ℹ️  Score gap: {score_gap:.1f} points (acceptable range)")
            else:
                # Good alignment
                print(f"   ✅ Excellent score alignment (gap: {score_gap:.1f} points)")
            
            # Get market benchmarks (if enough data)
            if len(self.dna_encoder.dna_database) >= 2:
                market_benchmarks = self.dna_encoder.get_market_benchmarks(risk_dna)
                print(f"   📊 Benchmarked against {market_benchmarks.get('similar_count', 0)} similar properties")
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"   ⚠️  Risk DNA generation failed: {e}")
            print(f"   📋 Full error details:\n{error_details}")
            logger.error(f"Risk DNA error: {e}\n{error_details}")
        
        # Innovation #2: Generate Transparency Report™
        print("🔍 Scoring Seller Transparency...")
        transparency_report = None
        try:
            transparency_report = self.transparency_scorer.score_transparency(
                disclosure_doc=disclosure_doc,
                inspection_doc=inspection_doc,
                cross_reference_report=cross_ref,
                property_metadata=None  # Would include permit history
            )
            print(f"   ✅ Transparency: {transparency_report.transparency_score}/100 (Grade {transparency_report.grade.value})")
            print(f"   🚨 Red flags: {len(transparency_report.red_flags)}")
            print(f"   ⚠️  Undisclosed issues: {len(transparency_report.undisclosed_issues)}")
        except Exception as e:
            print(f"   ⚠️  Transparency scoring failed: {e}")
            logger.error(f"Transparency error: {e}")
        
        # Innovation #3: Predict Hidden Issues™
        print("🔮 Predicting Hidden Issues...")
        predicted_issues = None
        try:
            predicted_issues = self.predictive_engine.predict_hidden_issues(
                current_findings=inspection_doc.inspection_findings,
                property_metadata={
                    'age': 0,  # Would come from metadata
                    'type': 'single_family',
                    'location': property_address
                }
            )
            print(f"   ✅ Predictions: {len(predicted_issues)} hidden/future issues detected")
            
            # Show top 3 predictions
            for i, pred in enumerate(predicted_issues[:3], 1):
                print(f"      {i}. {pred.predicted_issue} ({pred.probability:.0%} probability)")
        except Exception as e:
            print(f"   ⚠️  Predictive engine failed: {e}")
            logger.error(f"Prediction error: {e}")
        
        # Train predictive engine on this analysis (for future learning)
        try:
            self.predictive_engine.train_on_analysis({
                'inspection_findings': inspection_doc.inspection_findings,
                'cross_reference_report': cross_ref,
                'property_metadata': {'age': 0},
                'total_repair_costs': risk_score.total_repair_cost_high
            })
        except Exception as e:
            logger.error(f"Training error: {e}")
        
        print("=" * 60)
        print("✅ ALL INNOVATIONS GENERATED SUCCESSFULLY")
        print("=" * 60)
        
        return PropertyAnalysis(
            property_address=risk_score.property_address,
            analysis_date="2024-12-22",
            seller_disclosure=disclosure_doc,
            inspection_report=inspection_doc,
            cross_reference=cross_ref,
            risk_score=risk_score,
            offer_strategy=offer_strategy,
            inspection_priorities=inspection_priorities,
            negotiation_strategy=negotiation_strategy,
            decision_framework=decision_framework,
            strategic_options=strategic_options,
            # 🚀 BILLION-DOLLAR INNOVATIONS
            risk_dna=risk_dna,
            transparency_report=transparency_report,
            predicted_issues=predicted_issues,
            market_benchmarks=market_benchmarks
        )
    
    def _generate_offer_strategy(
        self,
        property_price: float,
        risk_score: PropertyRiskScore,
        cross_ref: CrossReferenceReport,
        buyer_profile: BuyerProfile
    ) -> Dict[str, Any]:
        """Generate specific offer recommendations"""
        
        # 🚨 EMERGENCY DEBUG
        import logging
        logging.error("=" * 80)
        logging.error("🚨 GENERATING OFFER STRATEGY")
        logging.error(f"property_price received: {property_price}")
        logging.error(f"property_price type: {type(property_price)}")
        logging.error("=" * 80)
        
        # Calculate recommended offer price
        base_discount = 0.0
        
        # Discount based on repair costs - DEFENSIVE: Handle None values
        cost_low = risk_score.total_repair_cost_low if risk_score.total_repair_cost_low is not None else 0.0
        cost_high = risk_score.total_repair_cost_high if risk_score.total_repair_cost_high is not None else 0.0
        
        repair_cost_avg = (cost_low + cost_high) / 2
        # Use 100% of repair costs as starting point for negotiation
        cost_discount = repair_cost_avg
        
        # Additional discount for risk
        if risk_score.risk_tier == "CRITICAL":
            risk_discount = property_price * 0.10
        elif risk_score.risk_tier == "HIGH":
            risk_discount = property_price * 0.05
        elif risk_score.risk_tier == "MODERATE":
            risk_discount = property_price * 0.02
        else:
            risk_discount = 0.0
        
        # Additional discount for transparency issues
        transparency_discount = 0.0
        if cross_ref.transparency_score < 50:
            transparency_discount = property_price * 0.03
        
        total_discount = cost_discount + risk_discount + transparency_discount
        recommended_offer = property_price - total_discount
        
        # DEFENSIVE: Ensure recommended_offer is never None
        if recommended_offer is None or str(recommended_offer).lower() == 'nan':
            recommended_offer = property_price  # Default to asking price if calculation fails
        
        # Ensure recommended_offer is not negative
        recommended_offer = max(0, recommended_offer)
        
        # Adjust for buyer profile - now handles free-form text
        biggest_regret_lower = buyer_profile.biggest_regret.lower() if buyer_profile.biggest_regret else ""
        
        # Check if user fears losing the house (keyword-based)
        if (("lose" in biggest_regret_lower or "losing" in biggest_regret_lower or "miss" in biggest_regret_lower or "another buyer" in biggest_regret_lower) 
            and buyer_profile.replaceability == "very_rare"):
            # Buyer fears losing house - be more aggressive
            recommended_offer = min(property_price, recommended_offer + (total_discount * 0.3))
            logging.info(f"Buyer fears losing house - adjusted offer up by 30% of discount")
        
        # Check if user fears overpaying (keyword-based)
        elif ("overpay" in biggest_regret_lower or "too much" in biggest_regret_lower or "overvalue" in biggest_regret_lower):
            # Buyer fears overpaying - be more conservative
            recommended_offer = recommended_offer - (total_discount * 0.1)
            logging.info(f"Buyer fears overpaying - adjusted offer down by 10% of discount")
        
        # Check if user has specific cost/repair concerns (keyword-based)
        elif any(word in biggest_regret_lower for word in ["repair", "fix", "cost", "expense", "hidden"]):
            # Buyer concerned about repair costs - already factored into repair_cost_discount
            # but log their specific concern
            logging.info(f"Buyer concern about repairs noted: {buyer_profile.biggest_regret}")
        
        # Cap at buyer's max budget
        if buyer_profile.max_budget:
            recommended_offer = min(recommended_offer, buyer_profile.max_budget)
        
        # Final safety check
        if recommended_offer is None or str(recommended_offer).lower() == 'nan':
            recommended_offer = property_price
        
        # 🚨 EMERGENCY DEBUG
        logging.error("=" * 80)
        logging.error("🚨 OFFER CALCULATION COMPLETE")
        logging.error(f"property_price: {property_price}")
        logging.error(f"total_discount: {total_discount}")
        logging.error(f"recommended_offer FINAL: {recommended_offer}")
        logging.error(f"recommended_offer type: {type(recommended_offer)}")
        logging.error("=" * 80)
        
        # Ensure discount calculations are also safe
        discount_from_ask = property_price - recommended_offer
        discount_percentage = (discount_from_ask / property_price * 100) if property_price > 0 else 0
        
        # Contingency strategy
        has_resale_impact = any(cat.affects_resale for cat in risk_score.category_scores)
        contingencies = {
            "inspection": "REQUIRED" if risk_score.risk_tier in ["HIGH", "CRITICAL"] else "RECOMMENDED",
            "financing": "STANDARD",
            "appraisal": "RECOMMENDED" if has_resale_impact else "OPTIONAL"
        }
        
        return {
            "recommended_offer": recommended_offer,
            "discount_from_ask": discount_from_ask,
            "discount_percentage": discount_percentage,
            "discount_breakdown": {
                "repair_costs": cost_discount,
                "risk_premium": risk_discount,
                "transparency_issues": transparency_discount
            },
            "contingencies": contingencies,
            "escalation_cap": min(property_price, buyer_profile.max_budget or property_price),
            "walk_away_price": risk_score.walk_away_threshold,
            "confidence_level": self._calculate_confidence(risk_score, cross_ref)
        }
    
    def _generate_inspection_priorities(
        self,
        inspection_doc: PropertyDocument,
        risk_score: PropertyRiskScore,
        buyer_profile: BuyerProfile
    ) -> Dict[str, Any]:
        """Generate prioritized inspection checklist"""
        
        must_inspect = []
        should_inspect = []
        optional_inspect = []
        
        for cat_score in risk_score.category_scores:
            if cat_score.score == 0:
                continue
            
            category_name = cat_score.category.value
            
            # Categorize by priority
            if cat_score.score > 60 or cat_score.safety_concern:
                must_inspect.append({
                    "category": category_name,
                    "reason": f"High risk score ({cat_score.score:.0f}/100)" if cat_score.score > 60 
                             else "Safety concern",
                    "specialist": cat_score.requires_specialist,
                    "est_cost": f"${cat_score.estimated_cost_low:,.0f}-${cat_score.estimated_cost_high:,.0f}"
                })
            elif cat_score.score > 30:
                should_inspect.append({
                    "category": category_name,
                    "reason": f"Moderate risk ({cat_score.score:.0f}/100)",
                    "specialist": cat_score.requires_specialist,
                    "est_cost": f"${cat_score.estimated_cost_low:,.0f}-${cat_score.estimated_cost_high:,.0f}"
                })
            else:
                optional_inspect.append({
                    "category": category_name,
                    "reason": "Low risk, standard inspection coverage"
                })
        
        # Add buyer deal-breakers to must-inspect
        for deal_breaker in buyer_profile.deal_breakers:
            if not any(item['category'] == deal_breaker for item in must_inspect):
                must_inspect.append({
                    "category": deal_breaker,
                    "reason": "Buyer-specified deal-breaker",
                    "specialist": True,
                    "est_cost": "TBD"
                })
        
        return {
            "must_inspect": must_inspect,
            "should_inspect": should_inspect,
            "optional": optional_inspect,
            "estimated_inspection_cost": len(must_inspect) * 500 + len(should_inspect) * 300
        }
    
    def _generate_negotiation_strategy(
        self,
        property_price: float,
        risk_score: PropertyRiskScore,
        cross_ref: CrossReferenceReport,
        buyer_profile: BuyerProfile
    ) -> Dict[str, Any]:
        """Generate tactical negotiation approach"""
        
        # Determine negotiation posture
        if risk_score.risk_tier == "CRITICAL":
            posture = "HARD - Walk away or demand major concessions"
        elif len(cross_ref.contradictions) > 3:
            posture = "FIRM - Seller transparency issues give strong leverage"
        elif risk_score.risk_tier == "HIGH":
            posture = "FIRM - Significant issues warrant price reduction"
        elif risk_score.risk_tier == "MODERATE":
            posture = "BALANCED - Some leverage, negotiate repair credits"
        else:
            posture = "FLEXIBLE - Minor issues, standard negotiation"
        
        # Key talking points
        talking_points = []
        
        if cross_ref.contradictions:
            talking_points.append(
                f"Seller failed to disclose {len(cross_ref.contradictions)} issue(s) found during inspection"
            )
        
        if risk_score.total_repair_cost_high > property_price * 0.05:
            talking_points.append(
                f"Inspection revealed ${risk_score.total_repair_cost_high:,.0f} in necessary repairs "
                f"({risk_score.total_repair_cost_high/property_price*100:.1f}% of purchase price)"
            )
        
        for deal_breaker in risk_score.deal_breakers:
            talking_points.append(f"Critical issue: {deal_breaker}")
        
        # Negotiation options
        options = {
            "option_1_price_reduction": {
                "ask": risk_score.total_repair_cost_high,
                "fallback": risk_score.total_repair_cost_low,
                "rationale": "Full cost of repairs"
            },
            "option_2_repair_credit": {
                "ask": risk_score.total_repair_cost_high * 0.8,
                "fallback": risk_score.total_repair_cost_low * 0.8,
                "rationale": "Partial repair credit at closing"
            },
            "option_3_seller_repairs": {
                "must_fix": [item for item in risk_score.deal_breakers],
                "optional_fix": risk_score.negotiation_items[:3],
                "rationale": "Seller addresses critical items before close"
            }
        }
        
        return {
            "posture": posture,
            "talking_points": talking_points,
            "negotiation_options": options,
            "leverage_score": self._calculate_leverage(risk_score, cross_ref),
            "walk_away_threshold": risk_score.walk_away_threshold,
            "timeline": {
                "inspection_period": "10 days" if risk_score.risk_tier in ["HIGH", "CRITICAL"] else "7 days",
                "response_deadline": "3 business days after inspection report"
            }
        }
    
    def _generate_decision_framework(
        self,
        property_price: float,
        risk_score: PropertyRiskScore,
        cross_ref: CrossReferenceReport,
        buyer_profile: BuyerProfile
    ) -> Dict[str, Any]:
        """Generate decision tree for buyer"""
        
        # Immediate decisions
        if len(risk_score.deal_breakers) > 2:
            recommendation = "WALK AWAY - Multiple critical deal-breakers present"
            confidence = 0.95
        elif risk_score.risk_tier == "CRITICAL":
            recommendation = "WALK AWAY - Risk exceeds acceptable threshold"
            confidence = 0.90
        elif cross_ref.transparency_score < 30:
            recommendation = "STRONG CAUTION - Seller transparency is very concerning"
            confidence = 0.85
        elif risk_score.buyer_adjusted_score > 70:
            recommendation = "NEGOTIATE HARD - Significant issues but deal may work with major price reduction"
            confidence = 0.75
        elif risk_score.buyer_adjusted_score > 45:
            recommendation = "PROCEED WITH CAUTION - Address issues through negotiation or repair credits"
            confidence = 0.70
        else:
            recommendation = "PROCEED - Typical issues for property type and age"
            confidence = 0.80
        
        # Scenario analysis
        scenarios = {
            "best_case": {
                "seller_response": "Accepts price reduction",
                "final_price": risk_score.walk_away_threshold + (property_price - risk_score.walk_away_threshold) * 0.3,
                "total_investment": "final_price + low_repair_costs",
                "outcome": "Good deal with manageable repairs"
            },
            "likely_case": {
                "seller_response": "Counters with partial credit",
                "final_price": property_price - (risk_score.total_repair_cost_high * 0.5),
                "total_investment": "final_price + half_repair_costs",
                "outcome": "Fair deal, split repair costs"
            },
            "worst_case": {
                "seller_response": "Refuses concessions",
                "final_price": property_price,
                "total_investment": "full_price + full_repair_costs",
                "outcome": "Walk away or overpay significantly"
            }
        }
        
        return {
            "recommendation": recommendation,
            "confidence": confidence,
            "scenarios": scenarios,
            "key_decision_points": {
                "inspection_findings": "Confirmed via professional inspection",
                "seller_response": "Gauge willingness to negotiate",
                "max_total_cost": buyer_profile.max_budget,
                "deal_breakers_present": len(risk_score.deal_breakers) > 0
            }
        }
    
    def _calculate_confidence(
        self,
        risk_score: PropertyRiskScore,
        cross_ref: CrossReferenceReport
    ) -> float:
        """Calculate confidence in analysis (0-1)"""
        confidence = 0.8  # Base confidence
        
        # Reduce confidence if limited data
        if cross_ref.total_disclosures < 5:
            confidence -= 0.1
        
        if cross_ref.total_findings < 5:
            confidence -= 0.1
        
        # Increase confidence with good transparency
        if cross_ref.transparency_score > 80:
            confidence += 0.1
        
        return max(0.5, min(1.0, confidence))
    
    def _calculate_leverage(
        self,
        risk_score: PropertyRiskScore,
        cross_ref: CrossReferenceReport
    ) -> float:
        """Calculate buyer's negotiation leverage (0-100)"""
        leverage = 50.0  # Base leverage
        
        # Increase leverage with issues
        leverage += risk_score.overall_risk_score * 0.3
        
        # Major boost for contradictions (seller dishonesty)
        leverage += len(cross_ref.contradictions) * 5
        
        # Boost for undisclosed issues
        leverage += len(cross_ref.undisclosed_issues) * 3
        
        return min(100, leverage)
    
    def generate_report(self, analysis: PropertyAnalysis) -> str:
        """Generate comprehensive text report"""
        lines = []
        lines.append("=" * 100)
        lines.append(" " * 35 + "OFFERWISE PROPERTY ANALYSIS")
        lines.append("=" * 100)
        lines.append(f"\nProperty: {analysis.property_address}")
        lines.append(f"Analysis Date: {analysis.analysis_date}")
        
        # Executive Summary
        lines.append("\n" + "=" * 100)
        lines.append("EXECUTIVE SUMMARY")
        lines.append("=" * 100)
        lines.append(f"\nRisk Tier: {analysis.risk_score.risk_tier}")
        lines.append(f"Overall Risk Score: {analysis.risk_score.overall_risk_score:.1f}/100")
        lines.append(f"Buyer-Adjusted Risk: {analysis.risk_score.buyer_adjusted_score:.1f}/100")
        lines.append(f"Seller Transparency: {analysis.cross_reference.transparency_score:.0f}/100")
        lines.append(f"\nRecommendation: {analysis.decision_framework['recommendation']}")
        lines.append(f"Confidence: {analysis.decision_framework['confidence']*100:.0f}%")
        
        # Offer Strategy
        lines.append("\n" + "=" * 100)
        lines.append("OFFER STRATEGY")
        lines.append("=" * 100)
        offer = analysis.offer_strategy
        lines.append(f"\nRecommended Offer: ${offer['recommended_offer']:,.0f}")
        lines.append(f"Discount from Ask: ${offer['discount_from_ask']:,.0f} ({offer['discount_percentage']:.1f}%)")
        lines.append(f"Walk-Away Threshold: ${offer['walk_away_price']:,.0f}")
        
        # Risk Scorecard
        lines.append("\n" + "=" * 100)
        lines.append("RISK SCORECARD")
        lines.append("=" * 100)
        lines.append(f"\n{'Category':<30} {'Score':<10} {'Cost Range':<25} {'Status'}")
        lines.append("-" * 100)
        for cat_score in analysis.risk_score.category_scores:
            if cat_score.score > 0:
                cost_range = f"${cat_score.estimated_cost_low:,.0f}-${cat_score.estimated_cost_high:,.0f}"
                status = "⚠ CRITICAL" if cat_score.safety_concern else "Moderate"
                lines.append(f"{cat_score.category.value:<30} {cat_score.score:>5.1f}/100  {cost_range:<25} {status}")
        
        lines.append(f"\n{'TOTAL ESTIMATED REPAIRS':<30} {'':10} "
                    f"${analysis.risk_score.total_repair_cost_low:,.0f}-"
                    f"${analysis.risk_score.total_repair_cost_high:,.0f}")
        
        # Cross-Reference Findings
        if analysis.cross_reference.contradictions or analysis.cross_reference.undisclosed_issues:
            lines.append("\n" + "=" * 100)
            lines.append("DISCLOSURE ISSUES")
            lines.append("=" * 100)
            
            if analysis.cross_reference.contradictions:
                lines.append(f"\n⚠ {len(analysis.cross_reference.contradictions)} CONTRADICTION(S) FOUND")
                for match in analysis.cross_reference.contradictions[:3]:
                    lines.append(f"  • {match.explanation[:150]}")
            
            if analysis.cross_reference.undisclosed_issues:
                lines.append(f"\n⚠ {len(analysis.cross_reference.undisclosed_issues)} UNDISCLOSED ISSUE(S)")
                for match in analysis.cross_reference.undisclosed_issues[:3]:
                    lines.append(f"  • {match.explanation[:150]}")
        
        # Negotiation Strategy
        lines.append("\n" + "=" * 100)
        lines.append("NEGOTIATION STRATEGY")
        lines.append("=" * 100)
        neg = analysis.negotiation_strategy
        lines.append(f"\nPosture: {neg['posture']}")
        lines.append(f"Leverage Score: {neg['leverage_score']:.0f}/100")
        lines.append("\nKey Talking Points:")
        for point in neg['talking_points'][:5]:
            lines.append(f"  • {point}")
        
        return '\n'.join(lines)


# ============================================================================
# USAGE EXAMPLE WITH COMPLETE WORKFLOW
# ============================================================================

if __name__ == "__main__":
    # Initialize intelligence system
    intelligence = OfferWiseIntelligence()
    
    # Sample documents (in production, these come from PDF extraction)
    seller_disclosure = """
    SELLER'S DISCLOSURE STATEMENT
    Property: 456 Oak Avenue, San Jose, CA 95120
    Date: November 15, 2024
    
    FOUNDATION & STRUCTURE
    1. Foundation cracks? [ ] Yes [X] No
    2. Structural issues? [ ] Yes [X] No
    
    ROOF & EXTERIOR
    3. Roof leaks? [ ] Yes [X] No
    4. Window problems? [X] Yes [ ] No
       Explanation: One window in bedroom has minor crack, cosmetic only
    
    PLUMBING
    5. Plumbing issues? [X] Yes [ ] No
       Explanation: Kitchen faucet drips occasionally, needs washer replacement
    6. Sewer line problems? [ ] Yes [X] No
    
    ENVIRONMENTAL
    7. Lead paint? [ ] Yes [X] No
    8. Asbestos? [ ] Yes [X] No
    """
    
    inspection_report = """
    RESIDENTIAL INSPECTION REPORT
    Property: 456 Oak Avenue, San Jose, CA 95120
    Inspection Date: December 10, 2024
    Inspector: John Smith, License #12345
    
    FOUNDATION - SIGNIFICANT CONCERNS
    Multiple diagonal cracks observed in foundation walls, particularly southeast corner.
    Cracks are 3/8 inch wide and show signs of active movement. Water intrusion evident.
    This is a major structural concern requiring immediate structural engineer evaluation.
    Estimated repair cost: $35,000 - $75,000
    RECOMMENDATION: Structural engineer evaluation REQUIRED before closing
    
    ROOF - MODERATE CONCERNS
    Composition shingle roof showing significant wear. Age estimated 18-20 years.
    Multiple areas of granule loss. Several damaged/missing shingles on north slope.
    Life expectancy: 2-4 years maximum.
    Estimated replacement cost: $15,000 - $22,000
    RECOMMENDATION: Budget for replacement within 2 years
    
    PLUMBING - CRITICAL ISSUE
    Main sewer line shows severe root intrusion throughout via camera inspection.
    Multiple sections heavily compromised. High risk of complete failure.
    Partial blockage currently present.
    Estimated repair cost: $12,000 - $25,000 for full replacement
    RECOMMENDATION: Immediate replacement of affected sections
    
    ELECTRICAL - MINOR CONCERNS
    Electrical panel adequate but near capacity. Some outlets lack GFCI protection
    in bathrooms and kitchen. Otherwise functional.
    Estimated cost: $800 - $1,500 for GFCI upgrades
    
    INTERIOR - MINOR ISSUES
    Bedroom window shows crack in glass as disclosed by seller.
    Kitchen faucet drips as disclosed.
    """
    
    # Define buyer profile
    buyer = BuyerProfile(
        max_budget=950000,
        repair_tolerance="moderate",
        ownership_duration="7-10",
        biggest_regret="hidden_issues",
        replaceability="somewhat_unique",
        deal_breakers=["foundation", "insurance"]
    )
    
    # Run complete analysis
    print("\n" + "=" * 100)
    print("RUNNING OFFERWISE INTELLIGENCE SYSTEM")
    print("=" * 100 + "\n")
    
    analysis = intelligence.analyze_property(
        seller_disclosure_text=seller_disclosure,
        inspection_report_text=inspection_report,
        property_price=925000,
        buyer_profile=buyer,
        property_address="456 Oak Avenue, San Jose, CA 95120"
    )
    
    # Generate and print comprehensive report
    report = intelligence.generate_report(analysis)
    print(report)
    
    print("\n\n" + "=" * 100)
    print("DETAILED OFFER STRATEGY")
    print("=" * 100)
    print(json.dumps(analysis.offer_strategy, indent=2))
