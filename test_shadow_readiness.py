"""
test_shadow_readiness.py — the shadow readout must tell you, per state, whether
the reasoning moat is safe to turn on for buyers. This gates the activation
allowlist: a state is READY only with enough real samples AND a firing extractor
AND a producing disclosure side AND reasoning surfacing at least as much as live.
"""
import unittest
from reasoning_shadow import _summarize_rows


class Row:
    def __init__(self, juris, extr=True, disc=5, ri=3, lc=1, lu=1):
        self.jurisdiction = juris
        self.extractor_ok = extr
        self.disclosure_readings = disc
        self.reasoning_issues = ri
        self.live_contradictions = lc
        self.live_undisclosed = lu
        self.ok = True
        self.reasoning_corroborated = 1
        self.reasoning_contradiction = 1
        self.reasoning_undisclosed = 2
        self.elapsed_ms = 1200


class TestShadowReadiness(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_summarize_rows([]), {"count": 0})

    def test_state_grouping_and_counts(self):
        rows = [Row('CA:santa_clara:san_jose') for _ in range(12)] + [Row('TX') for _ in range(3)]
        out = _summarize_rows(rows)
        self.assertEqual(out['count'], 15)
        self.assertIn('CA', out['by_jurisdiction'])
        self.assertIn('TX', out['by_jurisdiction'])
        self.assertEqual(out['by_jurisdiction']['CA']['count'], 12)
        self.assertEqual(out['by_jurisdiction']['TX']['count'], 3)

    def test_ready_requires_enough_samples(self):
        # Strong signal but only 3 samples -> not ready (needs samples).
        rows = [Row('TX') for _ in range(3)]
        b = _summarize_rows(rows)['by_jurisdiction']['TX']
        self.assertFalse(b['ready'])
        self.assertIn('needs samples', b['verdict'])

    def test_ready_when_bar_cleared(self):
        rows = [Row('CA') for _ in range(12)]  # strong on all metrics
        b = _summarize_rows(rows)['by_jurisdiction']['CA']
        self.assertTrue(b['ready'])
        self.assertIn('READY', b['verdict'])

    def test_below_threshold_not_ready(self):
        # Enough samples, but reasoning never beats live -> below threshold.
        rows = [Row('FL', ri=0, lc=1, lu=1) for _ in range(12)]
        b = _summarize_rows(rows)['by_jurisdiction']['FL']
        self.assertEqual(b['reasoning_surfaced_more_rate'], 0.0)
        self.assertFalse(b['ready'])
        self.assertIn('below threshold', b['verdict'])

    def test_disclosure_gap_blocks_readiness(self):
        # Extractor fine, reasoning beats live, but disclosure side never fires
        # (non-CA disclosure not extracted) -> not ready. This is the exact
        # national-correctness signal the moat work targets.
        rows = [Row('TX', disc=0) for _ in range(12)]
        b = _summarize_rows(rows)['by_jurisdiction']['TX']
        self.assertEqual(b['disclosure_extracted_rate'], 0.0)
        self.assertFalse(b['ready'])


if __name__ == '__main__':
    unittest.main()
