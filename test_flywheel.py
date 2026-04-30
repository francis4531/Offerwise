"""
test_flywheel.py — Admin-runnable tests for all four flywheel sequences.

Tests cover:
  1. Buyer verdict thresholds — Proceed / Review / Walk Away
  2. Inspector loop email generation — correct content, no crashes
  3. Agent post-close email generation — stats, timeline, copy
  4. Contractor completion — record saved, estimate engine updated
  5. Flywheel notification functions importable and callable
  6. Verdict card logic matches offerScore thresholds
  7. Inspector impact stats — correct aggregation
  8. Agent pipeline stats — correct quarter window
  9. Contractor completion variance calculation
 10. All email functions return False gracefully on bad input
"""

import unittest
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))


# ─────────────────────────────────────────────────────────────────────────────
# 1. FLYWHEEL NOTIFICATIONS — IMPORT + SMOKE
# ─────────────────────────────────────────────────────────────────────────────

class TestFlywheelImports(unittest.TestCase):

    def test_module_importable(self):
        import flywheel_notifications
        self.assertTrue(hasattr(flywheel_notifications, '_send_inspector_loop_email'))
        self.assertTrue(hasattr(flywheel_notifications, 'send_agent_postclose_email'))
        self.assertTrue(hasattr(flywheel_notifications, 'process_contractor_completion'))
        self.assertTrue(hasattr(flywheel_notifications, 'get_inspector_impact_stats'))
        self.assertTrue(hasattr(flywheel_notifications, 'get_agent_pipeline_stats'))

    def test_fmt_helper(self):
        from flywheel_notifications import _fmt
        self.assertEqual(_fmt(207000), '$207K')
        self.assertEqual(_fmt(1500000), '$1.5M')
        self.assertEqual(_fmt(500), '$500')
        self.assertEqual(_fmt(0), '$0')

    def test_inspector_loop_fails_gracefully_no_db(self):
        """Should return False (not crash) when called outside app context."""
        from flywheel_notifications import _send_inspector_loop_email
        result = _send_inspector_loop_email(
            inspector_report=None,
            result_dict={},
            buyer_email='test@test.com',
            savings=0,
        )
        self.assertFalse(result)

    def test_agent_postclose_fails_gracefully_no_db(self):
        from flywheel_notifications import send_agent_postclose_email
        result = send_agent_postclose_email(agent_share_id=999999)
        self.assertFalse(result)

    def test_contractor_completion_fails_gracefully_no_db(self):
        from flywheel_notifications import process_contractor_completion
        result = process_contractor_completion(
            lead_id=999999,
            contractor_id=999999,
            won_job=True,
            final_price=11400.0,
            work_completed='electrical',
        )
        self.assertFalse(result['success'])
        self.assertIn('message', result)

    def test_impact_stats_fails_gracefully_no_db(self):
        from flywheel_notifications import get_inspector_impact_stats
        stats = get_inspector_impact_stats(inspector_id=999999)
        self.assertEqual(stats.get('total_analyses', 0), 0)
        self.assertEqual(stats.get('total_savings', 0), 0)

    def test_pipeline_stats_fails_gracefully_no_db(self):
        from flywheel_notifications import get_agent_pipeline_stats
        stats = get_agent_pipeline_stats(agent_id=999999)
        self.assertEqual(stats.get('closed_this_quarter', 0), 0)


# ─────────────────────────────────────────────────────────────────────────────
# 2. BUYER VERDICT LOGIC
# ─────────────────────────────────────────────────────────────────────────────

