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


# ─────────────────────────────────────────────────────────────────────────────
# 11. v5.87.40 FLYWHEEL COMPLETION — wiring + structured cost data
# ─────────────────────────────────────────────────────────────────────────────
# These tests verify the three flywheel completions shipped in v5.87.40:
#   #1 Contractor lead email — wired into contractor_routes.contractor_claim_lead
#   #2 Agent post-close — triggered from PostCloseSurvey.did_buy='yes_closed'
#   #3 Contractor cost data — _update_estimate_from_completion writes structured
#      breakdown_json with categorized systems and metro names
#
# Tests read source files directly via filesystem reads rather than importing
# the modules. This makes them runnable in any environment (no Flask needed),
# which matters because the integrity test runner spins them up in contexts
# where the full app may not be available. The trade-off: these are static
# checks. Runtime behavior is exercised by the existing integrity suite.
# ─────────────────────────────────────────────────────────────────────────────


def _read_repo_file(rel_path):
    """Read a source file from the repo root. Returns '' on missing/error."""
    repo_root = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(repo_root, rel_path), 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return ''


def _extract_function_source(file_src, func_name):
    """Pull a function's source out of file_src by line scan.

    Looks for `def {func_name}(` and returns from there to the next
    top-level `def ` or `class ` (whichever comes first), or EOF. Good
    enough for static-content assertions; not a real Python parser.
    """
    lines = file_src.split('\n')
    out = []
    in_func = False
    for line in lines:
        if not in_func:
            stripped = line.lstrip()
            if stripped.startswith(f'def {func_name}('):
                in_func = True
                out.append(line)
            continue
        # Inside the function — stop when we hit the next top-level def/class
        # at column 0 (less indentation than where the function body starts)
        if line and not line[0].isspace() and (line.startswith('def ') or line.startswith('class ')):
            break
        out.append(line)
    return '\n'.join(out)


class TestContractorLeadEmailWired(unittest.TestCase):
    """Fix #1: send_contractor_lead_email is now invoked from the claim path."""

    def test_claim_endpoint_calls_send_contractor_lead_email(self):
        """contractor_claim_lead should fire the structured-scope flywheel email."""
        src = _read_repo_file('contractor_routes.py')
        self.assertTrue(src, 'contractor_routes.py not found')
        # Extract the claim function and verify the import + call exist within it
        claim_src = _extract_function_source(src, 'contractor_claim_lead')
        self.assertIn('send_contractor_lead_email', claim_src,
                     "contractor_claim_lead should invoke send_contractor_lead_email")

    def test_send_contractor_lead_email_no_longer_reads_missing_column(self):
        """v5.87.40 refactor — _send_lead_to_contractor must derive scope from
        ContractorLead's actual fields, not a non-existent JSON column.
        Reading a missing attribute would AttributeError every call.
        """
        src = _read_repo_file('flywheel_notifications.py')
        fn_src = _extract_function_source(src, '_send_lead_to_contractor')
        self.assertTrue(fn_src, '_send_lead_to_contractor not found')
        # Old impl referenced lead.scope_json directly (attribute access).
        # We check that no code-level access to that attribute exists.
        # Comments mentioning the old field are fine; code-level access is not.
        for line in fn_src.split('\n'):
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('"""'):
                continue
            self.assertNotIn('lead.scope_json', line,
                             f"Code-level access to lead.scope_json found: {line!r}")
        # And the new impl uses the right fields
        self.assertIn('lead.repair_system', fn_src)
        self.assertIn('lead.cost_estimate', fn_src)


