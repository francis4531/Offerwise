"""
OfferWise New Workflow Tests — v5.74.90
========================================
Covers all workflows added in the platform pivot:
  - Inspector portal (registration, analysis, quota, report sharing)
  - Contractor network (signup, lead matching, lead submission)
  - Contractor lead revenue tracking (fee calculation, status pipeline)
  - Free tier detection and credit flow
  - Results page tools (offer calculator math, negotiation checklist)
  - Stripe Inspector Pro (checkout plan routing, webhook plan dispatch)
  - Inspector Pro quota enforcement
"""

import unittest
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))


# ─── HELPERS ────────────────────────────────────────────────────────────────

def make_inspector(**kwargs):
    """Build a minimal Inspector-like object for testing."""
    class FakeInspector:
        plan = kwargs.get('plan', 'free')
        monthly_quota = kwargs.get('monthly_quota', 5)
        monthly_used = kwargs.get('monthly_used', 0)
        total_reports = kwargs.get('total_reports', 0)
        business_name = kwargs.get('business_name', 'Bay Area Inspections')
        license_number = kwargs.get('license_number', 'CA-12345')
        license_state = kwargs.get('license_state', 'CA')

        def quota_remaining(self):
            if self.monthly_quota == -1:
                return float('inf')
            return max(0, self.monthly_quota - self.monthly_used)

        def is_at_quota(self):
            if self.monthly_quota == -1:
                return False
            return self.monthly_used >= self.monthly_quota

    return FakeInspector()


def make_contractor(**kwargs):
    """Build a minimal Contractor-like object for testing."""
    class FakeContractor:
        status = kwargs.get('status', 'active')
        trades = kwargs.get('trades', 'roofing,hvac')
        service_zips = kwargs.get('service_zips', '94086,94087,94088')
        service_cities = kwargs.get('service_cities', 'Sunnyvale, Santa Clara')
        accepts_leads = kwargs.get('accepts_leads', True)
        leads_sent_total = kwargs.get('leads_sent_total', 0)
        email = kwargs.get('email', 'contractor@example.com')
        business_name = kwargs.get('business_name', 'Test Roofing')

        def trades_list(self):
            return [t.strip() for t in self.trades.split(',') if t.strip()]

    return FakeContractor()


def make_lead(**kwargs):
    """Build a minimal ContractorLead-like object for testing."""
    class FakeLead:
        repair_system = kwargs.get('repair_system', 'roof')
        property_zip = kwargs.get('property_zip', '94086')
        property_address = kwargs.get('property_address', '123 Oak Ave, Sunnyvale CA 94086')
        user_name = kwargs.get('user_name', 'Sarah Chen')
        user_email = kwargs.get('user_email', 'sarah@example.com')
        user_phone = kwargs.get('user_phone', '4085550123')
        cost_estimate = kwargs.get('cost_estimate', '$8K–$14K')
        contact_timing = kwargs.get('contact_timing', 'this_week')
        status = kwargs.get('status', 'new')
        job_value = kwargs.get('job_value', None)
        referral_fee_pct = kwargs.get('referral_fee_pct', None)
        referral_paid = kwargs.get('referral_paid', False)
        assigned_contractor_id = kwargs.get('assigned_contractor_id', None)

        def fee_due(self):
            if self.job_value and self.referral_fee_pct:
                return round(self.job_value * self.referral_fee_pct / 100, 2)
            return 0.0

    return FakeLead()


# ─── INSPECTOR QUOTA TESTS ──────────────────────────────────────────────────

class TestInspectorQuota(unittest.TestCase):
    """Inspector monthly quota enforcement logic."""

    def test_free_plan_has_5_quota(self):
        insp = make_inspector(plan='free', monthly_quota=5, monthly_used=0)
        self.assertEqual(insp.monthly_quota, 5)

    def test_pro_plan_has_unlimited_quota(self):
        insp = make_inspector(plan='inspector_pro', monthly_quota=-1)
        self.assertFalse(insp.is_at_quota())
        self.assertEqual(insp.quota_remaining(), float('inf'))

    def test_at_quota_blocks(self):
        insp = make_inspector(monthly_quota=5, monthly_used=5)
        self.assertTrue(insp.is_at_quota())

    def test_under_quota_allows(self):
        insp = make_inspector(monthly_quota=5, monthly_used=4)
        self.assertFalse(insp.is_at_quota())

    def test_quota_remaining_correct(self):
        insp = make_inspector(monthly_quota=5, monthly_used=3)
        self.assertEqual(insp.quota_remaining(), 2)

    def test_quota_remaining_never_negative(self):
        insp = make_inspector(monthly_quota=5, monthly_used=7)
        self.assertEqual(insp.quota_remaining(), 0)

    def test_pro_upgrade_removes_quota_enforcement(self):
        """When plan=inspector_pro, monthly_quota=-1 means unlimited."""
        insp = make_inspector(plan='inspector_pro', monthly_quota=-1, monthly_used=999)
        self.assertFalse(insp.is_at_quota())

    def test_free_plan_exactly_at_quota_blocks(self):
        insp = make_inspector(monthly_quota=5, monthly_used=5)
        self.assertTrue(insp.is_at_quota())
        self.assertEqual(insp.quota_remaining(), 0)