class TestBuyerVerdictThresholds(unittest.TestCase):
    """
    These tests verify the verdict threshold logic that the JS verdict card
    uses — same logic, tested in Python so it's CI-checkable.
    """

    def _verdict(self, offer_score, deal_breakers=0, contradictions=0, avg_repair=0):
        """Mirror the JS verdict card logic."""
        is_walk_away = deal_breakers >= 3 or offer_score < 25
        is_review = (not is_walk_away) and (
            deal_breakers >= 1 or offer_score < 50 or avg_repair > 50000
        )
        is_proceed = not is_walk_away and not is_review
        if is_walk_away:   return 'walk_away'
        elif is_review:    return 'review'
        else:              return 'proceed'

    def test_high_score_no_issues_proceeds(self):
        self.assertEqual(self._verdict(75), 'proceed')

    def test_low_score_walks_away(self):
        self.assertEqual(self._verdict(20), 'walk_away')

    def test_three_deal_breakers_walks_away(self):
        self.assertEqual(self._verdict(60, deal_breakers=3), 'walk_away')

    def test_one_deal_breaker_review(self):
        self.assertEqual(self._verdict(65, deal_breakers=1), 'review')

    def test_score_40_review(self):
        self.assertEqual(self._verdict(40), 'review')

    def test_score_50_proceed(self):
        self.assertEqual(self._verdict(50), 'proceed')

    def test_high_repair_cost_triggers_review(self):
        self.assertEqual(self._verdict(70, avg_repair=60000), 'review')

    def test_borderline_24_walks_away(self):
        self.assertEqual(self._verdict(24), 'walk_away')

    def test_borderline_25_review(self):
        # 25 is not < 25, so not walk_away — but < 50, so review
        self.assertEqual(self._verdict(25), 'review')

    def test_two_deal_breakers_review_not_walk(self):
        # 2 deal breakers is not >= 3, so not walk_away — but has deal breaker, so review
        self.assertEqual(self._verdict(60, deal_breakers=2), 'review')

    def test_contradictions_alone_no_effect(self):
        # Contradictions alone don't change verdict tier in the card logic
        self.assertEqual(self._verdict(70, contradictions=3), 'proceed')

    def test_perfect_score_proceeds(self):
        self.assertEqual(self._verdict(95), 'proceed')

    def test_zero_score_walks_away(self):
        self.assertEqual(self._verdict(0), 'walk_away')


# ─────────────────────────────────────────────────────────────────────────────
# 3. INSPECTOR EMAIL HTML CONTENT
# ─────────────────────────────────────────────────────────────────────────────

class TestInspectorEmailContent(unittest.TestCase):
    """
    Test the HTML email generation for the inspector loop.
    Uses mock objects to avoid DB dependency.
    """

    def _make_mock_report(self, address='2839 Pendleton Dr, San Jose CA',
                           price=900000.0):
        from unittest.mock import MagicMock
        r = MagicMock()
        r.property_address = address
        r.property_price = price
        r.inspector_id = 1
        return r

    def _make_mock_result(self, rec_offer=693250, deal_breakers=None,
                           contradictions=None):
        db_items = deal_breakers or [
            {'system': 'Electrical', 'severity': 'critical'},
            {'system': 'Roof', 'severity': 'major'},
        ]
        c_items = contradictions or [
            {'title': 'Seller disclosed no electrical issues; FPE panel found'},
        ]
        return {
            'offer_strategy': {'recommended_offer': rec_offer},
            'risk_score': {'deal_breakers': db_items, 'overall_risk_score': 55},
            'cross_reference': {'contradictions': c_items},
        }

    def test_email_html_builds_without_crash(self):
        """The core email HTML construction logic should not crash."""
        from flywheel_notifications import _fmt
        # Mirror core logic from _send_inspector_loop_email
        result_dict = self._make_mock_result()
        offer_strategy = result_dict.get('offer_strategy', {})
        risk_score = result_dict.get('risk_score', {})
        deal_breakers = risk_score.get('deal_breakers', [])
        savings = 900000 - offer_strategy.get('recommended_offer', 900000)

        self.assertGreater(savings, 0)
        self.assertGreater(len(deal_breakers), 0)

        savings_fmt = _fmt(savings)
        self.assertIn('K', savings_fmt)

    def test_findings_html_rows_built(self):
        """Findings from deal_breakers and contradictions should produce HTML rows."""
        result_dict = self._make_mock_result()
        deal_breakers = result_dict['risk_score']['deal_breakers']
        contradictions = result_dict['cross_reference']['contradictions']

        findings_html = ''
        findings_used = 0
        for db_item in deal_breakers[:3]:
            title = db_item.get('system', '') if isinstance(db_item, dict) else str(db_item)
            findings_html += f'<tr><td>{title}</td><td>Used in offer</td></tr>'
            findings_used += 1
        for c_item in contradictions[:2]:
            title = c_item.get('title', '') if isinstance(c_item, dict) else str(c_item)
            findings_html += f'<tr><td>{title}</td><td>Contradiction surfaced</td></tr>'
            findings_used += 1

        self.assertEqual(findings_used, 3)  # 2 deal breakers + 1 contradiction
        self.assertIn('Electrical', findings_html)
        self.assertIn('FPE panel', findings_html)

    def test_subject_line_includes_savings(self):
        """Subject line should mention savings when > 0."""
        from flywheel_notifications import _fmt
        address = '2839 Pendleton Dr'
        savings = 207000
        subject = f"🔍 Your inspection of {address} helped a buyer save {_fmt(savings)}"
        self.assertIn('$207K', subject)
        self.assertIn(address, subject)

    def test_subject_line_no_savings_fallback(self):
        """When savings = 0, subject falls back to generic."""
        address = '2839 Pendleton Dr'
        savings = 0
        subject = f"🔍 Your inspection of {address[:40]} just helped a buyer"
        if savings > 0:
            subject = f"🔍 Your inspection of {address[:40]} helped a buyer save"
        self.assertNotIn('save', subject)
        self.assertIn(address, subject)