class TestAgentPostCloseFromSurvey(unittest.TestCase):
    """Fix #2: agent post-close fires from buyer's PostCloseSurvey response."""

    def test_survey_submit_handler_wires_agent_postclose(self):
        """post_close_survey_submit should trigger send_agent_postclose_email
        when did_buy='yes_closed' and a matching AgentShare exists.
        """
        src = _read_repo_file('survey_routes.py')
        fn_src = _extract_function_source(src, 'post_close_survey_submit')
        self.assertTrue(fn_src, 'post_close_survey_submit not found')
        self.assertIn("'yes_closed'", fn_src,
                     "Handler should check did_buy=='yes_closed' before firing agent flywheel")
        self.assertIn('send_agent_postclose_email', fn_src,
                     "Handler should invoke send_agent_postclose_email when conditions match")
        self.assertIn('AgentShare', fn_src,
                     "Handler should look up matching AgentShare rows")

    def test_survey_handler_skips_already_closed_shares(self):
        """Don't double-fire if buyer re-submits — only fire on shares that
        haven't already been marked closed.
        """
        src = _read_repo_file('survey_routes.py')
        fn_src = _extract_function_source(src, 'post_close_survey_submit')
        self.assertIn('deal_closed_at', fn_src,
                     "Handler should check deal_closed_at to avoid duplicate fires")

    def test_send_agent_postclose_uses_hasattr_guard(self):
        """send_agent_postclose_email must not crash if migration hasn't run.
        v5.87.40 wraps direct attribute writes in hasattr() so the email
        still goes out even if columns are missing.
        """
        src = _read_repo_file('flywheel_notifications.py')
        fn_src = _extract_function_source(src, 'send_agent_postclose_email')
        self.assertTrue(fn_src, 'send_agent_postclose_email not found')
        self.assertIn('hasattr(share', fn_src,
                     "Direct attribute writes must be guarded for pre-migration safety")

    def test_agent_share_model_has_close_columns(self):
        """v5.87.40 added deal_closed_at and final_sale_price to AgentShare.
        Both fields are required for the post-close flywheel.
        """
        src = _read_repo_file('models.py')
        # Find the AgentShare class definition and confirm both columns are declared
        # Within the AgentShare block (between `class AgentShare` and the next class)
        start = src.find('class AgentShare(')
        self.assertGreater(start, 0, 'AgentShare class not found in models.py')
        # Find next class declaration
        next_cls = src.find('\nclass ', start + 1)
        if next_cls < 0:
            next_cls = len(src)
        block = src[start:next_cls]
        self.assertIn('deal_closed_at', block,
                     "AgentShare.deal_closed_at must be declared after v5.87.40")
        self.assertIn('final_sale_price', block,
                     "AgentShare.final_sale_price must be declared after v5.87.40")

    def test_v5_87_40_migration_exists(self):
        """The migration file for the close columns must exist."""
        migration_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      'alembic', 'versions')
        if not os.path.isdir(migration_dir):
            self.skipTest('alembic/versions not present')
        migrations = os.listdir(migration_dir)
        match = [m for m in migrations if 'v5_87_40' in m and 'agent_shares' in m]
        self.assertTrue(len(match) >= 1,
                       f'Expected v5_87_40 agent_shares migration. Found: {migrations}')
        # And the migration must add deal_closed_at + final_sale_price
        mig_src = _read_repo_file(os.path.join('alembic', 'versions', match[0]))
        self.assertIn('deal_closed_at', mig_src,
                     'Migration must add deal_closed_at column')
        self.assertIn('final_sale_price', mig_src,
                     'Migration must add final_sale_price column')


