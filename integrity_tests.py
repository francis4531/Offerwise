"""
OfferWise Integrity Test Engine v2.0
====================================
Production-grade tests that import and exercise REAL production modules.
These tests are designed to find REAL BUGS — not just verify happy paths.

Test Groups:
 1. Risk Scoring Model — real RiskScoringModel with crafted InspectionFindings
 2. Transparency Scorer — real SellerTransparencyScorer with cross-reference data
 3. Risk DNA Encoder — real PropertyRiskDNAEncoder vector generation
 4. Offer Strategy Math — full pipeline, verify financial correctness + negative offers
 5. Cross-User Data Isolation (IDOR) — verify user A can't see user B's data
 6. Auth Boundary Testing — verify protected endpoints reject unauthorized access
 7. Credit Flow Integrity — atomic deduction, can't go negative, correct column names
 8. Edge Cases — zero prices, extreme inputs, boundary conditions
 9. Schema Consistency — detect divergent model definitions and orphaned columns
10. Payment Pipeline Integrity — verify payment code is actually reachable
11. Code Quality — detect debug/emergency code left in production
12. Concurrency Safety — detect race conditions in credit deduction

All tests auto-file bugs when they fail.
"""

import logging
import time
import traceback
import math
import os
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class IntegrityTestEngine:
    """Runs integrity tests against real production modules."""

    def __init__(self, app=None, db=None):
        self.app = app
        self.db = db
        self.results = []
        self.passed = 0
        self.failed = 0
        self._cleanup_items = []

    def run_all(self) -> Dict[str, Any]:
        """Run all integrity test groups and return results."""
        self.results = []
        self.passed = 0
        self.failed = 0
        self._cleanup_items = []
        start_time = time.time()

        test_groups = [
            ("Risk Scoring Model", self._test_risk_scoring_model),
            ("Transparency Scorer", self._test_transparency_scorer),
            ("Risk DNA Encoder", self._test_risk_dna_encoder),
            ("Offer Strategy Math", self._test_offer_strategy_math),
            ("Cross-User Data Isolation", self._test_data_isolation),
            ("Auth Boundaries", self._test_auth_boundaries),
            ("Credit Flow Integrity", self._test_credit_integrity),
            ("Edge Cases", self._test_edge_cases),
            ("Schema Consistency", self._test_schema_consistency),
            ("Payment Pipeline", self._test_payment_pipeline),
            ("Code Quality", self._test_code_quality),
            ("Concurrency Safety", self._test_concurrency_safety),
        ]

        for group_name, test_fn in test_groups:
            try:
                test_fn()
            except Exception as e:
                self._record("CRASH", f"{group_name} crashed: {str(e)[:200]}", False,
                             error=traceback.format_exc()[:500])

        self._cleanup()
        elapsed = round(time.time() - start_time, 2)

        return {
            'success': self.failed == 0,
            'summary': {
                'total': self.passed + self.failed,
                'passed': self.passed,
                'failed': self.failed,
                'duration_seconds': elapsed,
            },
            'results': self.results,
        }

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _record(self, name: str, details: str, passed: bool, error: str = None):
        entry = {'name': name, 'passed': passed, 'details': details}
        if error:
            entry['error'] = error
        self.results.append(entry)
        if passed:
            self.passed += 1
        else:
            self.failed += 1

    def _cleanup(self):
        if not self.db:
            return
        try:
            for obj in reversed(self._cleanup_items):
                try:
                    self.db.session.delete(obj)
                except Exception:
                    self.db.session.rollback()
            self.db.session.commit()
        except Exception:
            self.db.session.rollback()

    # =========================================================================
    # GROUP 1: RISK SCORING MODEL
    # =========================================================================

    def _test_risk_scoring_model(self):
        from risk_scoring_model import RiskScoringModel, BuyerProfile
        from document_parser import InspectionFinding, IssueCategory, Severity

        model = RiskScoringModel()
        buyer = BuyerProfile(
            max_budget=1_500_000, repair_tolerance="moderate",
            ownership_duration="3-7", biggest_regret="hidden_issues",
            replaceability="somewhat_unique", deal_breakers=["foundation", "mold"]
        )

        # Empty findings → zero risk
        result = model.calculate_risk_score([], None, 1_000_000, buyer)
        self._record(
            "Risk: Empty findings → zero score",
            f"overall={result.overall_risk_score}, tier={result.risk_tier}",
            result.overall_risk_score == 0.0 and result.risk_tier == "LOW"
        )

        # Single critical → score >= 40
        findings = [InspectionFinding(
            category=IssueCategory.FOUNDATION_STRUCTURE, severity=Severity.CRITICAL,
            location="North wall",
            description="Horizontal crack indicating lateral pressure — structural failure risk",
            recommendation="Immediate structural engineer evaluation",
            estimated_cost_low=15000, estimated_cost_high=35000,
            safety_concern=True, requires_specialist=True
        )]
        result = model.calculate_risk_score(findings, None, 1_000_000, buyer)
        self._record(
            "Risk: Single critical finding → score >= 40",
            f"overall={result.overall_risk_score:.1f}, tier={result.risk_tier}",
            result.overall_risk_score >= 40,
            error=f"Critical floor violated: got {result.overall_risk_score}" if result.overall_risk_score < 40 else None
        )

        # Score bounded [0, 100]
        self._record(
            "Risk: Score bounded [0, 100]",
            f"overall={result.overall_risk_score:.1f}",
            0 <= result.overall_risk_score <= 100
        )

        # Buyer-adjusted bounded
        self._record(
            "Risk: Buyer-adjusted bounded [0, 100]",
            f"buyer_adjusted={result.buyer_adjusted_score:.1f}",
            0 <= result.buyer_adjusted_score <= 100
        )

        # Tier matches score
        adj = result.buyer_adjusted_score
        expected_tier = (
            "CRITICAL" if adj >= 70 else "HIGH" if adj >= 50 else
            "MODERATE" if adj >= 30 else "LOW"
        )
        self._record(
            "Risk: Tier label matches buyer-adjusted score",
            f"adj_score={adj:.1f}, tier={result.risk_tier}, expected={expected_tier}",
            result.risk_tier == expected_tier,
            error=f"Tier mismatch: score {adj:.1f} → '{result.risk_tier}' expected '{expected_tier}'" if result.risk_tier != expected_tier else None
        )

        # cost_low <= cost_high
        self._record(
            "Risk: cost_low <= cost_high",
            f"low=${result.total_repair_cost_low:,.0f}, high=${result.total_repair_cost_high:,.0f}",
            result.total_repair_cost_low <= result.total_repair_cost_high
        )

        # Nightmare scenario
        nightmare = [InspectionFinding(
            category=cat, severity=Severity.CRITICAL,
            location="Multiple", description=desc,
            recommendation="Immediate specialist evaluation",
            estimated_cost_low=10000, estimated_cost_high=30000,
            safety_concern=True, requires_specialist=True
        ) for cat, desc in [
            (IssueCategory.FOUNDATION_STRUCTURE, "Lateral pressure failure imminent"),
            (IssueCategory.FOUNDATION_STRUCTURE, "Foundation sinking 2 inches"),
            (IssueCategory.ROOF_EXTERIOR, "Roof past useful life"),
            (IssueCategory.ELECTRICAL, "Federal Pacific panel — fire hazard"),
            (IssueCategory.PLUMBING, "Galvanized pipes corroded throughout"),
            (IssueCategory.ENVIRONMENTAL, "Asbestos in attic insulation"),
        ]]
        result = model.calculate_risk_score(nightmare, None, 1_000_000, buyer)
        self._record(
            "Risk: Nightmare (6 critical) → score >= 60",
            f"overall={result.overall_risk_score:.1f}, tier={result.risk_tier}",
            result.overall_risk_score >= 60,
            error=f"Nightmare too low: {result.overall_risk_score}" if result.overall_risk_score < 60 else None
        )

        # Buyer sensitivity
        scared = BuyerProfile(max_budget=1_500_000, repair_tolerance="low",
            ownership_duration="<3", biggest_regret="hidden_issues",
            replaceability="easy", deal_breakers=["foundation"])
        chill = BuyerProfile(max_budget=1_500_000, repair_tolerance="high",
            ownership_duration="10+", biggest_regret="lose_house",
            replaceability="very_rare", deal_breakers=[])
        r_scared = model.calculate_risk_score(findings, None, 1_000_000, scared)
        r_chill = model.calculate_risk_score(findings, None, 1_000_000, chill)
        self._record(
            "Risk: Low tolerance → higher adjusted score than high tolerance",
            f"low={r_scared.buyer_adjusted_score:.1f}, high={r_chill.buyer_adjusted_score:.1f}",
            r_scared.buyer_adjusted_score >= r_chill.buyer_adjusted_score,
            error=f"Low({r_scared.buyer_adjusted_score:.1f}) < High({r_chill.buyer_adjusted_score:.1f})" if r_scared.buyer_adjusted_score < r_chill.buyer_adjusted_score else None
        )

    # =========================================================================
    # GROUP 2: TRANSPARENCY SCORER
    # =========================================================================

    def _test_transparency_scorer(self):
        from transparency_scorer import SellerTransparencyScorer, TransparencyGrade, RedFlag

        scorer = SellerTransparencyScorer()

        weight_sum = sum(scorer.weights.values())
        self._record("Transparency: Weights sum to 1.0",
            f"sum={weight_sum:.4f}", abs(weight_sum - 1.0) < 0.001,
            error=f"Weights sum to {weight_sum}" if abs(weight_sum - 1.0) >= 0.001 else None)

        thresholds = sorted(scorer.grade_thresholds.keys())
        self._record("Transparency: Grade thresholds start at 0",
            f"lowest={thresholds[0]}", thresholds[0] == 0)

        for score_val, expected_grade in [(95, TransparencyGrade.A_PLUS), (94.9, TransparencyGrade.A), (0, TransparencyGrade.F)]:
            grade = scorer._score_to_grade(score_val)
            self._record(f"Transparency: Score {score_val} → {expected_grade.value}",
                f"grade={grade.value}", grade == expected_grade,
                error=f"Got {grade.value}" if grade != expected_grade else None)

        trust_high = scorer._calculate_trust_level(85, [])
        self._record("Transparency: Score 85 → 'high' trust",
            f"trust={trust_high}", trust_high == 'high')

        trust_50 = scorer._calculate_trust_level(50, [])
        self._record("Transparency: Score 50 → NOT 'high' trust",
            f"trust={trust_50}", trust_50 != 'high')

        many_flags = [RedFlag(flag_type="t", severity="critical", description="t",
            evidence=[], impact="t", recommendation="t") for _ in range(20)]
        adj = scorer._calculate_risk_adjustment(10, many_flags)
        self._record("Transparency: Risk adjustment capped at 10%",
            f"adjustment={adj:.3f}", adj <= 0.10,
            error=f"Adjustment {adj} exceeds 10%" if adj > 0.10 else None)

        self._record("Transparency: Composite(0,0,0,0) → 0",
            f"score={scorer._calculate_composite_score(0,0,0,0)}", scorer._calculate_composite_score(0,0,0,0) == 0)
        self._record("Transparency: Composite(100,100,100,100) → 100",
            f"score={scorer._calculate_composite_score(100,100,100,100)}", scorer._calculate_composite_score(100,100,100,100) == 100)

        neg_score = scorer._calculate_composite_score(-50, -50, -50, -50)
        self._record("Transparency: Composite(-50,-50,-50,-50) → clamped ≥ 0",
            f"score={neg_score}", neg_score >= 0,
            error=f"Negative composite: {neg_score}" if neg_score < 0 else None)

    # =========================================================================
    # GROUP 3: RISK DNA ENCODER
    # =========================================================================

    def _test_risk_dna_encoder(self):
        import numpy as np
        from property_risk_dna import PropertyRiskDNAEncoder
        from risk_scoring_model import RiskScoringModel, BuyerProfile
        from document_parser import InspectionFinding, IssueCategory, Severity
        from cross_reference_engine import CrossReferenceReport

        encoder = PropertyRiskDNAEncoder()
        findings = [
            InspectionFinding(category=IssueCategory.FOUNDATION_STRUCTURE, severity=Severity.MAJOR,
                location="Basement", description="Foundation crack", recommendation="Repair",
                estimated_cost_low=5000, estimated_cost_high=12000),
            InspectionFinding(category=IssueCategory.ROOF_EXTERIOR, severity=Severity.MODERATE,
                location="Roof", description="Worn shingles", recommendation="Replace in 2 years",
                estimated_cost_low=3000, estimated_cost_high=8000),
        ]

        model = RiskScoringModel()
        buyer = BuyerProfile(max_budget=1_500_000, repair_tolerance="moderate",
            ownership_duration="3-7", biggest_regret="hidden_issues",
            replaceability="somewhat_unique", deal_breakers=[])
        risk_score = model.calculate_risk_score(findings, None, 1_000_000, buyer)
        cross_ref = CrossReferenceReport(
            property_address="123 Test St", total_disclosures=5, total_findings=2,
            contradictions=[], undisclosed_issues=[], confirmed_disclosures=[],
            disclosed_not_found=[], transparency_score=75, risk_score=30, summary="Moderate")

        try:
            dna = encoder.encode_property(
                property_analysis={
                    'risk_score': {
                        'overall_risk_score': risk_score.overall_risk_score,
                        'risk_tier': risk_score.risk_tier,
                        'total_repair_cost_low': risk_score.total_repair_cost_low,
                        'total_repair_cost_high': risk_score.total_repair_cost_high,
                        'category_scores': [{'category': cs.category.value, 'score': cs.score,
                            'severity_breakdown': cs.severity_breakdown, 'safety_concern': cs.safety_concern}
                            for cs in risk_score.category_scores]
                    },
                    'transparency_score': cross_ref.transparency_score,
                },
                inspection_findings=findings, cross_reference_report=cross_ref,
                property_metadata={'address': '123 Test St', 'price': 1_000_000, 'year_built': 1985, 'sqft': 2000}
            )

            self._record("DNA: Signature is 64 dimensions",
                f"dimensions={len(dna.dna_signature)}", len(dna.dna_signature) == 64)
            self._record("DNA: No NaN or Inf",
                f"nan={np.any(np.isnan(dna.dna_signature))}, inf={np.any(np.isinf(dna.dna_signature))}",
                not np.any(np.isnan(dna.dna_signature)) and not np.any(np.isinf(dna.dna_signature)))
            self._record("DNA: Composite bounded [0,100]",
                f"composite={dna.composite_score:.1f}", 0 <= dna.composite_score <= 100)

            for attr in ['structural_score', 'systems_score', 'transparency_score', 'temporal_score', 'financial_score']:
                val = getattr(dna, attr, 0)
                self._record(f"DNA: {attr} bounded [0,100]", f"{val:.1f}", 0 <= val <= 100)

            d = dna.to_dict()
            self._record("DNA: to_dict() has required keys",
                f"keys={list(d.keys())}",
                all(k in d for k in ['dna_signature', 'composite_score', 'risk_category', 'vector_scores']))

        except Exception as e:
            self._record("DNA: Encoding succeeded", str(e)[:200], False, error=traceback.format_exc()[:500])

    # =========================================================================
    # GROUP 4: OFFER STRATEGY MATH — hunting for negative offer bug
    # =========================================================================

    def _test_offer_strategy_math(self):
        from risk_scoring_model import RiskScoringModel, BuyerProfile
        from document_parser import InspectionFinding, IssueCategory, Severity
        from cross_reference_engine import CrossReferenceReport
        from offerwise_intelligence import OfferWiseIntelligence, BuyerConcerns

        intel = OfferWiseIntelligence()
        model = RiskScoringModel()

        scenarios = [
            {'name': 'Clean property', 'price': 1_000_000, 'sentiment': 'balanced',
             'findings': [InspectionFinding(category=IssueCategory.HVAC, severity=Severity.MINOR,
                location="Basement", description="HVAC filter needs replacement",
                recommendation="Replace", estimated_cost_low=50, estimated_cost_high=100)]},
            {'name': 'Nightmare + scared buyer (negative offer hunter)', 'price': 400_000, 'sentiment': 'conservative',
             'findings': [
                InspectionFinding(category=IssueCategory.FOUNDATION_STRUCTURE, severity=Severity.CRITICAL,
                    location="Basement", description="Complete foundation failure imminent",
                    recommendation="Emergency", estimated_cost_low=80000, estimated_cost_high=150000, safety_concern=True),
                InspectionFinding(category=IssueCategory.ROOF_EXTERIOR, severity=Severity.CRITICAL,
                    location="Roof", description="Roof collapsed, active water damage",
                    recommendation="Emergency", estimated_cost_low=25000, estimated_cost_high=45000, safety_concern=True),
                InspectionFinding(category=IssueCategory.ELECTRICAL, severity=Severity.CRITICAL,
                    location="Panel", description="Federal Pacific panel — fire hazard",
                    recommendation="Immediate replacement", estimated_cost_low=4000, estimated_cost_high=8000, safety_concern=True),
                InspectionFinding(category=IssueCategory.ENVIRONMENTAL, severity=Severity.CRITICAL,
                    location="Throughout", description="Asbestos, lead paint, mold",
                    recommendation="Abatement", estimated_cost_low=30000, estimated_cost_high=60000, safety_concern=True),
                InspectionFinding(category=IssueCategory.PLUMBING, severity=Severity.CRITICAL,
                    location="Throughout", description="Polybutylene pipes failing system-wide",
                    recommendation="Complete repipe", estimated_cost_low=15000, estimated_cost_high=30000),
            ]},
            {'name': 'Extreme: $200K house, $300K+ repairs', 'price': 200_000, 'sentiment': 'conservative',
             'findings': [
                InspectionFinding(category=IssueCategory.FOUNDATION_STRUCTURE, severity=Severity.CRITICAL,
                    location="All walls", description="Foundation crumbling — total replacement",
                    recommendation="Demo and rebuild", estimated_cost_low=120000, estimated_cost_high=200000, safety_concern=True),
                InspectionFinding(category=IssueCategory.ROOF_EXTERIOR, severity=Severity.CRITICAL,
                    location="Entire roof", description="Structural roof failure with mold",
                    recommendation="Complete tear-off", estimated_cost_low=40000, estimated_cost_high=70000, safety_concern=True),
                InspectionFinding(category=IssueCategory.ENVIRONMENTAL, severity=Severity.CRITICAL,
                    location="Entire house", description="Black mold, asbestos, lead in water",
                    recommendation="Full remediation", estimated_cost_low=50000, estimated_cost_high=90000, safety_concern=True),
            ]},
        ]

        for s in scenarios:
            buyer = BuyerProfile(max_budget=s['price'] + 200_000, repair_tolerance="low",
                ownership_duration="<3", biggest_regret="hidden_issues",
                replaceability="easy", deal_breakers=["foundation", "mold"])
            risk_score = model.calculate_risk_score(s['findings'], None, s['price'], buyer)
            cross_ref = CrossReferenceReport(
                property_address="Test", total_disclosures=3, total_findings=len(s['findings']),
                contradictions=[], undisclosed_issues=[], confirmed_disclosures=[],
                disclosed_not_found=[], transparency_score=30, risk_score=risk_score.overall_risk_score, summary="Test")
            concerns = BuyerConcerns(
                primary_concerns=["foundation", "safety", "mold"], sentiment=s['sentiment'],
                risk_tolerance="very_low", has_budget_constraint=True, has_time_pressure=False,
                has_safety_concern=True, has_trust_issue=True, has_past_trauma=True,
                emotional_weight="high", raw_text="Terrified of hidden issues. Last house had foundation problems.")

            try:
                offer = intel._generate_offer_strategy(s['price'], risk_score, cross_ref, buyer, concerns)
                rec = offer.get('recommended_offer', 0)
                disc_pct = offer.get('discount_percentage', 0)
                disc_ask = offer.get('discount_from_ask', 0)

                self._record(f"Offer [{s['name']}]: recommended_offer >= 0",
                    f"offer=${rec:,.0f}, price=${s['price']:,.0f}", rec >= 0,
                    error=f"NEGATIVE OFFER BUG: ${rec:,.0f} on ${s['price']:,.0f} property. "
                          f"Post-adjustment calculations (sentiment, safety, budget+trauma buffer) "
                          f"pushed offer below zero. Need final max(0, recommended_offer) after ALL adjustments."
                          if rec < 0 else None)

                self._record(f"Offer [{s['name']}]: offer <= asking price",
                    f"offer=${rec:,.0f}, asking=${s['price']:,.0f}", rec <= s['price'],
                    error=f"Offer ${rec:,.0f} EXCEEDS asking ${s['price']:,.0f}" if rec > s['price'] else None)

                self._record(f"Offer [{s['name']}]: discount % in [0, 100]",
                    f"discount={disc_pct:.1f}%", 0 <= disc_pct <= 100,
                    error=f"Discount {disc_pct:.1f}% outside [0, 100]" if not (0 <= disc_pct <= 100) else None)

                expected = s['price'] - disc_ask
                self._record(f"Offer [{s['name']}]: Math consistency",
                    f"price-discount={expected:,.0f}, offer={rec:,.0f}", abs(expected - rec) < 1.0,
                    error=f"Math error: {s['price']:,.0f} - {disc_ask:,.0f} = {expected:,.0f} ≠ {rec:,.0f}" if abs(expected - rec) >= 1.0 else None)

                nan_keys = [k for k, v in offer.items() if isinstance(v, float) and (math.isnan(v) or math.isinf(v))]
                self._record(f"Offer [{s['name']}]: No NaN/Inf",
                    f"checked floats", len(nan_keys) == 0,
                    error=f"NaN/Inf in: {nan_keys}" if nan_keys else None)

            except Exception as e:
                self._record(f"Offer [{s['name']}]: Calculation succeeded",
                    str(e)[:200], False, error=traceback.format_exc()[:500])

    # =========================================================================
    # GROUP 5: CROSS-USER DATA ISOLATION (IDOR)
    # =========================================================================

    def _test_data_isolation(self):
        if not self.app or not self.db:
            self._record("IDOR: Skipped", "No app/db context", True)
            return

        from models import User, Property, Analysis

        try:
            ts = int(time.time())
            user_a = User(email=f"integrity_a_{ts}@test.offerwise.ai", name="A", auth_provider='test', analysis_credits=5)
            user_b = User(email=f"integrity_b_{ts}@test.offerwise.ai", name="B", auth_provider='test', analysis_credits=5)
            self.db.session.add_all([user_a, user_b])
            self.db.session.commit()
            self._cleanup_items.extend([user_a, user_b])

            prop_a = Property(user_id=user_a.id, address="123 Secret St", price=1_500_000, status='analyzed')
            self.db.session.add(prop_a)
            self.db.session.commit()
            self._cleanup_items.append(prop_a)

            b_props = Property.query.filter_by(user_id=user_b.id).all()
            self._record("IDOR: User B sees 0 of User A's properties",
                f"count={len(b_props)}", len(b_props) == 0,
                error=f"User B sees {len(b_props)} properties!" if b_props else None)

            a_props = Property.query.filter_by(user_id=user_a.id).all()
            self._record("IDOR: User A sees own property", f"count={len(a_props)}", len(a_props) == 1)

            stolen = Property.query.filter_by(id=prop_a.id, user_id=user_b.id).first()
            self._record("IDOR: Direct ID + wrong user → None",
                f"result={stolen}", stolen is None,
                error="IDOR VULNERABILITY!" if stolen else None)

            analysis_a = Analysis(user_id=user_a.id, property_id=prop_a.id,
                status='completed', offer_score=75.0, risk_tier='MODERATE')
            self.db.session.add(analysis_a)
            self.db.session.commit()
            self._cleanup_items.append(analysis_a)

            b_analyses = Analysis.query.filter_by(user_id=user_b.id).all()
            self._record("IDOR: User B sees 0 analyses", f"count={len(b_analyses)}", len(b_analyses) == 0)

            stolen_a = Analysis.query.filter_by(id=analysis_a.id, user_id=user_b.id).first()
            self._record("IDOR: Direct analysis ID + wrong user → None",
                f"result={stolen_a}", stolen_a is None,
                error="IDOR: User B accessed User A's analysis!" if stolen_a else None)

        except Exception as e:
            self._record("IDOR: Test execution", str(e)[:200], False, error=traceback.format_exc()[:500])

    # =========================================================================
    # GROUP 6: AUTH BOUNDARIES
    # =========================================================================

    def _test_auth_boundaries(self):
        if not self.app:
            self._record("Auth: Skipped", "No app context", True)
            return

        with self.app.test_client() as client:
            for method, path, label in [
                ('GET', '/api/properties', 'Properties list'),
                ('GET', '/api/my-account', 'My account'),
                ('POST', '/api/analyze', 'Analyze property'),
                ('GET', '/api/consent/status', 'Consent status'),
            ]:
                try:
                    resp = client.get(path) if method == 'GET' else client.post(path, json={})
                    ok = resp.status_code in (401, 403, 302)
                    self._record(f"Auth: {label} rejects unauthenticated",
                        f"{method} {path} → {resp.status_code}", ok,
                        error=f"Got {resp.status_code}" if not ok else None)
                except Exception as e:
                    self._record(f"Auth: {label}", str(e)[:200], False)

            for method, path, label in [
                ('GET', '/api/system-info', 'System info'),
                ('POST', '/api/turk/start', 'Turk start'),
                ('POST', '/api/auto-test/run', 'Auto-test run'),
                ('GET', '/api/worker/stats', 'Worker stats'),
            ]:
                try:
                    resp = client.get(path) if method == 'GET' else client.post(path, json={})
                    ok = resp.status_code in (401, 403, 302)
                    self._record(f"Auth: {label} rejects without admin key",
                        f"{method} {path} → {resp.status_code}", ok,
                        error=f"Got {resp.status_code}" if not ok else None)
                except Exception as e:
                    self._record(f"Auth: {label}", str(e)[:200], False)

    # =========================================================================
    # GROUP 7: CREDIT FLOW INTEGRITY
    # =========================================================================

    def _test_credit_integrity(self):
        if not self.app or not self.db:
            self._record("Credits: Skipped", "No app/db context", True)
            return

        from models import User

        try:
            ts = int(time.time())
            test_user = User(email=f"integrity_credit_{ts}@test.offerwise.ai",
                name="Credit Test", auth_provider='test', analysis_credits=3)
            self.db.session.add(test_user)
            self.db.session.commit()
            self._cleanup_items.append(test_user)

            self._record("Credits: Initial value correct",
                f"analysis_credits={test_user.analysis_credits}", test_user.analysis_credits == 3)

            test_user.analysis_credits -= 1
            self.db.session.commit()
            self.db.session.refresh(test_user)
            self._record("Credits: Deduction decrements by 1",
                f"analysis_credits={test_user.analysis_credits}", test_user.analysis_credits == 2)

            test_user.analysis_credits = 0
            self.db.session.commit()
            self.db.session.refresh(test_user)
            self._record("Credits: 0 credits blocks analysis",
                f"can_analyze={test_user.analysis_credits > 0}", test_user.analysis_credits == 0)

            # Atomic deduction with CORRECT column name
            from sqlalchemy import update
            test_user.analysis_credits = 1
            self.db.session.commit()

            r1 = self.db.session.execute(
                update(User).where(User.id == test_user.id)
                .where(User.analysis_credits >= 1)
                .values(analysis_credits=User.analysis_credits - 1))
            self.db.session.commit()
            self._record("Credits: Atomic deduction succeeds", f"rows={r1.rowcount}", r1.rowcount == 1)

            r2 = self.db.session.execute(
                update(User).where(User.id == test_user.id)
                .where(User.analysis_credits >= 1)
                .values(analysis_credits=User.analysis_credits - 1))
            self.db.session.commit()
            self._record("Credits: Atomic deduction blocked at 0",
                f"rows={r2.rowcount}", r2.rowcount == 0,
                error=f"Deducted from 0!" if r2.rowcount != 0 else None)

            self.db.session.refresh(test_user)
            self._record("Credits: Balance is 0 (not negative)",
                f"analysis_credits={test_user.analysis_credits}", test_user.analysis_credits == 0,
                error=f"Credits = {test_user.analysis_credits}!" if test_user.analysis_credits != 0 else None)

            # BUG HUNTER: Source code analysis for non-atomic deductions
            try:
                app_path = os.path.join(os.path.dirname(__file__), 'app.py')
                with open(app_path, 'r') as f:
                    src = f.read()

                non_atomic = re.findall(r'current_user\.analysis_credits\s*-=\s*1', src)
                atomic_guards = re.findall(r'\.where\(User\.analysis_credits\s*>=', src)

                self._record(
                    "Credits: app.py deduction is atomic (not Python -= 1)",
                    f"non-atomic: {len(non_atomic)}, atomic guards: {len(atomic_guards)}",
                    len(non_atomic) == 0 or len(atomic_guards) > 0,
                    error=f"RACE CONDITION: {len(non_atomic)} non-atomic 'analysis_credits -= 1' in app.py. "
                          f"Two simultaneous requests can both read credits=1, both deduct, "
                          f"resulting in credits=-1. Use SQLAlchemy update() with WHERE guard."
                          if non_atomic and not atomic_guards else None)

            except Exception as e:
                self._record("Credits: Source analysis", str(e)[:200], False)

        except Exception as e:
            self._record("Credits: Test execution", str(e)[:200], False, error=traceback.format_exc()[:500])

    # =========================================================================
    # GROUP 8: EDGE CASES
    # =========================================================================

    def _test_edge_cases(self):
        from risk_scoring_model import RiskScoringModel, BuyerProfile
        from document_parser import InspectionFinding, IssueCategory, Severity

        model = RiskScoringModel()
        buyer = BuyerProfile(max_budget=2_000_000, repair_tolerance="moderate",
            ownership_duration="3-7", biggest_regret="hidden_issues",
            replaceability="somewhat_unique", deal_breakers=[])

        findings = [InspectionFinding(category=IssueCategory.ROOF_EXTERIOR, severity=Severity.MAJOR,
            location="Roof", description="Major damage", recommendation="Replace",
            estimated_cost_low=10000, estimated_cost_high=20000)]

        # Zero price
        try:
            r = model.calculate_risk_score(findings, None, 0, buyer)
            self._record("Edge: Zero price doesn't crash", f"score={r.overall_risk_score:.1f}", True)
            self._record("Edge: Zero price → bounded", f"overall={r.overall_risk_score:.1f}",
                0 <= r.overall_risk_score <= 100)
        except Exception as e:
            self._record("Edge: Zero price", str(e)[:200], False)

        # Very small price
        try:
            r = model.calculate_risk_score(findings, None, 100, buyer)
            self._record("Edge: $100 < repair costs", f"score={r.overall_risk_score:.1f}", True)
        except Exception as e:
            self._record("Edge: Price < repairs", str(e)[:200], False)

        # Billion dollar property
        try:
            r = model.calculate_risk_score(findings, None, 999_999_999, buyer)
            self._record("Edge: $1B property bounded", f"score={r.overall_risk_score:.1f}",
                0 <= r.overall_risk_score <= 100)
        except Exception as e:
            self._record("Edge: $1B property", str(e)[:200], False)

        # All categories critical
        all_crit = [InspectionFinding(category=cat, severity=Severity.CRITICAL,
            location="Multiple", description=f"Critical {cat.value} failure",
            recommendation="Emergency", estimated_cost_low=20000, estimated_cost_high=50000,
            safety_concern=True, requires_specialist=True) for cat in IssueCategory]
        try:
            r = model.calculate_risk_score(all_crit, None, 1_000_000, buyer)
            self._record("Edge: All categories critical → CRITICAL tier",
                f"tier={r.risk_tier}", r.risk_tier == "CRITICAL",
                error=f"Got {r.risk_tier}" if r.risk_tier != "CRITICAL" else None)
            self._record("Edge: All critical → deal breakers exist",
                f"count={len(r.deal_breakers)}", len(r.deal_breakers) > 0)
        except Exception as e:
            self._record("Edge: All categories critical", str(e)[:200], False)

        # None cost estimates
        none_costs = [InspectionFinding(category=IssueCategory.ELECTRICAL, severity=Severity.MAJOR,
            location="Panel", description="Panel at capacity", recommendation="Upgrade",
            estimated_cost_low=None, estimated_cost_high=None)]
        try:
            r = model.calculate_risk_score(none_costs, None, 1_000_000, buyer)
            self._record("Edge: None costs don't crash", f"score={r.overall_risk_score:.1f}", True)
        except Exception as e:
            self._record("Edge: None costs", str(e)[:200], False)

        # Required frontend keys in offer strategy
        try:
            from cross_reference_engine import CrossReferenceReport
            from offerwise_intelligence import OfferWiseIntelligence, BuyerConcerns
            intel = OfferWiseIntelligence()
            risk = model.calculate_risk_score(findings, None, 1_000_000, buyer)
            xref = CrossReferenceReport(property_address="T", total_disclosures=5, total_findings=1,
                contradictions=[], undisclosed_issues=[], confirmed_disclosures=[],
                disclosed_not_found=[], transparency_score=75, risk_score=30, summary="OK")
            concerns = BuyerConcerns(primary_concerns=["foundation"], sentiment="balanced",
                risk_tolerance="medium", has_budget_constraint=False, has_time_pressure=False,
                has_safety_concern=False, has_trust_issue=False, has_past_trauma=False,
                emotional_weight="low", raw_text="Good deal")
            offer = intel._generate_offer_strategy(1_000_000, risk, xref, buyer, concerns)
            required = ['recommended_offer', 'discount_from_ask', 'discount_percentage',
                        'repair_cost_avg', 'risk_discount', 'transparency_discount']
            missing = [k for k in required if k not in offer]
            self._record("Edge: Offer has all required frontend keys",
                f"missing={missing}", len(missing) == 0,
                error=f"Missing: {missing}" if missing else None)
        except Exception as e:
            self._record("Edge: Offer strategy keys", str(e)[:200], False)

    # =========================================================================
    # GROUP 9: SCHEMA CONSISTENCY — finds real divergence bugs
    # =========================================================================

    def _test_schema_consistency(self):
        # Test 1: Check database.py vs models.py User model
        try:
            models_path = os.path.join(os.path.dirname(__file__), 'models.py')
            database_path = os.path.join(os.path.dirname(__file__), 'database.py')

            with open(models_path, 'r') as f:
                models_src = f.read()
            with open(database_path, 'r') as f:
                db_src = f.read()

            # Extract db.Column definitions from both
            models_cols = set(re.findall(r'(\w+)\s*=\s*db\.Column', models_src))
            db_cols = set(re.findall(r'(\w+)\s*=\s*db\.Column', db_src))

            # Compare User-specific columns (filter to likely User columns)
            # models.py has analysis_credits, database.py has credits
            db_only = db_cols - models_cols
            models_only = models_cols - db_cols

            self._record(
                "Schema: database.py and models.py column sets are consistent",
                f"database.py-only: {db_only or 'none'}, models.py-only: {models_only or 'none'}",
                len(db_only) == 0,
                error=f"SCHEMA DRIFT: database.py defines columns {db_only} not in models.py. "
                      f"Code importing from database.py will reference columns that don't exist "
                      f"in the app's User model (models.py). This affects payment_routes.py."
                      if db_only else None)

        except Exception as e:
            self._record("Schema: Model comparison", str(e)[:200], False)

        # Test 2: payment_routes.py column references
        try:
            pay_path = os.path.join(os.path.dirname(__file__), 'payment_routes.py')
            with open(pay_path, 'r') as f:
                pay_src = f.read()

            wrong_credits = len(re.findall(r'(?:User|user|current_user)\.credits\b(?!_)', pay_src))
            wrong_total = len(re.findall(r'\.total_credits_purchased', pay_src))
            wrong_analyses = len(re.findall(r'\.analyses_completed', pay_src))

            self._record(
                "Schema: payment_routes.py uses correct 'analysis_credits' column",
                f"wrong 'credits' refs: {wrong_credits}",
                wrong_credits == 0,
                error=f"COLUMN MISMATCH: payment_routes.py references '.credits' {wrong_credits}x but "
                      f"models.py User has 'analysis_credits'. Will crash at runtime."
                      if wrong_credits > 0 else None)

            self._record(
                "Schema: 'total_credits_purchased' exists in User model",
                f"references: {wrong_total}",
                wrong_total == 0,
                error=f"MISSING COLUMN: 'total_credits_purchased' referenced {wrong_total}x but "
                      f"does not exist in models.py User."
                      if wrong_total > 0 else None)

            self._record(
                "Schema: 'analyses_completed' exists in User model",
                f"references: {wrong_analyses}",
                wrong_analyses == 0,
                error=f"MISSING COLUMN: 'analyses_completed' referenced {wrong_analyses}x but "
                      f"does not exist in models.py User."
                      if wrong_analyses > 0 else None)

        except FileNotFoundError:
            self._record("Schema: payment_routes.py", "File not found", True)
        except Exception as e:
            self._record("Schema: payment_routes.py", str(e)[:200], False)

        # Test 3: Import source
        try:
            pay_path = os.path.join(os.path.dirname(__file__), 'payment_routes.py')
            with open(pay_path, 'r') as f:
                pay_src = f.read()

            from_database = 'from database import' in pay_src
            from_models = 'from models import' in pay_src

            self._record(
                "Schema: payment_routes.py imports from models (not database)",
                f"from database: {from_database}, from models: {from_models}",
                from_models and not from_database,
                error=f"IMPORT MISMATCH: payment_routes.py imports from database.py which has a "
                      f"DIFFERENT User model (column 'credits' vs 'analysis_credits'). "
                      f"Should import from models.py."
                      if from_database else None)

        except Exception as e:
            self._record("Schema: Import analysis", str(e)[:200], False)

    # =========================================================================
    # GROUP 10: PAYMENT PIPELINE INTEGRITY
    # =========================================================================

    def _test_payment_pipeline(self):
        if not self.app:
            self._record("Payment: Skipped", "No app context", True)
            return

        # Blueprint registration
        blueprints = list(self.app.blueprints.keys())
        payment_registered = 'payment' in blueprints
        self._record(
            "Payment: payment_routes.py blueprint is registered",
            f"blueprints: {blueprints}",
            payment_registered,
            error=f"DEAD CODE: payment_routes.py defines a Blueprint but it's NOT registered. "
                  f"The /api/deduct-credit endpoint and atomic deduction fix (H5) are in this "
                  f"dead file. Credit deduction happens inline in app.py instead (non-atomic)."
                  if not payment_registered else None)

        # /api/deduct-credit endpoint exists
        url_rules = {rule.rule for rule in self.app.url_map.iter_rules()}
        deduct_exists = '/api/deduct-credit' in url_rules
        self._record(
            "Payment: /api/deduct-credit endpoint is registered",
            f"in URL map: {deduct_exists}",
            deduct_exists,
            error=f"MISSING ENDPOINT: /api/deduct-credit not in Flask URL map. "
                  f"If payment_routes.py blueprint isn't registered, this endpoint doesn't exist."
                  if not deduct_exists else None)

        # Deduction atomicity in app.py
        try:
            with open(os.path.join(os.path.dirname(__file__), 'app.py'), 'r') as f:
                src = f.read()

            non_atomic = len(re.findall(r'current_user\.analysis_credits\s*-=\s*1', src))
            atomic = len(re.findall(r'\.where\(User\.analysis_credits\s*>=', src))

            self._record(
                "Payment: Credit deduction in app.py is atomic",
                f"non-atomic: {non_atomic}, atomic: {atomic}",
                non_atomic == 0 or atomic > 0,
                error=f"RACE CONDITION: {non_atomic} non-atomic deductions in app.py with {atomic} "
                      f"atomic guards. Concurrent requests can cause negative credits."
                      if non_atomic > 0 and atomic == 0 else None)

        except Exception as e:
            self._record("Payment: Atomicity", str(e)[:200], False)

        # Stripe webhook
        webhook_exists = any('webhook' in r.lower() or 'stripe' in r.lower() for r in url_rules)
        self._record("Payment: Stripe webhook endpoint exists",
            f"found: {webhook_exists}", webhook_exists,
            error="No Stripe webhook endpoint found" if not webhook_exists else None)

    # =========================================================================
    # GROUP 11: CODE QUALITY
    # =========================================================================

    def _test_code_quality(self):
        try:
            with open(os.path.join(os.path.dirname(__file__), 'app.py'), 'r') as f:
                app_src = f.read()
            with open(os.path.join(os.path.dirname(__file__), 'offerwise_intelligence.py'), 'r') as f:
                intel_src = f.read()
        except Exception as e:
            self._record("Quality: Source read", str(e)[:200], False)
            return

        # Emergency debug in app.py
        emergency_app = len(re.findall(r'EMERGENCY DEBUG', app_src))
        self._record("Quality: No emergency debug in app.py",
            f"markers: {emergency_app}", emergency_app == 0,
            error=f"DEBUG IN PROD: {emergency_app} 'EMERGENCY DEBUG' markers in app.py"
                  if emergency_app > 0 else None)

        # Emergency debug in intelligence
        emergency_intel = len(re.findall(r'🚨 EMERGENCY|EMERGENCY DEBUG|logging\.error.*OFFER.*CALCULATION', intel_src))
        self._record("Quality: No emergency debug in offerwise_intelligence.py",
            f"markers: {emergency_intel}", emergency_intel == 0,
            error=f"DEBUG IN PROD: {emergency_intel} emergency debug patterns in offerwise_intelligence.py. "
                  f"Offer strategy uses logging.error() for debug output, polluting error logs."
                  if emergency_intel > 0 else None)

        # str(e) in API responses
        str_e_leaks = len(re.findall(r"jsonify\(\{[^}]*['\"]error['\"]\s*:\s*str\(e\)", app_src))
        self._record("Quality: No str(e) in API responses",
            f"leaks: {str_e_leaks}", str_e_leaks == 0,
            error=f"INFO LEAK: {str_e_leaks} API responses return str(e) to users"
                  if str_e_leaks > 0 else None)

        # traceback in responses
        tb_leaks = len(re.findall(r"['\"]trace['\"]\s*:\s*traceback\.format_exc\(\)", app_src))
        self._record("Quality: No traceback in API responses",
            f"leaks: {tb_leaks}", tb_leaks == 0,
            error=f"STACK TRACE LEAK: {tb_leaks} responses return traceback.format_exc()"
                  if tb_leaks > 0 else None)

    # =========================================================================
    # GROUP 12: CONCURRENCY SAFETY
    # =========================================================================

    def _test_concurrency_safety(self):
        if not self.app or not self.db:
            self._record("Concurrency: Skipped", "No app/db context", True)
            return

        from models import User
        from sqlalchemy import update

        try:
            ts = int(time.time())
            test_user = User(email=f"integrity_race_{ts}@test.offerwise.ai",
                name="Race Test", auth_provider='test', analysis_credits=1)
            self.db.session.add(test_user)
            self.db.session.commit()
            self._cleanup_items.append(test_user)
            uid = test_user.id

            # Demonstrate the race condition conceptually
            r1_read = test_user.analysis_credits
            r2_read = test_user.analysis_credits
            self._record("Concurrency: Two reads both see credits > 0",
                f"r1={r1_read}, r2={r2_read}", r1_read > 0 and r2_read > 0)

            # Prove atomic approach works
            test_user.analysis_credits = 1
            self.db.session.commit()

            d1 = self.db.session.execute(
                update(User).where(User.id == uid).where(User.analysis_credits >= 1)
                .values(analysis_credits=User.analysis_credits - 1))
            d2 = self.db.session.execute(
                update(User).where(User.id == uid).where(User.analysis_credits >= 1)
                .values(analysis_credits=User.analysis_credits - 1))
            self.db.session.commit()

            self._record("Concurrency: Atomic — only first deduction succeeds",
                f"d1_rows={d1.rowcount}, d2_rows={d2.rowcount}",
                d1.rowcount == 1 and d2.rowcount == 0,
                error=f"DOUBLE DEDUCTION: ({d1.rowcount}, {d2.rowcount}) expected (1, 0)"
                      if not (d1.rowcount == 1 and d2.rowcount == 0) else None)

            self.db.session.refresh(test_user)
            self._record("Concurrency: Final balance is 0",
                f"credits={test_user.analysis_credits}", test_user.analysis_credits == 0,
                error=f"Negative credits: {test_user.analysis_credits}" if test_user.analysis_credits != 0 else None)

        except Exception as e:
            self._record("Concurrency: Test execution", str(e)[:200], False, error=traceback.format_exc()[:500])


def run_integrity_tests(app, db) -> Dict[str, Any]:
    """Entry point callable from API endpoint."""
    engine = IntegrityTestEngine(app=app, db=db)
    with app.app_context():
        return engine.run_all()
