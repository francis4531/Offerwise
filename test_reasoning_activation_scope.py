"""
test_reasoning_activation_scope.py — the buyer-facing reasoning section can be
activated per jurisdiction, so the moat ships to the depth-first market (CA) while
other states keep validating in shadow.

Locks the gate's resolution: global env flag = all states; state allowlist = only
listed states; the national base ('*' / unresolved) never opts in; default OFF.
(DB-setting resolution is exercised in staging; here we cover the env paths, which
the DB path falls through to.)
"""
import os
import unittest
from reasoning.report_bridge import reasoning_in_report_enabled, _reasoning_state


class TestReasoningActivationScope(unittest.TestCase):
    def setUp(self):
        for k in ('OFFERWISE_REASONING_IN_REPORT',
                  'OFFERWISE_REASONING_IN_REPORT_JURISDICTIONS'):
            os.environ.pop(k, None)

    tearDown = setUp

    def test_state_extraction_from_path(self):
        self.assertEqual(_reasoning_state('CA:santa_clara:san_jose'), 'CA')
        self.assertEqual(_reasoning_state('TX'), 'TX')
        self.assertEqual(_reasoning_state('*'), '*')
        self.assertEqual(_reasoning_state(None), '')

    def test_default_is_off_everywhere(self):
        self.assertFalse(reasoning_in_report_enabled('CA:santa_clara:san_jose'))
        self.assertFalse(reasoning_in_report_enabled('TX'))
        self.assertFalse(reasoning_in_report_enabled('*'))
        self.assertFalse(reasoning_in_report_enabled(None))

    def test_allowlist_enables_only_listed_states(self):
        os.environ['OFFERWISE_REASONING_IN_REPORT_JURISDICTIONS'] = 'CA'
        self.assertTrue(reasoning_in_report_enabled('CA:santa_clara:san_jose'))
        self.assertTrue(reasoning_in_report_enabled('CA'))
        self.assertFalse(reasoning_in_report_enabled('TX'))
        self.assertFalse(reasoning_in_report_enabled('FL'))

    def test_allowlist_multi_state(self):
        os.environ['OFFERWISE_REASONING_IN_REPORT_JURISDICTIONS'] = 'ca, tx'  # case/space tolerant
        self.assertTrue(reasoning_in_report_enabled('CA'))
        self.assertTrue(reasoning_in_report_enabled('TX'))
        self.assertFalse(reasoning_in_report_enabled('FL'))

    def test_national_base_never_matches_allowlist(self):
        os.environ['OFFERWISE_REASONING_IN_REPORT_JURISDICTIONS'] = 'CA'
        # An unresolved state must not be opted in by a specific-state allowlist.
        self.assertFalse(reasoning_in_report_enabled('*'))
        self.assertFalse(reasoning_in_report_enabled(None))

    def test_global_env_enables_all_including_unresolved(self):
        os.environ['OFFERWISE_REASONING_IN_REPORT'] = '1'
        self.assertTrue(reasoning_in_report_enabled('CA'))
        self.assertTrue(reasoning_in_report_enabled('TX'))
        self.assertTrue(reasoning_in_report_enabled('*'))
        self.assertTrue(reasoning_in_report_enabled(None))


if __name__ == '__main__':
    unittest.main()
