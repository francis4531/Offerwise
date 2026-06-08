"""
test_ask_engine.py — v5.89.154

Unit tests for the shared grounding engine that powers all three Ask surfaces
(on-ramp, full report, shared view). Covers the context builders and the
grounded_answer call (AI mocked). The per-surface endpoints are thin wrappers
around these, plus the on-ramp's full path is covered in test_try_onramp.py.
"""
import json
import unittest
from unittest.mock import patch

import ask_engine


class _FakeAnalysis:
    def __init__(self, result):
        self.result_json = json.dumps(result)


class _FakeDoc:
    def __init__(self, dtype, fname, text):
        self.document_type = dtype
        self.filename = fname
        self.extracted_text = text


class AskEngineTests(unittest.TestCase):

    def test_context_from_document(self):
        ctx = ask_engine.context_from_document('Roof shows wear.')
        self.assertIn('Roof shows wear.', ctx)

    def test_context_from_analysis_includes_result_and_docs(self):
        a = _FakeAnalysis({'offer_strategy': {'recommended_offer': 475000}})
        docs = [_FakeDoc('inspection_report', 'insp.pdf', 'Foundation crack noted.'),
                _FakeDoc('seller_disclosure', 'disc.pdf', 'No known water issues.')]
        ctx = ask_engine.context_from_analysis(a, docs)
        self.assertIn('OFFERWISE ANALYSIS', ctx)
        self.assertIn('475000', ctx)
        self.assertIn('Foundation crack noted.', ctx)
        self.assertIn('No known water issues.', ctx)
        self.assertIn('INSPECTION REPORT', ctx)  # label cleaned/upper

    def test_context_from_analysis_handles_bad_json(self):
        class Bad:
            result_json = '{not valid'
        ctx = ask_engine.context_from_analysis(Bad(), [])
        self.assertIsInstance(ctx, str)  # no crash

    def test_context_from_snapshot(self):
        ctx = ask_engine.context_from_snapshot({'address': '128 Maple', 'offerscore': 33})
        self.assertIn('128 Maple', ctx)
        self.assertIn('SHARED OFFERWISE ANALYSIS', ctx)

    def test_grounded_answer_passes_context_and_system(self):
        with patch('ai_client.get_ai_response', return_value='Grounded reply.') as m:
            out = ask_engine.grounded_answer('What about the roof?', 'The roof is 15 years old.')
        self.assertEqual(out, 'Grounded reply.')
        prompt = m.call_args[0][0]
        self.assertIn('The roof is 15 years old.', prompt)
        self.assertIn('What about the roof?', prompt)
        # system prompt enforces grounding + no horizontal rules
        sys = m.call_args.kwargs.get('system', '')
        self.assertIn('ONLY from the context', sys)
        self.assertIn("'---'", sys)

    def test_grounded_answer_truncates_huge_context(self):
        big = 'x' * (ask_engine.MAX_CONTEXT_CHARS + 5000)
        with patch('ai_client.get_ai_response', return_value='ok') as m:
            ask_engine.grounded_answer('q', big)
        prompt = m.call_args[0][0]
        # context portion capped; prompt shouldn't carry the full oversized blob
        self.assertLess(prompt.count('x'), ask_engine.MAX_CONTEXT_CHARS + 100)


if __name__ == '__main__':
    unittest.main()
