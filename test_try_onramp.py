"""
test_try_onramp.py — v5.89.152

Covers the no-login conversational on-ramp:
  /try            — page served
  /api/try/start  — parse one doc/text -> top findings + session token
  /api/try/chat   — grounded answer, message cap, expired token

The real parser (Anthropic) and AI client are mocked so the orchestration —
session handling, the free-message cap, complete-sentence findings, and the
expired-token path — is exercised without calling a model.
"""
import os
import unittest
from unittest.mock import patch

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-try-onramp'
os.environ['DATABASE_URL'] = 'sqlite:///test_try_onramp.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-try')
os.environ['RATELIMIT_ENABLED'] = 'false'

if os.path.exists('test_try_onramp.db'):
    os.remove('test_try_onramp.db')

import app as app_module  # noqa: E402
from app import app  # noqa: E402


class _FakeSev:
    def __init__(self, v):
        self.value = v


class _FakeFinding:
    def __init__(self, sev, desc, rec=''):
        self.severity = _FakeSev(sev)
        self.description = desc
        self.recommendation = rec


class _FakeDoc:
    def __init__(self, findings, address='123 Test St'):
        self.inspection_findings = findings
        self.property_address = address


_SAMPLE_FINDINGS = [
    _FakeFinding('minor', 'the downspouts discharge close to the foundation', 'extend them away from the house'),
    _FakeFinding('critical', 'there is a visible crack in the foundation wall', 'have a structural engineer evaluate it before closing'),
    _FakeFinding('major', 'the water heater is past its expected service life', 'budget for replacement'),
    _FakeFinding('moderate', 'several outlets are not grounded', 'have an electrician confirm'),
]

_LONG_TEXT = ('Home inspection report. ' * 30)  # comfortably over the 80-char floor


def _start(client, **body):
    return client.post('/api/try/start', json=body)