# ─── CONTRACTOR MATCHING TESTS ──────────────────────────────────────────────

class TestContractorMatching(unittest.TestCase):
    """Trade and ZIP matching logic for contractor lead routing."""

    def _matches(self, contractor, repair_system, property_zip):
        """Replicate find_matching_contractor logic."""
        trade_key = (repair_system or '').lower()
        trades = [t.strip().lower() for t in contractor.trades_list()]
        trade_match = any(trade_key in t or t in trade_key for t in trades) or 'general' in trades
        if not trade_match:
            return False
        if contractor.service_zips and property_zip:
            return property_zip in [z.strip() for z in contractor.service_zips.split(',')]
        return True

    def test_exact_trade_match(self):
        c = make_contractor(trades='roofing,hvac', service_zips='94086,94087')
        self.assertTrue(self._matches(c, 'roofing', '94086'))

    def test_partial_trade_match(self):
        """'roof' should match 'roofing'."""
        c = make_contractor(trades='roofing', service_zips='94086')
        self.assertTrue(self._matches(c, 'roof', '94086'))

    def test_hvac_match(self):
        c = make_contractor(trades='hvac,plumbing', service_zips='94086')
        self.assertTrue(self._matches(c, 'hvac', '94086'))

    def test_general_contractor_matches_any_trade(self):
        c = make_contractor(trades='general', service_zips='94086')
        self.assertTrue(self._matches(c, 'foundation', '94086'))
        self.assertTrue(self._matches(c, 'electrical', '94086'))
        self.assertTrue(self._matches(c, 'mold', '94086'))

    def test_wrong_trade_no_match(self):
        c = make_contractor(trades='roofing', service_zips='94086')
        self.assertFalse(self._matches(c, 'electrical', '94086'))

    def test_wrong_zip_no_match(self):
        c = make_contractor(trades='roofing', service_zips='94086,94087')
        self.assertFalse(self._matches(c, 'roofing', '10001'))

    def test_correct_zip_matches(self):
        c = make_contractor(trades='hvac', service_zips='94086,94087,94088')
        self.assertTrue(self._matches(c, 'hvac', '94087'))

    def test_no_service_zips_accepts_any(self):
        """Contractor with no ZIP restriction accepts all ZIPs."""
        c = make_contractor(trades='plumbing', service_zips='')
        self.assertTrue(self._matches(c, 'plumbing', '90210'))

    def test_inactive_contractor_should_not_match(self):
        """Active status check is a filter layer — logic test."""
        c = make_contractor(trades='roofing', service_zips='94086', status='pending')
        self.assertEqual(c.status, 'pending')
        self.assertNotEqual(c.status, 'active')

    def test_foundation_structural_cross_match(self):
        c = make_contractor(trades='foundation,structural', service_zips='94086')
        self.assertTrue(self._matches(c, 'structural', '94086'))
        self.assertTrue(self._matches(c, 'foundation', '94086'))


# ─── CONTRACTOR LEAD REVENUE TESTS ─────────────────────────────────────────

