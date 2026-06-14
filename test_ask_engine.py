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


class NoDisclosureRuleTests(unittest.TestCase):
    """v5.89.176: a blanket no-disclosure / as-is seller disclosure must be
    flagged as its own finding, not reported as 'essentially clean'."""

    def test_extract_rules_carries_as_is_rule(self):
        rules = ask_engine.EXTRACT_RULES.lower()
        self.assertIn('as-is', rules)
        self.assertIn('no material defects', rules)
        # it must instruct returning a finding for that case, graded C
        self.assertIn('grade to c', rules)

    def test_parser_keeps_zero_cost_as_is_finding(self):
        raw = (
            '{"summary":"Seller disclosed no defects and sells as-is.",'
            '"grade":"C","findings":[{"severity":"moderate",'
            '"title":"Sold As-Is, Little Disclosed","icon":"\\ud83d\\udcdd",'
            '"cost":0,"detail":"The seller marked no known defects across every '
            'disclosure category and added an as-is clause.",'
            '"why":"A blanket no-defects disclosure shifts repair risk to the '
            'buyer, so an independent inspection is essential."}]}'
        )
        out = ask_engine._parse_findings_json(raw)
        self.assertIsNotNone(out)
        self.assertEqual(out['grade'], 'C')
        self.assertEqual(len(out['findings']), 1)
        f = out['findings'][0]
        self.assertEqual(f['severity'], 'moderate')
        self.assertEqual(f['cost'], 0)
        self.assertTrue(f['title'])
        self.assertTrue(f['why'])


if __name__ == '__main__':
    unittest.main()
