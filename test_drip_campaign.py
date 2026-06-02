"""
Tests for OfferWise Drip Campaign Engine
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from drip_campaign import (
    generate_unsubscribe_token,
    get_unsubscribe_url,
    get_list_unsubscribe_headers,
    DRIP_SCHEDULE,
    DRIP_TEMPLATES,
    drip_email_1,
    drip_email_2,
    drip_email_3,
    drip_email_4,
    drip_email_5,
    send_drip_email,
)


class FakeEntry:
    def __init__(self, **kwargs):
        self.email = kwargs.get('email', 'test@example.com')
        self.source = kwargs.get('source', 'risk-check-results')
        self.result_address = kwargs.get('result_address', '123 Main St, San Jose, CA')
        self.result_grade = kwargs.get('result_grade', 'D')
        self.result_exposure = kwargs.get('result_exposure', 47200)
        self.result_score = kwargs.get('result_score', 45)
        self.had_result = kwargs.get('had_result', True)
        self.unsubscribe_token = kwargs.get('unsubscribe_token', 'test-token-abc123')
        self.email_unsubscribed = kwargs.get('email_unsubscribed', False)
        self.drip_step = kwargs.get('drip_step', 0)
        self.drip_last_sent_at = kwargs.get('drip_last_sent_at', None)
        self.drip_completed = kwargs.get('drip_completed', False)
        self.created_at = kwargs.get('created_at', datetime.now(timezone.utc) - timedelta(hours=1))


# === TOKEN GENERATION ===

class TestUnsubscribeTokens:
    def test_tokens_are_unique(self):
        tokens = {generate_unsubscribe_token() for _ in range(100)}
        assert len(tokens) == 100

    def test_token_is_url_safe(self):
        token = generate_unsubscribe_token()
        assert '+' not in token and '/' not in token

    def test_token_length(self):
        assert len(generate_unsubscribe_token()) >= 20


# === LIST-UNSUBSCRIBE HEADERS ===

class TestListUnsubscribeHeaders:
    def test_contains_url(self):
        h = get_list_unsubscribe_headers('tok123')
        assert 'tok123' in h['List-Unsubscribe']

    def test_contains_one_click_post(self):
        h = get_list_unsubscribe_headers('tok123')
        assert h['List-Unsubscribe-Post'] == 'List-Unsubscribe=One-Click'

    def test_url_format(self):
        url = get_unsubscribe_url('abc')
        assert '/unsubscribe/abc' in url


# === SCHEDULE ===

class TestDripSchedule:
    def test_five_steps(self):
        assert len(DRIP_SCHEDULE) == 5

    def test_step_1_immediate(self):
        assert DRIP_SCHEDULE[1] == 0

    def test_monotonically_increasing(self):
        vals = [DRIP_SCHEDULE[i] for i in range(1, 6)]
        assert vals == sorted(vals)
        assert len(set(vals)) == 5  # all distinct

    def test_all_templates_exist(self):
        for step in range(1, 6):
            assert step in DRIP_TEMPLATES


# === EMAIL TEMPLATES ===

class TestEmailTemplates:
    def test_email_1_risk_check(self):
        entry = FakeEntry(source='risk-check-results', result_exposure=47200, result_grade='D')
        subj, html = drip_email_1(entry)
        assert '$47,200' in html
        assert 'Grade D' in html
        assert '123 Main St' in html

    def test_email_1_truth_check(self):
        entry = FakeEntry(source='truth-check-results', result_score=45, result_exposure=0)
        _, html = drip_email_1(entry)
        assert '45/100' in html

    def test_email_1_generic(self):
        entry = FakeEntry(source='homepage', result_exposure=0, result_address='', result_grade='')
        _, html = drip_email_1(entry)
        assert 'OfferWise' in html

    def test_email_2_education(self):
        _, html = drip_email_2(FakeEntry())
        assert 'SECTION' in html
        assert 'disclosure' in html.lower()  # updated: template uses 'disclosure sections' not 'TDS'

    def test_email_3_case_study(self):
        _, html = drip_email_3(FakeEntry())
        assert '$23' in html

    def test_email_4_free_credit_messaging(self):
        """Email 4 should surface free credit — no false expiry language."""
        entry = FakeEntry(result_address='456 Oak Ave')
        subj, html = drip_email_4(entry)
        # Must NOT contain false urgency about expiry
        assert 'expires' not in subj.lower(), \
            f"Email 4 subject should not contain 'expires' (false urgency removed): {subj}"
        # Must contain free credit messaging
        assert any(word in (subj + html).lower() for word in ['free', 'credit', 'waiting', 'ready']), \
            f"Email 4 should surface free credit availability. Subject: {subj}"
        assert '456 Oak Ave' in html

    def test_email_5_no_false_urgency(self):
        """Email 5 should not use false 'last chance' urgency."""
        subj, html = drip_email_5(FakeEntry())
        assert 'last chance' not in subj.lower(), \
            f"Email 5 should not use false urgency ('last chance' removed): {subj}"
        # Should be testimonial/social proof based
        assert any(word in (subj + html).lower() for word in ['found', 'buyers', 'stories', 'analysis', 'what']), \
            f"Email 5 should contain social proof content. Subject: {subj}"

    def test_all_have_unsubscribe_link(self):
        entry = FakeEntry()
        for step, fn in DRIP_TEMPLATES.items():
            _, html = fn(entry)
            assert '/unsubscribe/' in html, f"Email {step} missing unsubscribe link"

    def test_all_are_valid_html(self):
        entry = FakeEntry()
        for step, fn in DRIP_TEMPLATES.items():
            subj, html = fn(entry)
            assert len(subj) > 5
            assert '<!DOCTYPE' in html
            assert '</html>' in html


# === SEND LOGIC ===

class TestSendDripEmail:
    @patch('email_service.EMAIL_ENABLED', True)
    @patch('email_service.send_email', return_value=True)
    def test_send_updates_step(self, mock_send):
        entry = FakeEntry(drip_step=0)
        result = send_drip_email(entry, 1)
        assert result is True
        assert entry.drip_step == 1
        assert entry.drip_last_sent_at is not None

    @patch('email_service.EMAIL_ENABLED', True)
    @patch('email_service.send_email', return_value=True)
    def test_final_step_marks_completed(self, mock_send):
        """MAX_DRIP_STEP=17: sending step 17 should mark drip_completed=True."""
        from drip_campaign import MAX_DRIP_STEP
        entry = FakeEntry(drip_step=MAX_DRIP_STEP - 1)
        send_drip_email(entry, MAX_DRIP_STEP)
        assert entry.drip_completed is True

    def test_skip_if_unsubscribed(self):
        entry = FakeEntry(email_unsubscribed=True)
        assert send_drip_email(entry, 1) is False

    def test_invalid_step(self):
        assert send_drip_email(FakeEntry(), 99) is False

    @patch('email_service.EMAIL_ENABLED', True)
    @patch('email_service.send_email', return_value=True)
    def test_generates_token_if_missing(self, mock_send):
        entry = FakeEntry(unsubscribe_token=None)
        send_drip_email(entry, 1)
        assert entry.unsubscribe_token is not None

    @patch('email_service.EMAIL_ENABLED', True)
    @patch('email_service.send_email', return_value=True)
    def test_headers_passed_to_send(self, mock_send):
        entry = FakeEntry()
        send_drip_email(entry, 1)
        assert mock_send.called
        kwargs = mock_send.call_args
        # send_email is called with keyword args
        headers = kwargs.kwargs.get('headers') or kwargs[1].get('headers', {})
        assert 'List-Unsubscribe' in headers
        assert 'List-Unsubscribe-Post' in headers


# === TIMING LOGIC ===

class TestSchedulerTiming:
    def test_step_1_after_5_min(self):
        now = datetime.now(timezone.utc)
        entry = FakeEntry(created_at=now - timedelta(minutes=10))
        hours = (now - entry.created_at).total_seconds() / 3600
        assert hours >= DRIP_SCHEDULE[1]

    def test_step_2_requires_48h(self):
        assert DRIP_SCHEDULE[2] == 48

    def test_step_5_at_day_14(self):
        assert DRIP_SCHEDULE[5] == 336


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