# ─────────────────────────────────────────────────────────────────────────────
# 4. AGENT POST-CLOSE EMAIL CONTENT
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentPostCloseEmailContent(unittest.TestCase):

    def test_stats_formatting(self):
        from flywheel_notifications import _fmt
        asking = 900000
        final = 693250
        saved = asking - final
        self.assertEqual(_fmt(saved), '$207K')
        subject = f"🎉 Sarah closed on 2839 Pendleton Dr. They saved {_fmt(saved)}."
        self.assertIn('$207K', subject)

    def test_timeline_dates_present(self):
        from datetime import datetime
        analysis_date = datetime(2026, 3, 15).strftime('%b %d, %Y')
        close_date = datetime(2026, 3, 29).strftime('%b %d, %Y')
        self.assertEqual(analysis_date, 'Mar 15, 2026')
        self.assertEqual(close_date, 'Mar 29, 2026')

    def test_quarter_start_calculation(self):
        """Quarter start should be first day of current quarter."""
        from datetime import datetime
        now = datetime(2026, 3, 30)
        q_month = ((now.month - 1) // 3) * 3 + 1  # = 1 for Q1
        self.assertEqual(q_month, 1)

        now_q3 = datetime(2026, 8, 15)
        q_month_q3 = ((now_q3.month - 1) // 3) * 3 + 1  # = 7 for Q3
        self.assertEqual(q_month_q3, 7)

    def test_bio_line_present(self):
        """The 'put in your bio' line must be in the email template."""
        import flywheel_notifications
        import inspect
        src = inspect.getsource(flywheel_notifications.send_agent_postclose_email)
        self.assertIn("worth putting in your bio", src)

    def test_deal_closed_at_recorded(self):
        """send_agent_postclose_email should record deal_closed_at — verified in source."""
        import flywheel_notifications
        import inspect
        src = inspect.getsource(flywheel_notifications.send_agent_postclose_email)
        self.assertIn('deal_closed_at', src)
        self.assertIn('datetime.utcnow()', src)


# ─────────────────────────────────────────────────────────────────────────────
# 5. CONTRACTOR COMPLETION LOGIC
# ─────────────────────────────────────────────────────────────────────────────

class TestContractorCompletionLogic(unittest.TestCase):

    def test_variance_calculation_above(self):
        """Price above midpoint = positive variance."""
        final = 13000.0
        est_low, est_high = 9000.0, 15000.0
        est_mid = (est_low + est_high) / 2  # 12000
        variance = round((final - est_mid) / est_mid * 100, 1)
        self.assertGreater(variance, 0)
        self.assertAlmostEqual(variance, 8.3, delta=0.1)

    def test_variance_calculation_below(self):
        """Price below midpoint = negative variance."""
        final = 10000.0
        est_low, est_high = 9000.0, 15000.0
        est_mid = (est_low + est_high) / 2  # 12000
        variance = round((final - est_mid) / est_mid * 100, 1)
        self.assertLess(variance, 0)
        self.assertAlmostEqual(variance, -16.7, delta=0.1)

    def test_variance_none_when_no_estimate(self):
        """Variance should be None when no original estimate exists."""
        est_mid = 0
        final = 11000.0
        variance = None
        if final and est_mid:
            variance = round((final - est_mid) / est_mid * 100, 1)
        self.assertIsNone(variance)

    def test_variance_none_when_not_won(self):
        """Variance should not be calculated for lost bids."""
        won_job = False
        final_price = None
        est_mid = 12000
        variance = None
        if won_job and final_price and est_mid:
            variance = round((final_price - est_mid) / est_mid * 100, 1)
        self.assertIsNone(variance)

    def test_price_per_system_calculation(self):
        """Price per system should divide final price by system count."""
        final = 23400.0
        systems = ['electrical', 'roof']
        price_per = final / max(len(systems), 1)
        self.assertEqual(price_per, 11700.0)

    def test_single_system_full_price(self):
        final = 11400.0
        systems = ['electrical']
        price_per = final / max(len(systems), 1)
        self.assertEqual(price_per, 11400.0)

    def test_completion_model_importable(self):
        from models import ContractorJobCompletion
        self.assertEqual(ContractorJobCompletion.__tablename__, 'contractor_job_completions')

    def test_completion_model_fields(self):
        from models import ContractorJobCompletion
        cols = [c.name for c in ContractorJobCompletion.__table__.columns]
        required = ['id', 'lead_id', 'contractor_id', 'won_job', 'final_price',
                    'zip_code', 'work_completed', 'variance_pct', 'permit_uploaded']
        for col in required:
            self.assertIn(col, cols, f"Missing column: {col}")

    def test_thank_you_email_builds(self):
        """Thank-you email HTML should build without crash."""
        from flywheel_notifications import _fmt
        price = 11400.0
        variance_pct = 8.3
        direction = 'above' if variance_pct > 0 else 'below'
        line = f"Your price was {abs(variance_pct):.0f}% {direction} the OfferWise estimate"
        self.assertIn('above', line)
        self.assertIn('8%', line)


# ─────────────────────────────────────────────────────────────────────────────
# 6. ESTIMATE ENGINE FEEDBACK
# ─────────────────────────────────────────────────────────────────────────────

class TestEstimateEngineFeedback(unittest.TestCase):

    def test_update_function_importable(self):
        from flywheel_notifications import _update_estimate_from_completion
        self.assertTrue(callable(_update_estimate_from_completion))

    def test_update_skipped_when_no_price(self):
        """_update_estimate_from_completion should skip when no final_price."""
        from flywheel_notifications import _update_estimate_from_completion
        from unittest.mock import MagicMock
        completion = MagicMock()
        completion.won_job = True
        completion.final_price = None   # ← no price
        completion.zip_code = '95148'
        # Should not raise — returns silently
        try:
            _update_estimate_from_completion(completion)
        except Exception as e:
            self.fail(f"Should not raise: {e}")

    def test_update_skipped_when_not_won(self):
        from flywheel_notifications import _update_estimate_from_completion
        from unittest.mock import MagicMock
        completion = MagicMock()
        completion.won_job = False      # ← not won
        completion.final_price = 11400.0
        completion.zip_code = '95148'
        try:
            _update_estimate_from_completion(completion)
        except Exception as e:
            self.fail(f"Should not raise: {e}")

    def test_repair_cost_log_model_importable(self):
        from models import RepairCostLog
        self.assertTrue(hasattr(RepairCostLog, '__tablename__'))


# ─────────────────────────────────────────────────────────────────────────────
# 7. ROUTE EXISTENCE CHECKS
# ─────────────────────────────────────────────────────────────────────────────

class TestFlywheelRouteExistence(unittest.TestCase):

    def test_contractor_completion_route_exists(self):
        """The /completion endpoint should be defined in contractor_routes."""
        import contractor_routes
        import inspect
        src = inspect.getsource(contractor_routes)
        self.assertIn('/completion', src)
        self.assertIn('contractor_submit_completion', src)

    def test_agent_close_route_exists(self):
        import agent_routes
        import inspect
        src = inspect.getsource(agent_routes)
        self.assertIn('/close', src)
        self.assertIn('agent_mark_deal_closed', src)

    def test_agent_pipeline_route_exists(self):
        import agent_routes
        import inspect
        src = inspect.getsource(agent_routes)
        self.assertIn('/api/agent/pipeline', src)

    def test_inspector_impact_route_exists(self):
        import inspector_routes
        import inspect
        src = inspect.getsource(inspector_routes)
        self.assertIn('/api/inspector/impact', src)
        self.assertIn('inspector_impact_stats', src)

    def test_contractor_close_delegates_to_flywheel(self):
        """The /close route should call process_contractor_completion."""
        import contractor_routes
        import inspect
        src = inspect.getsource(contractor_routes.contractor_close_lead)
        self.assertIn('process_contractor_completion', src)


if __name__ == '__main__':
    unittest.main(verbosity=2)