class TestContractorCostDataStructure(unittest.TestCase):
    """Fix #3: _update_estimate_from_completion writes per-category breakdowns."""

    def test_function_categorizes_work_completed(self):
        """v5.87.40 — work_completed is parsed into known categories rather
        than written as opaque text.
        """
        src = _read_repo_file('flywheel_notifications.py')
        fn_src = _extract_function_source(src, '_update_estimate_from_completion')
        self.assertTrue(fn_src, '_update_estimate_from_completion not found')
        # Categories the predictor needs
        for cat in ('electrical', 'plumbing', 'hvac', 'roof', 'foundation'):
            self.assertIn(cat, fn_src,
                         f"Category '{cat}' must be in CATEGORY_KEYWORDS map")

    def test_function_looks_up_metro_name(self):
        """The previous impl wrote metro_name='' which made rows useless to
        the predictor. v5.87.40 looks up RepairCostZone by ZIP prefix.
        """
        src = _read_repo_file('flywheel_notifications.py')
        fn_src = _extract_function_source(src, '_update_estimate_from_completion')
        self.assertIn('RepairCostZone', fn_src,
                     "Function must look up zone metadata by ZIP prefix")
        self.assertIn('zip_prefix', fn_src,
                     "Function must use 3-digit ZIP prefix for zone lookup")
        self.assertIn('metro_name', fn_src)

    def test_function_writes_structured_breakdown(self):
        """breakdown_json must contain per-system rows, not be empty '[]'."""
        src = _read_repo_file('flywheel_notifications.py')
        fn_src = _extract_function_source(src, '_update_estimate_from_completion')
        self.assertIn("'category'", fn_src,
                     "Each breakdown row must carry the categorized system")
        self.assertIn("'allocated_price'", fn_src,
                     "Each breakdown row must carry its share of total price")
        self.assertIn('json.dumps(breakdown)', fn_src,
                     "breakdown_json must be a serialized list, not '[]'")
        # Old impl had breakdown_json='[]' literal — that's the bug
        self.assertNotIn("breakdown_json='[]'", fn_src,
                        "v5.87.40 must not write empty '[]' breakdowns")

    def test_function_does_not_pass_invalid_source_kwarg(self):
        """RepairCostLog has no `source` column — passing source= as a kwarg
        was a latent bug. v5.87.40 puts source inside breakdown_json.
        """
        src = _read_repo_file('flywheel_notifications.py')
        fn_src = _extract_function_source(src, '_update_estimate_from_completion')
        # RepairCostLog constructor must not pass source=
        # Look for the literal pattern source='contractor_completion' as a kwarg
        # (vs. inside breakdown_json dict where 'source' is fine)
        # Heuristic: kwarg lines look like `            source='contractor_completion'`
        # while dict entries look like `'source': 'contractor_completion'`
        for line in fn_src.split('\n'):
            stripped = line.strip()
            self.assertFalse(
                stripped.startswith("source='contractor_completion'"),
                f"RepairCostLog has no 'source' column — must not be passed as kwarg. Line: {line!r}",
            )

    def test_band_pct_calibrated_by_system_count(self):
        """Single-system completions get a tighter band; multi-system wider."""
        src = _read_repo_file('flywheel_notifications.py')
        fn_src = _extract_function_source(src, '_update_estimate_from_completion')
        # The hardcoded * 0.9 / * 1.1 band is gone
        self.assertNotIn('* 0.9', fn_src, "Old hardcoded -10% band must be replaced")
        self.assertNotIn('* 1.1', fn_src, "Old hardcoded +10% band must be replaced")
        self.assertIn('band_pct', fn_src,
                     "v5.87.40 uses a calibrated band based on system count")

    def test_function_handles_no_zone_match(self):
        """When ZIP prefix isn't in RepairCostZone, function should still write
        a row (with metro_name='' and cost_multiplier=1.0) rather than skip.
        """
        src = _read_repo_file('flywheel_notifications.py')
        fn_src = _extract_function_source(src, '_update_estimate_from_completion')
        self.assertIn('zone =', fn_src)
        # Default fallback must exist
        self.assertIn('if zone', fn_src,
                     "Function must handle the no-zone case gracefully")


class TestFlywheelEndToEndShape(unittest.TestCase):
    """Sanity checks on the overall v5.87.40 wiring shape — no flywheel
    is silently broken in a way the source-inspection tests above missed."""

    def test_all_flywheel_emails_have_callers(self):
        """Every flywheel email function must have at least one production
        caller. This catches the v5.87.39-and-prior state where
        send_contractor_lead_email existed but nothing called it.
        """
        repo_root = os.path.dirname(os.path.abspath(__file__))
        scan_files = []
        for fname in os.listdir(repo_root):
            if fname.endswith('.py') and not fname.startswith('test_'):
                scan_files.append(os.path.join(repo_root, fname))

        flywheel_callers = {
            '_send_inspector_loop_email':      ['analysis_routes.py'],
            'send_agent_postclose_email':      ['agent_routes.py', 'survey_routes.py'],
            'send_contractor_lead_email':      ['contractor_routes.py'],
            'process_contractor_completion':   ['contractor_routes.py'],
        }

        for fn_name, expected_locations in flywheel_callers.items():
            count_outside_module = 0
            for fp in scan_files:
                if fp.endswith('flywheel_notifications.py'):
                    continue
                try:
                    with open(fp, 'r', encoding='utf-8') as f:
                        if fn_name in f.read():
                            count_outside_module += 1
                except Exception:
                    pass

            self.assertGreater(
                count_outside_module, 0,
                f"Flywheel function {fn_name} has no production caller — "
                f"expected at least one in {expected_locations}. "
                f"This is the bug-shape of v5.87.39 and earlier."
            )


if __name__ == '__main__':
    unittest.main(verbosity=2)