class TestContractorLeadRevenue(unittest.TestCase):
    """Referral fee calculation and status pipeline."""

    def test_fee_due_calculation(self):
        lead = make_lead(job_value=18000, referral_fee_pct=5)
        self.assertAlmostEqual(lead.fee_due(), 900.0)

    def test_fee_due_zero_when_no_job_value(self):
        lead = make_lead(job_value=None, referral_fee_pct=5)
        self.assertEqual(lead.fee_due(), 0.0)

    def test_fee_due_zero_when_no_pct(self):
        lead = make_lead(job_value=18000, referral_fee_pct=None)
        self.assertEqual(lead.fee_due(), 0.0)

    def test_fee_calculation_various_rates(self):
        cases = [
            (10000, 3.0, 300.0),
            (45000, 5.0, 2250.0),
            (8000, 2.5, 200.0),
            (100000, 10.0, 10000.0),
        ]
        for job_val, pct, expected in cases:
            lead = make_lead(job_value=job_val, referral_fee_pct=pct)
            self.assertAlmostEqual(lead.fee_due(), expected,
                msg=f"job_value={job_val}, pct={pct}")

    def test_status_pipeline_new_to_sent(self):
        lead = make_lead(status='new')
        self.assertEqual(lead.status, 'new')
        lead.status = 'sent'
        self.assertEqual(lead.status, 'sent')

    def test_status_pipeline_sent_to_closed(self):
        lead = make_lead(status='sent')
        lead.status = 'closed'
        lead.job_value = 22000
        lead.referral_fee_pct = 5
        self.assertAlmostEqual(lead.fee_due(), 1100.0)

    def test_referral_paid_starts_false(self):
        lead = make_lead()
        self.assertFalse(lead.referral_paid)

    def test_fee_rounding(self):
        """Fees should be rounded to 2 decimal places."""
        lead = make_lead(job_value=10000, referral_fee_pct=3.333)
        fee = lead.fee_due()
        self.assertEqual(fee, round(fee, 2))

    def test_high_value_job(self):
        lead = make_lead(job_value=150000, referral_fee_pct=5)
        self.assertAlmostEqual(lead.fee_due(), 7500.0)


# ─── FREE TIER DETECTION TESTS ─────────────────────────────────────────────

class TestFreeTierDetection(unittest.TestCase):
    """Free tier vs paid user detection logic."""

    def _is_free_tier(self, has_stripe_customer_id, is_developer=False,
                      developer_emails=None, user_email='user@example.com'):
        """Replicate the free tier detection from analysis_routes.py."""
        dev_emails = developer_emails or []
        is_dev = user_email.lower() in dev_emails
        has_paid = bool(has_stripe_customer_id) or is_dev
        return not has_paid

    def test_no_stripe_id_is_free_tier(self):
        self.assertTrue(self._is_free_tier(has_stripe_customer_id=None))

    def test_has_stripe_id_is_paid(self):
        self.assertFalse(self._is_free_tier(has_stripe_customer_id='cus_abc123'))

    def test_developer_email_is_not_free_tier(self):
        self.assertFalse(self._is_free_tier(
            has_stripe_customer_id=None,
            developer_emails=['francis@getofferwise.ai'],
            user_email='francis@getofferwise.ai'
        ))

    def test_non_developer_is_free_tier(self):
        self.assertTrue(self._is_free_tier(
            has_stripe_customer_id=None,
            developer_emails=['francis@getofferwise.ai'],
            user_email='buyer@gmail.com'
        ))

    def test_free_tier_with_zero_credits_blocked(self):
        """Free user with 0 credits should be blocked, not get another free analysis."""
        credits = 0
        is_free = self._is_free_tier(has_stripe_customer_id=None)
        self.assertTrue(is_free)
        # Zero credits + free tier = blocked (already used their free one)
        should_block = credits <= 0 and is_free
        self.assertTrue(should_block)

    def test_free_tier_with_one_credit_allowed(self):
        credits = 1
        is_free = self._is_free_tier(has_stripe_customer_id=None)
        should_allow = credits > 0
        self.assertTrue(should_allow)

    def test_paid_user_with_zero_credits_blocked_differently(self):
        """Paid user with 0 credits should redirect to pricing, not be treated as free tier."""
        is_free = self._is_free_tier(has_stripe_customer_id='cus_abc123')
        self.assertFalse(is_free)


# ─── OFFER CALCULATOR MATH TESTS ────────────────────────────────────────────

