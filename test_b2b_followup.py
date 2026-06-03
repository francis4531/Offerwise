"""test_b2b_followup.py — v5.89.136

Tests the B2B follow-up sequence engine:
  - run_b2b_followup_scheduler candidate selection + timing + max-touches +
    stop-on-reply + unsubscribe handling (send_followup is patched so these
    tests exercise scheduling logic, not the email/render path).
  - _build_followup copy: subject threads off the original, human tone
    (no em-dashes), clean link, unsubscribe footer, first-name substitution.
"""
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'test-secret-b2bfu')
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-b2bfu')
os.environ.setdefault('OUTREACH_UNSUB_SECRET', 'test-unsub-secret-b2bfu')

_DB_PATH = 'test_b2b_followup.db'
if os.path.exists(_DB_PATH):
    os.remove(_DB_PATH)

from flask import Flask
from models import db, OutreachContact, OutreachLog, OutreachUnsubscribe

_app = Flask(__name__)
_app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{_DB_PATH}'
_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(_app)

from b2b_followup import (
    run_b2b_followup_scheduler, _build_followup, MAX_TOUCHES, FOLLOWUP_GAP_HOURS,
)


class B2BFollowupBase(unittest.TestCase):
    def setUp(self):
        self.ctx = _app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.rollback()
        db.drop_all()
        self.ctx.pop()

    def _contact(self, email, status='contacted', days_ago=4, n_logs=1,
                 name='Jane Doe', wedge='renovation_lenders', company='Acme'):
        c = OutreachContact(
            cohort='b2b', email=email, name=name, wedge=wedge, company=company,
            status=status,
            last_contacted_at=datetime.utcnow() - timedelta(days=days_ago),
        )
        db.session.add(c)
        db.session.flush()
        for i in range(n_logs):
            db.session.add(OutreachLog(
                cohort='b2b', contact_id=c.id, to_email=email,
                subject='OfferWise + Acme', body='hi', success=True,
                sent_at=datetime.utcnow() - timedelta(days=days_ago + i),
            ))
        db.session.commit()
        return c


class TestScheduler(B2BFollowupBase):
    def test_sends_touch_2_when_due(self):
        c = self._contact('due2@example.com', days_ago=4, n_logs=1)
        with patch('b2b_followup.send_followup', return_value=True) as m:
            stats = run_b2b_followup_scheduler(db.session)
        self.assertEqual(stats['sent'], 1)
        m.assert_called_once()
        # second positional arg is the step
        self.assertEqual(m.call_args[0][1], 2)
        self.assertEqual(m.call_args[0][0].id, c.id)

    def test_sends_touch_3_when_two_already_sent(self):
        self._contact('due3@example.com', days_ago=5, n_logs=2)
        with patch('b2b_followup.send_followup', return_value=True) as m:
            run_b2b_followup_scheduler(db.session)
        m.assert_called_once()
        self.assertEqual(m.call_args[0][1], 3)

    def test_skips_when_too_recent(self):
        # 1 hour ago — far below the touch-2 gap
        self._contact('recent@example.com', days_ago=0, n_logs=1)
        # overwrite to exactly 1h ago
        c = OutreachContact.query.filter_by(email='recent@example.com').first()
        c.last_contacted_at = datetime.utcnow() - timedelta(hours=1)
        db.session.commit()
        with patch('b2b_followup.send_followup', return_value=True) as m:
            stats = run_b2b_followup_scheduler(db.session)
        m.assert_not_called()
        self.assertEqual(stats['sent'], 0)

    def test_stops_when_replied(self):
        self._contact('replied@example.com', status='replied', days_ago=30, n_logs=1)
        with patch('b2b_followup.send_followup', return_value=True) as m:
            stats = run_b2b_followup_scheduler(db.session)
        m.assert_not_called()
        self.assertEqual(stats['checked'], 0)

    def test_respects_max_touches(self):
        self._contact('maxed@example.com', days_ago=30, n_logs=MAX_TOUCHES)
        with patch('b2b_followup.send_followup', return_value=True) as m:
            stats = run_b2b_followup_scheduler(db.session)
        m.assert_not_called()
        self.assertEqual(stats['skipped'], 1)

    def test_skips_unsubscribed(self):
        self._contact('unsub@example.com', days_ago=4, n_logs=1)
        db.session.add(OutreachUnsubscribe(email='unsub@example.com', reason='manual'))
        db.session.commit()
        with patch('b2b_followup.send_followup', return_value=True) as m:
            stats = run_b2b_followup_scheduler(db.session)
        m.assert_not_called()
        self.assertEqual(stats['skipped'], 1)

    def test_legacy_contacted_no_logs_floors_at_touch_2(self):
        # A 'contacted' contact with NO OutreachLog rows (legacy first send
        # was never logged) must still be treated as touch 1 -> next is 2.
        self._contact('legacy@example.com', days_ago=4, n_logs=0)
        with patch('b2b_followup.send_followup', return_value=True) as m:
            run_b2b_followup_scheduler(db.session)
        m.assert_called_once()
        self.assertEqual(m.call_args[0][1], 2)


class TestCopy(B2BFollowupBase):
    def test_subject_threads_off_original(self):
        c = self._contact('copy@example.com', days_ago=4, n_logs=1)
        subject, html, body = _build_followup(c, 2)
        self.assertTrue(subject.startswith('Re: '))
        self.assertNotIn('Re: Re:', subject)

    def test_copy_is_human_no_em_dash(self):
        c = self._contact('copy2@example.com', days_ago=4, n_logs=1)
        for step in (2, 3, 4):
            _, _, body = _build_followup(c, step)
            self.assertNotIn('\u2014', body)  # em-dash
            self.assertIn('-Francis', body)

    def test_link_and_unsubscribe_present(self):
        c = self._contact('copy3@example.com', days_ago=4, n_logs=1)
        _, html, _ = _build_followup(c, 2)
        self.assertIn('getofferwise.ai', html)
        self.assertIn('<a href="https://', html)
        self.assertIn('unsubscribe', html.lower())

    def test_first_name_substituted(self):
        c = self._contact('copy4@example.com', days_ago=4, n_logs=1, name='Smith, John')
        _, _, body = _build_followup(c, 2)
        self.assertIn('John', body)


if __name__ == '__main__':
    unittest.main()