class TryOnRampTests(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()
        # Start each test from a clean session store.
        app_module._TRY_SESSIONS.clear()
        # Default to the deterministic parser path (no model call). AI-path
        # tests override ask_engine.extract_findings within their own patch.
        self._ai_patch = patch('ask_engine.extract_findings', return_value=None)
        self._ai_patch.start()

    def tearDown(self):
        self._ai_patch.stop()

    def test_try_page_served(self):
        r = self.client.get('/try')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Drop your inspection report', r.data)

    def test_start_with_text_returns_token_and_findings(self):
        with patch.object(app_module.parser, 'parse_inspection_report',
                          return_value=_FakeDoc(_SAMPLE_FINDINGS)):
            r = _start(self.client, text=_LONG_TEXT)
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d['token'])
        self.assertEqual(len(d['findings']), 3)              # capped to top 3
        self.assertEqual(d['messages_remaining'], app_module._TRY_MAX_MESSAGES)

    def test_findings_sorted_by_severity_and_complete_sentences(self):
        with patch.object(app_module.parser, 'parse_inspection_report',
                          return_value=_FakeDoc(_SAMPLE_FINDINGS)):
            d = _start(self.client, text=_LONG_TEXT).get_json()
        # Critical should rank first, minor should not make the top 3.
        self.assertEqual(d['findings'][0]['severity'], 'critical')
        sevs = [f['severity'] for f in d['findings']]
        self.assertNotIn('minor', sevs)
        for f in d['findings']:
            self.assertTrue(f['text'][0].isupper(), f['text'])
            self.assertTrue(f['text'].rstrip().endswith(('.', '!', '?')), f['text'])
            self.assertNotIn('IssueCategory', f['text'])
            self.assertNotIn('Severity.', f['text'])

    def test_start_short_text_returns_400(self):
        r = _start(self.client, text='too short')
        self.assertEqual(r.status_code, 400)

    def test_start_no_findings_still_opens_chat(self):
        with patch.object(app_module.parser, 'parse_inspection_report',
                          return_value=_FakeDoc([], address=None)):
            r = _start(self.client, text=_LONG_TEXT)
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertTrue(d['token'])
        self.assertEqual(d['findings'], [])

    def test_chat_grounded_answer(self):
        with patch.object(app_module.parser, 'parse_inspection_report',
                          return_value=_FakeDoc(_SAMPLE_FINDINGS)):
            token = _start(self.client, text=_LONG_TEXT).get_json()['token']
        with patch('ai_client.get_ai_response', return_value='The foundation crack is the main concern.') as m:
            r = self.client.post('/api/try/chat', json={'token': token, 'message': 'What worries you most?'})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertIn('foundation', d['answer'].lower())
        self.assertEqual(d['messages_remaining'], app_module._TRY_MAX_MESSAGES - 1)
        # The document text must be passed into the prompt (grounding).
        self.assertIn('Home inspection report', m.call_args[0][0])

    def test_findings_strip_glyphs_labels_and_exclude_non_concerns(self):
        findings = [
            _FakeFinding('informational', '\u25a1 No history of mold'),        # positive -> excluded
            _FakeFinding('minor', 'Comments: small cosmetic scuff'),          # minor -> excluded
            _FakeFinding('critical', 'Comments: \u2610 active foundation leak', 'have it evaluated'),
        ]
        with patch.object(app_module.parser, 'parse_inspection_report',
                          return_value=_FakeDoc(findings)):
            d = _start(self.client, text=_LONG_TEXT).get_json()
        self.assertEqual(len(d['findings']), 1)                 # only the real concern headlines
        self.assertEqual(d['findings'][0]['severity'], 'critical')
        txt = d['findings'][0]['text']
        self.assertTrue(txt.startswith('Active foundation leak'), txt)
        self.assertNotIn('\u25a1', txt)
        self.assertNotIn('\u2610', txt)
        self.assertNotIn('Comments:', txt)

    def test_start_uses_ai_findings_and_summary(self):
        ai = {
            'summary': 'Seller discloses an active shower leak and broken pool equipment.',
            'grade': 'D',
            'findings': [
                {'severity': 'critical', 'icon': '\U0001f4a7', 'title': 'Active master-shower leak',
                 'cost': 18000,
                 'detail': 'The seller discloses an active water leak from the shower pan in the master bedroom.',
                 'why': 'Active leaks cause structural damage and mold; get a moisture inspection before closing.'},
                {'severity': 'major', 'icon': '\U0001f3ca', 'title': 'Pool and spa equipment failures',
                 'cost': 14000,
                 'detail': 'The seller discloses the skimmer pump is not working and the filter pipe handle is broken.',
                 'why': 'Pool repairs add up fast; price a full equipment inspection.'},
            ],
        }
        # Parser finds nothing (typical for a disclosure); the AI still surfaces findings.
        with patch.object(app_module.parser, 'parse_inspection_report',
                          return_value=_FakeDoc([], address='9 Oak Ave')), \
             patch('ask_engine.extract_findings', return_value=ai):
            d = _start(self.client, text=_LONG_TEXT).get_json()
        self.assertEqual(d['summary'], ai['summary'])
        self.assertEqual(len(d['findings']), 2)
        self.assertEqual(d['findings'][0]['severity'], 'critical')
        self.assertEqual(d['findings'][0]['title'], 'Active master-shower leak')
        # Report shell: exposure is the sum of costs, grade passed through.
        self.assertEqual(d['report']['exposure'], 32000)
        self.assertEqual(d['report']['grade'], 'D')
        self.assertIn('reportCta', d)
        self.assertTrue(d['reportCta']['title'])

    def test_start_ai_empty_is_honored_over_parser(self):
        # AI succeeded but found nothing significant -> trust it, do NOT fall
        # back to the parser's (often boilerplate) findings, and send no report.
        with patch.object(app_module.parser, 'parse_inspection_report',
                          return_value=_FakeDoc(_SAMPLE_FINDINGS)), \
             patch('ask_engine.extract_findings',
                   return_value={'summary': 'This disclosure is relatively clean.', 'grade': 'A', 'findings': []}):
            d = _start(self.client, text=_LONG_TEXT).get_json()
        self.assertEqual(d['findings'], [])
        self.assertEqual(d['summary'], 'This disclosure is relatively clean.')
        self.assertNotIn('report', d)

    def test_start_ai_unavailable_falls_back_to_parser(self):
        # extract_findings returns None (model down / no key) -> parser path,
        # simple findings, no summary, no report shell.
        with patch.object(app_module.parser, 'parse_inspection_report',
                          return_value=_FakeDoc(_SAMPLE_FINDINGS)), \
             patch('ask_engine.extract_findings', return_value=None):
            d = _start(self.client, text=_LONG_TEXT).get_json()
        self.assertEqual(len(d['findings']), 3)
        self.assertEqual(d['findings'][0]['severity'], 'critical')
        self.assertEqual(d.get('summary', ''), '')
        self.assertNotIn('report', d)

    def test_chat_unknown_token_returns_410(self):
        r = self.client.post('/api/try/chat', json={'token': 'nope', 'message': 'hi'})
        self.assertEqual(r.status_code, 410)
        self.assertEqual(r.get_json()['error'], 'expired')

    def test_chat_message_cap_returns_cta(self):
        with patch.object(app_module.parser, 'parse_inspection_report',
                          return_value=_FakeDoc(_SAMPLE_FINDINGS)):
            token = _start(self.client, text=_LONG_TEXT).get_json()['token']
        with patch('ai_client.get_ai_response', return_value='An answer.'):
            for _ in range(app_module._TRY_MAX_MESSAGES):
                ok = self.client.post('/api/try/chat', json={'token': token, 'message': 'q'})
                self.assertEqual(ok.status_code, 200)
                self.assertNotIn('capped', ok.get_json())
            capped = self.client.post('/api/try/chat', json={'token': token, 'message': 'one more'})
        d = capped.get_json()
        self.assertTrue(d.get('capped'))
        self.assertEqual(d.get('cta_url'), '/analyze')

    def test_chat_ai_failure_does_not_burn_a_turn(self):
        with patch.object(app_module.parser, 'parse_inspection_report',
                          return_value=_FakeDoc(_SAMPLE_FINDINGS)):
            token = _start(self.client, text=_LONG_TEXT).get_json()['token']
        with patch('ai_client.get_ai_response', side_effect=RuntimeError('boom')):
            r = self.client.post('/api/try/chat', json={'token': token, 'message': 'q'})
        self.assertEqual(r.status_code, 503)
        # Turn refunded — count back to 0.
        self.assertEqual(app_module._TRY_SESSIONS[token]['msg_count'], 0)


if __name__ == '__main__':
    unittest.main()