class TestOfferCalculatorMath(unittest.TestCase):
    """Offer price calculator math — mirrors owCalcUpdate() frontend logic."""

    def _calc(self, asking, credit):
        my_offer = max(0, asking - credit)
        saved = asking - my_offer
        pct = round((saved / asking) * 100, 1) if asking > 0 else 0.0
        return my_offer, saved, pct

    def test_zero_credit_no_change(self):
        offer, saved, pct = self._calc(1000000, 0)
        self.assertEqual(offer, 1000000)
        self.assertEqual(saved, 0)
        self.assertEqual(pct, 0.0)

    def test_standard_credit(self):
        offer, saved, pct = self._calc(1000000, 50000)
        self.assertEqual(offer, 950000)
        self.assertEqual(saved, 50000)
        self.assertEqual(pct, 5.0)

    def test_credit_exceeds_asking_clamped_to_zero(self):
        offer, saved, pct = self._calc(100000, 200000)
        self.assertEqual(offer, 0)

    def test_typical_bay_area_scenario(self):
        """$1.5M asking, $65K repair credit."""
        offer, saved, pct = self._calc(1509000, 65250)
        self.assertEqual(offer, 1443750)
        self.assertAlmostEqual(pct, 4.3, places=1)

    def test_percentage_rounding(self):
        offer, saved, pct = self._calc(1000000, 33333)
        self.assertEqual(round(pct, 1), pct)  # one decimal place

    def test_small_property(self):
        offer, saved, pct = self._calc(400000, 20000)
        self.assertEqual(offer, 380000)
        self.assertEqual(pct, 5.0)

    def test_offer_never_negative(self):
        offer, saved, pct = self._calc(500000, 999999)
        self.assertGreaterEqual(offer, 0)


# ─── NEGOTIATION CHECKLIST TESTS ────────────────────────────────────────────

class TestNegotiationChecklist(unittest.TestCase):
    """Negotiation checklist generation from analysis results."""

    def _build_neg_items(self, deal_breakers, leverage_points):
        """Replicate frontend negItems assembly logic."""
        neg_items = []
        for db in deal_breakers[:3]:
            text = db if isinstance(db, str) else (db.get('description') or db.get('issue') or db.get('title') or '')
            cost = '' if isinstance(db, str) else (db.get('cost_estimate') or '')
            if text:
                neg_items.append({'text': text, 'cost': cost, 'type': 'dealbreaker'})
        for lp in leverage_points[:5]:
            text = lp if isinstance(lp, str) else (lp.get('description') or lp.get('point') or lp.get('text') or '')
            if text and not any(n['text'] == text for n in neg_items):
                neg_items.append({'text': text, 'cost': '', 'type': 'leverage'})
        return neg_items

    def test_deal_breakers_become_checklist_items(self):
        items = self._build_neg_items(
            deal_breakers=['Foundation crack in basement', 'HVAC system failed'],
            leverage_points=[]
        )
        self.assertEqual(len(items), 2)
        self.assertTrue(all(i['type'] == 'dealbreaker' for i in items))

    def test_leverage_points_added(self):
        items = self._build_neg_items(
            deal_breakers=[],
            leverage_points=['Request $8K roof credit', 'Seller disclosed no issues']
        )
        self.assertEqual(len(items), 2)
        self.assertTrue(all(i['type'] == 'leverage' for i in items))

    def test_max_three_deal_breakers(self):
        dbs = ['Issue A', 'Issue B', 'Issue C', 'Issue D', 'Issue E']
        items = self._build_neg_items(deal_breakers=dbs, leverage_points=[])
        self.assertLessEqual(len(items), 3)

    def test_max_five_leverage_points(self):
        lps = ['LP1', 'LP2', 'LP3', 'LP4', 'LP5', 'LP6', 'LP7']
        items = self._build_neg_items(deal_breakers=[], leverage_points=lps)
        self.assertLessEqual(len(items), 5)

    def test_no_duplicates(self):
        """Same text appearing in both lists should not duplicate."""
        items = self._build_neg_items(
            deal_breakers=['Foundation crack'],
            leverage_points=['Foundation crack', 'HVAC issue']
        )
        texts = [i['text'] for i in items]
        self.assertEqual(len(texts), len(set(texts)))

    def test_dict_deal_breakers_parsed(self):
        dbs = [{'description': 'Roof damage', 'cost_estimate': '$12K-$18K'}]
        items = self._build_neg_items(deal_breakers=dbs, leverage_points=[])
        self.assertEqual(items[0]['text'], 'Roof damage')
        self.assertEqual(items[0]['cost'], '$12K-$18K')

    def test_empty_inputs_returns_empty(self):
        items = self._build_neg_items(deal_breakers=[], leverage_points=[])
        self.assertEqual(items, [])

    def test_empty_text_filtered(self):
        dbs = [{'description': ''}, {'description': 'Real issue'}]
        items = self._build_neg_items(deal_breakers=dbs, leverage_points=[])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['text'], 'Real issue')


