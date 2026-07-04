"""
test_market_narrative_consistency.py — the report must tell ONE market story.

Regression lock for v5.89.248. The bug: the "Market discount" line carried a
hardcoded basis ("Listed above comparable closings") that could directly
contradict the AVM/comp narrative — e.g. a bankruptcy sale listed 43% BELOW the
AVM and 40% below comps still read "listed above comparable closings." A report
whose whole pitch is catching contradictions must not contain one. These tests
prove the market-discount basis is DERIVED from the real comp position and agrees
in direction with the AVM narrative.
"""
import unittest
from market_intelligence import apply_market_adjustment


class MI:
    def __init__(self, avm, comps, comp_count, temp, avc, trusted=True):
        self.avm_price = avm
        self.comp_median_price = comps
        self.comp_count = comp_count
        self.market_temperature = temp
        self.data_quality = 'high'
        self.asking_vs_comps_pct = avc
        self.avm_trusted = trusted
        self.avm_suppression_reason = ''


class TestMarketNarrativeConsistency(unittest.TestCase):
    def test_below_market_does_not_claim_above_comps(self):
        # Bankruptcy-style: asking far below AVM and comps. Must never say "above".
        mi = MI(avm=1_590_000, comps=1_500_000, comp_count=6, temp='buyer', avc=-40.0)
        r = apply_market_adjustment(700_000, 900_000, mi)
        self.assertLess(r['asking_vs_avm_pct'], 0)                 # below AVM
        self.assertIn('below', r['market_discount_basis'].lower())
        self.assertNotIn('above', r['market_discount_basis'].lower())

    def test_avm_and_comp_directions_agree(self):
        # When both AVM and comps are present, the basis direction must match the
        # AVM direction — they can't tell opposite stories in the same report.
        for avm, comps, avc in [
            (1_590_000, 1_500_000, -40.0),   # below both
            (820_000, 800_000, 10.0),        # above both
        ]:
            mi = MI(avm=avm, comps=comps, comp_count=5, temp='neutral', avc=avc)
            r = apply_market_adjustment(800_000, 900_000, mi)
            avm_below = r['asking_vs_avm_pct'] < 0
            basis = r['market_discount_basis'].lower()
            if avm_below:
                self.assertIn('below', basis)
            else:
                self.assertIn('above', basis)

    def test_basis_reflects_actual_comp_percentage(self):
        mi = MI(avm=0, comps=820_000, comp_count=5, temp='neutral', avc=9.8)
        r = apply_market_adjustment(800_000, 900_000, mi)
        self.assertIn('above recent comparable closings', r['market_discount_basis'])

    def test_rationale_anchor_not_inverted(self):
        # The old wording labeled comps "above asking" on a property listed ABOVE
        # comps. The corrected wording anchors on asking vs the median.
        mi = MI(avm=0, comps=800_000, comp_count=4, temp='neutral', avc=12.5)
        r = apply_market_adjustment(800_000, 900_000, mi)
        self.assertIn('asking is 12.5% above that median', r['rationale'])

    def test_no_data_means_market_not_applied(self):
        # No AVM and no comps: the market line must not render at all (so it can't
        # contradict anything). apply returns market_applied False.
        mi = MI(avm=0, comps=0, comp_count=0, temp='buyer', avc=0.0)
        r = apply_market_adjustment(800_000, 900_000, mi)
        self.assertFalse(r.get('market_applied', False))


if __name__ == '__main__':
    unittest.main()