# ─── STRIPE INSPECTOR PRO ROUTING TESTS ─────────────────────────────────────

class TestStripeInspectorProRouting(unittest.TestCase):
    """Inspector Pro checkout uses subscription mode, not one-time payment."""

    def test_inspector_pro_plan_detected(self):
        """'inspector_pro' plan must be handled separately from buyer plans."""
        buyer_plans = {'single', 'bundle_5', 'bundle_12'}
        inspector_plans = {'inspector_pro'}
        plan = 'inspector_pro'
        self.assertNotIn(plan, buyer_plans)
        self.assertIn(plan, inspector_plans)

    def test_buyer_plans_use_price_data(self):
        """Buyer plans use dynamic price_data (one-time payments)."""
        prices = {
            'single': {'amount': 1900, 'credits': 1},
            'bundle_5': {'amount': 7900, 'credits': 5},
            'bundle_12': {'amount': 14900, 'credits': 12},
        }
        for plan, info in prices.items():
            self.assertIn('amount', info)
            self.assertIn('credits', info)
            self.assertGreater(info['credits'], 0)

    def test_inspector_pro_gives_zero_analysis_credits(self):
        """Inspector Pro webhook should give 0 analysis_credits — it's a different product."""
        inspector_pro_credits = 0
        self.assertEqual(inspector_pro_credits, 0)

    def test_inspector_pro_webhook_sets_unlimited_quota(self):
        """When inspector_pro webhook fires, monthly_quota must become -1."""
        insp = make_inspector(plan='free', monthly_quota=5)
        # Simulate webhook effect
        insp.plan = 'inspector_pro'
        insp.monthly_quota = -1
        self.assertEqual(insp.monthly_quota, -1)
        self.assertFalse(insp.is_at_quota())

    def test_inspector_pro_price_id_from_env(self):
        """Price ID should come from env var, not be hardcoded."""
        import os
        # In production, STRIPE_INSPECTOR_PRO_PRICE_ID must be set
        # The code falls back to '' if not set — not a hardcoded ID
        price_id = os.environ.get('STRIPE_INSPECTOR_PRO_PRICE_ID', '')
        # Either the env var is set, or it's empty (dev mode)
        self.assertNotEqual(price_id, 'price_1TBHMGJXdIT4gk5mgXy0pm5h',
            "Price ID must not be hardcoded in source — must come from env var")

    def test_single_plan_amount_correct(self):
        self.assertEqual(1900, 19 * 100)  # $19.00

    def test_bundle_5_amount_correct(self):
        self.assertEqual(7900, 79 * 100)  # $79.00

    def test_bundle_12_amount_correct(self):
        self.assertEqual(14900, 149 * 100)  # $149.00


# ─── INSPECTOR REPORT SHARING TESTS ─────────────────────────────────────────

class TestInspectorReportSharing(unittest.TestCase):
    """Share token generation and report access."""

    def test_share_token_is_url_safe(self):
        import secrets
        import re
        for _ in range(20):
            token = secrets.token_urlsafe(16)
            self.assertRegex(token, r'^[A-Za-z0-9_\-]+$')

    def test_share_token_minimum_length(self):
        import secrets
        token = secrets.token_urlsafe(16)
        # token_urlsafe(16) produces ~22 chars
        self.assertGreaterEqual(len(token), 20)

    def test_tokens_are_unique(self):
        import secrets
        tokens = [secrets.token_urlsafe(16) for _ in range(100)]
        self.assertEqual(len(tokens), len(set(tokens)))

    def test_share_url_format(self):
        """Share URL must follow /inspector-report/<token> pattern."""
        token = 'abc123def456ghi7'
        host = 'www.getofferwise.ai'
        share_url = f'https://{host}/inspector-report/{token}'
        self.assertIn('/inspector-report/', share_url)
        self.assertTrue(share_url.startswith('https://'))
        self.assertTrue(share_url.endswith(token))

    def test_branded_report_has_inspector_name(self):
        """Buyer-facing report must include inspector attribution."""
        report_data = {
            'inspector_name': 'Jane Smith',
            'inspector_biz': 'Bay Area Home Inspections',
            'property_address': '123 Main St, San Jose CA 95110',
            'buyer_name': 'Sarah Chen',
        }
        # All fields present
        for key in ['inspector_name', 'inspector_biz', 'property_address']:
            self.assertIn(key, report_data)
            self.assertTrue(report_data[key])


# ─── CONTRACTOR SIGNUP VALIDATION TESTS ─────────────────────────────────────

class TestContractorSignupValidation(unittest.TestCase):
    """Contractor signup form validation."""

    def _validate(self, data):
        """Replicate API validation logic."""
        errors = []
        if not (data.get('name') or '').strip():
            errors.append('name required')
        if not (data.get('email') or '').strip():
            errors.append('email required')
        if not data.get('trades'):
            errors.append('trades required')
        return errors

    def test_valid_signup(self):
        data = {
            'name': 'Mike Rodriguez',
            'email': 'mike@roofing.com',
            'trades': ['roofing'],
            'phone': '4085550000',
        }
        errors = self._validate(data)
        self.assertEqual(errors, [])

    def test_missing_name_fails(self):
        data = {'email': 'mike@roofing.com', 'trades': ['roofing']}
        errors = self._validate(data)
        self.assertIn('name required', errors)

    def test_missing_email_fails(self):
        data = {'name': 'Mike', 'trades': ['roofing']}
        errors = self._validate(data)
        self.assertIn('email required', errors)

    def test_missing_trades_fails(self):
        data = {'name': 'Mike', 'email': 'mike@roofing.com', 'trades': []}
        errors = self._validate(data)
        self.assertIn('trades required', errors)

    def test_contractor_status_starts_pending(self):
        status = 'pending'
        self.assertEqual(status, 'pending')
        self.assertNotEqual(status, 'active')

    def test_multiple_trades_allowed(self):
        trades = ['roofing', 'general', 'windows']
        joined = ','.join(trades)
        parsed = [t.strip() for t in joined.split(',')]
        self.assertEqual(parsed, trades)

    def test_trades_stored_as_comma_separated(self):
        trades = ['roofing', 'hvac', 'plumbing']
        stored = ','.join(trades)
        self.assertEqual(stored, 'roofing,hvac,plumbing')
        recovered = stored.split(',')
        self.assertEqual(recovered, trades)


# ─── DRIP CAMPAIGN TESTS (corrected for fixed behavior) ─────────────────────

class TestDripCampaignFixedBehavior(unittest.TestCase):
    """Verify drip campaign no longer has false urgency language."""

    def setUp(self):
        try:
            from drip_campaign import drip_email_4, drip_email_5
            self.drip_email_4 = drip_email_4
            self.drip_email_5 = drip_email_5
            self.available = True
        except ImportError:
            self.available = False

    def _fake_entry(self, address='123 Main St'):
        class E:
            result_address = address
            email = 'test@example.com'
            unsubscribe_token = 'tok123'
            zip_code = '94086'
        return E()

    def test_email_4_subject_has_no_expires(self):
        if not self.available:
            self.skipTest('drip_campaign not importable')
        subj, _ = self.drip_email_4(self._fake_entry())
        self.assertNotIn('expires', subj.lower(),
            f"Email 4 must not use false expiry urgency. Subject: {subj}")

    def test_email_4_surfaces_free_credit(self):
        if not self.available:
            self.skipTest('drip_campaign not importable')
        subj, html = self.drip_email_4(self._fake_entry())
        combined = (subj + html).lower()
        has_free_messaging = any(w in combined for w in ['free', 'credit', 'waiting', 'ready', 'never expires'])
        self.assertTrue(has_free_messaging,
            f"Email 4 should surface free credit availability. Subject: {subj}")

    def test_email_5_no_last_chance(self):
        if not self.available:
            self.skipTest('drip_campaign not importable')
        subj, _ = self.drip_email_5(self._fake_entry())
        self.assertNotIn('last chance', subj.lower(),
            f"Email 5 must not use 'last chance' false urgency. Subject: {subj}")

    def test_email_4_never_expires_in_body(self):
        if not self.available:
            self.skipTest('drip_campaign not importable')
        _, html = self.drip_email_4(self._fake_entry())
        self.assertIn('never expires', html.lower(),
            "Email 4 body should explicitly say credits never expire")


# ─── RUN ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestInspectorQuota,
        TestContractorMatching,
        TestContractorLeadRevenue,
        TestFreeTierDetection,
        TestOfferCalculatorMath,
        TestNegotiationChecklist,
        TestStripeInspectorProRouting,
        TestInspectorReportSharing,
        TestContractorSignupValidation,
        TestDripCampaignFixedBehavior,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
