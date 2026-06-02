"""
test_e2e_analyze_cassettes.py — v5.88.19 (Cassette replay tests)

Replays VCR cassettes recorded by test_cassettes/record_cassettes.py to
exercise /api/analyze WITHOUT making real API calls. Cassettes capture
responses from Anthropic, RentCast, Hunter, etc. so the orchestrator
runs exactly as it did during recording — but free, fast, deterministic.

WHAT THESE TESTS PROTECT AGAINST:
  - Prompt drift: if intelligence_engine.py changes its prompt template
    in a way that breaks parsing, the cassette replay returns the OLD
    Anthropic response → parser fails → test fails. Catches the bug
    before it ships.
  - Response shape mismatch: if the orchestrator expects a field that
    no longer exists in the model output, the parser breaks. Test fails.
  - Persistence regression: if a code change stops creating Property/
    Analysis rows, the assertion fails.

WHAT THESE TESTS DO NOT PROTECT AGAINST:
  - Real upstream API changes (Anthropic releases new model with
    different output): cassette captures OLD response, test passes
    even though production breaks. Re-record cassettes when you
    suspect this.
  - Brand-new request paths not yet recorded: skipped with clear
    message.

CASSETTE LIFECYCLE:
  - Record locally: see test_cassettes/record_cassettes.py docstring
  - Cassettes committed to git so CI replays them
  - Re-record quarterly OR after prompt/orchestrator changes

Coverage: 8 tests (3 infrastructure + 5 cassette replays)
"""
import json
import os
import unittest
from datetime import datetime
from pathlib import Path

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-cassette-replay'
os.environ['DATABASE_URL'] = 'sqlite:///test_e2e_cassette.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-cassette-replay')
os.environ['RATELIMIT_ENABLED'] = 'false'

# Cassette replay only needs a fake API key — vcr intercepts requests
# before they leave the process. Setting a value satisfies any code
# that bails when the env var is empty.
os.environ.setdefault('ANTHROPIC_API_KEY', 'sk-ant-fake-replay-key')

import os as _os
_db_path = 'test_e2e_cassette.db'
if _os.path.exists(_db_path):
    _os.remove(_db_path)


CASSETTE_DIR = Path(__file__).parent / 'test_cassettes' / 'cassettes'


def _unique_email(prefix='cassette'):
    return f'{prefix}_{int(datetime.now().timestamp() * 1000000)}@e2e-cassette.test.example.com'


def _login_session(client, user_id, password=None):
    """v5.88.31: previously this set sess['_user_id'] directly via
    session_transaction(). That works for in-process pytest runs but
    fails when the test runs INSIDE a Flask request context (e.g.,
    via /api/test/cassette-replays endpoint inside a gunicorn worker).
    In that context, current_user during client.post() doesn't pick
    up session_transaction values reliably.

    The robust approach: POST to the real /auth/login-email endpoint
    with the test user's known password. The test_client's cookie jar
    picks up the real Flask-Login session cookie, and subsequent
    requests are authenticated the same way real users are.

    Callers should pass the password explicitly so the helper doesn't
    have to know the password convention used by each test.

    Falls back to session_transaction injection if password is None
    or login fails — preserves backward compatibility for any tests
    that haven't been migrated.
    """
    if password is not None:
        # Look up email. test_client uses the same app, so we can use
        # the app object directly without needing a separate context
        # (avoids "Working outside of application context" if the
        # caller didn't wrap us in one).
        try:
            from models import User
            from flask import current_app
            try:
                # Try to use existing context first
                u = User.query.get(int(user_id))
            except RuntimeError:
                # No context — push one via the app attached to the client
                app = client.application
                with app.app_context():
                    u = User.query.get(int(user_id))
            email = u.email if u else None
            if email:
                r = client.post('/auth/login-email', json={
                    'email': email,
                    'password': password,
                })
                if r.status_code == 200:
                    return  # logged in via real login flow
        except Exception:
            # If anything goes wrong with login flow, fall through to
            # the session_transaction fallback below
            pass

    # Fallback: direct session injection (works for in-process pytest)
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def _has_cassette(name):
    return (CASSETTE_DIR / name).exists()


def _cleanup_test_users(db, User, Property, Analysis):
    """v5.88.29: robust cleanup that won't crash the test if foreign-key
    constraints block a User delete.

    On SQLite (local testing), FK enforcement is off by default, so naive
    delete-user-after-deleting-analyses works fine. On PostgreSQL
    (production-like, what Render uses), the analysis flow creates rows
    in many tables that reference user_id — AICallLog, ConsentRecord,
    CreditTransaction, FunnelEvent, UsageRecord, etc. Any one of those
    blocks the User delete with an IntegrityError.

    Strategy:
      1. Try the simple cleanup (Analysis → Property → User)
      2. If that fails (FK violation), rollback and try without deleting User
      3. The leaked test user is harmless because:
         - Email matches the @e2e-cassette.test.example.com pattern
         - The is_test_account() helper (v5.88.25) filters it from Buyers view
         - The cassette recorder's startup ghost-sweep will clean it eventually

    Returns the number of users cleaned up (could be 0 if FK blocked).
    """
    try:
        users = User.query.filter(
            User.email.like('%@e2e-cassette.test.example.com')
        ).all()
        for u in users:
            props = Property.query.filter_by(user_id=u.id).all()
            for p in props:
                Analysis.query.filter_by(property_id=p.id).delete()
                db.session.delete(p)
            db.session.delete(u)
        db.session.commit()
        return len(users)
    except Exception:
        # FK constraint or other DB issue — rollback and try cleaning
        # just the analyses + properties (which usually do cascade fine)
        db.session.rollback()
        try:
            users = User.query.filter(
                User.email.like('%@e2e-cassette.test.example.com')
            ).all()
            for u in users:
                props = Property.query.filter_by(user_id=u.id).all()
                for p in props:
                    Analysis.query.filter_by(property_id=p.id).delete()
                    db.session.delete(p)
            db.session.commit()
        except Exception:
            db.session.rollback()
        return 0


# =============================================================================
# Cassette infrastructure tests (run with or without recorded cassettes)
# =============================================================================

class TestCassetteInfrastructure(unittest.TestCase):
    """Verify the cassette directory + recording script exist.
    These run even without recorded cassettes — they confirm the
    scaffolding is intact."""

    def test_cassette_directory_exists(self):
        """test_cassettes/cassettes/ must exist (even if empty)."""
        self.assertTrue(CASSETTE_DIR.exists(),
            f'Cassette directory missing: {CASSETTE_DIR}')

    def test_recording_script_exists(self):
        """The recording script must be present so future re-records
        can be done by anyone with the API keys."""
        script = CASSETTE_DIR.parent / 'record_cassettes.py'
        self.assertTrue(script.exists(),
            f'Recording script missing: {script}')

    def test_vcrpy_importable(self):
        """vcrpy must be installed (in requirements.txt)."""
        try:
            import vcr  # noqa: F401
        except ImportError:
            self.fail('vcrpy not installed — pip install vcrpy '
                      '(should be in requirements.txt)')


# =============================================================================
# Cassette replay tests — auto-skip if cassette missing
# =============================================================================

class TestAddressOnlyCassette(unittest.TestCase):
    """Replay analyze_address_only.yaml — verify orchestrator output
    shape and persistence side effects."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User, Property, Analysis
        cls.app = app
        cls.db = db
        cls.User = User
        cls.Property = Property
        cls.Analysis = Analysis

    def setUp(self):
        if not _has_cassette('analyze_address_only.yaml'):
            self.skipTest(
                'Cassette analyze_address_only.yaml not yet recorded. '
                'Run: python test_cassettes/record_cassettes.py '
                '(with ANTHROPIC_API_KEY set)'
            )
        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            # v5.88.29: robust cleanup tolerates FK constraints on Postgres
            _cleanup_test_users(self.db, self.User, self.Property, self.Analysis)

    def tearDown(self):
        self.setUp()

    def _make_user_and_login(self, credits=10):
        with self.app.app_context():
            user = self.User(
                email=_unique_email('addr_cassette'), name='Addr',
                auth_provider='email', tier='free',
                analysis_credits=credits, analyses_completed=0,
            )
            user.set_password('CassetteTest123!')
            self.db.session.add(user)
            self.db.session.commit()
            uid = user.id
        _login_session(self.client, uid, password='CassetteTest123!')
        return uid

    def test_address_only_replay_returns_200_and_persists(self):
        """Cassette replay: address-only analyze runs end-to-end without
        making real API calls. Verify Property + Analysis persist and
        credits decrement."""
        import vcr
        uid = self._make_user_and_login(credits=10)

        cassette_path = CASSETTE_DIR / 'analyze_address_only.yaml'

        replay_vcr = vcr.VCR(
            cassette_library_dir=str(CASSETTE_DIR),
            record_mode='none',  # NEVER record during replay tests
            match_on=['method', 'scheme', 'host', 'path'],
        )

        with replay_vcr.use_cassette('analyze_address_only.yaml'):
            r = self.client.post('/api/analyze', json={
                'property_address': '123 Cassette Test Lane, San Jose, CA',
                'property_price': 500000,
            }, headers={'Origin': 'https://www.getofferwise.ai'})

        self.assertEqual(r.status_code, 200,
            f'Cassette replay should produce 200, got {r.status_code}: '
            f'{r.data[:300]!r}')

        data = r.get_json()
        # Verify orchestrator output shape — these are the contracts the
        # frontend depends on. If any of these fields disappear, the UI
        # breaks even if the analysis "succeeded".
        self.assertIn('risk_score', data,
            'Response must include risk_score (frontend dashboard contract)')
        self.assertIn('offer_strategy', data,
            'Response must include offer_strategy (frontend offer card contract)')

        # Verify persistence
        with self.app.app_context():
            prop = self.Property.query.filter_by(user_id=uid).first()
            if prop is None:
                # v5.88.30: when the assertion fails, surface what actually
                # happened on the server side. Past failure was "Property row
                # must persist on success" with no other context — useless.
                all_props_for_user = self.Property.query.filter_by(user_id=uid).count()
                props_by_address = self.Property.query.filter_by(
                    address='123 Cassette Test Lane, San Jose, CA'
                ).all()
                user_check = self.User.query.get(uid)
                # Check if any Analysis exists for this user
                from sqlalchemy import text
                try:
                    analysis_count = self.db.session.execute(
                        text('SELECT COUNT(*) FROM analyses a '
                             'JOIN properties p ON a.property_id = p.id '
                             'WHERE p.user_id = :uid'),
                        {'uid': uid}
                    ).scalar()
                except Exception as _qe:
                    analysis_count = f'query failed: {_qe}'

                diag = (
                    f'\n  --- v5.88.30 diagnostics ---\n'
                    f'  uid: {uid}\n'
                    f'  user exists: {user_check is not None}\n'
                    f'  user.credits if exists: {getattr(user_check, "analysis_credits", "n/a")}\n'
                    f'  Property count for uid: {all_props_for_user}\n'
                    f'  Properties by address: {len(props_by_address)} '
                    f'(user_ids: {[p.user_id for p in props_by_address[:5]]})\n'
                    f'  Analyses linked via user: {analysis_count}\n'
                    f'  Response status: {r.status_code}\n'
                    f'  Response top keys: {sorted(data.keys()) if isinstance(data, dict) else "n/a"}\n'
                    f'  Response analysis_id: {data.get("analysis_id") if isinstance(data, dict) else "n/a"}\n'
                    f'  Response property_id: {data.get("property_id") if isinstance(data, dict) else "n/a"}\n'
                )
                self.fail(f'Property row not found for user {uid}.{diag}')

            user = self.User.query.get(uid)
            self.assertEqual(user.analysis_credits, 9,
                f'Credits must decrement from 10 to 9, got {user.analysis_credits}')


class TestCleanDisclosureCassette(unittest.TestCase):
    """Replay analyze_clean_disclosure.yaml — full disclosure path
    with the clean test PDF (low expected risk)."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User, Property, Analysis
        cls.app = app
        cls.db = db
        cls.User = User
        cls.Property = Property
        cls.Analysis = Analysis

    def setUp(self):
        if not _has_cassette('analyze_clean_disclosure.yaml'):
            self.skipTest(
                'Cassette analyze_clean_disclosure.yaml not yet recorded. '
                'Run: python test_cassettes/record_cassettes.py'
            )

        pdf_path = Path(__file__).parent / 'test_corpus' / '01_digital_tds_clean.pdf'
        if not pdf_path.exists():
            self.skipTest(f'Test PDF missing: {pdf_path}')
        self.pdf_path = pdf_path

        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            # v5.88.29: robust cleanup tolerates FK constraints on Postgres
            _cleanup_test_users(self.db, self.User, self.Property, self.Analysis)

    def tearDown(self):
        self.setUp()

    def test_clean_disclosure_replay_orchestrator_output_shape(self):
        """Cassette replay catches prompt drift: if intelligence_engine.py
        changes its prompt in a way that breaks parsing, the cassette
        returns the OLD Anthropic response, parser fails, test fails."""
        import vcr
        import base64
        import PyPDF2

        with self.app.app_context():
            user = self.User(
                email=_unique_email('clean_cassette'), name='Clean',
                auth_provider='email', tier='free',
                analysis_credits=10,
            )
            user.set_password('CleanCassetteTest123!')
            self.db.session.add(user)
            self.db.session.commit()
            uid = user.id
        _login_session(self.client, uid, password='CleanCassetteTest123!')

        # v5.88.24: use seller_disclosure_text (the field /api/analyze
        # actually accepts) — same Bug A fix as the cassette recorder.
        with open(self.pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            disclosure_text = '\n'.join(p.extract_text() or '' for p in reader.pages)

        replay_vcr = vcr.VCR(
            cassette_library_dir=str(CASSETTE_DIR),
            record_mode='none',
            match_on=['method', 'scheme', 'host', 'path'],
        )

        with replay_vcr.use_cassette('analyze_clean_disclosure.yaml'):
            r = self.client.post('/api/analyze', json={
                'property_address': '456 Clean Disclosure St, Oakland, CA',
                'property_price': 750000,
                'seller_disclosure_text': disclosure_text,
            }, headers={'Origin': 'https://www.getofferwise.ai'})

        # If recording succeeded with 200, replay should match
        if r.status_code != 200:
            # Could be 502 from "no anthropic key" or similar in test env;
            # surface the error so the human knows what to fix
            self.fail(
                f'Cassette replay returned {r.status_code}, expected 200. '
                f'Response: {r.data[:500]!r}\n'
                f'This usually means the orchestrator code drifted from '
                f'when the cassette was recorded. Re-record via the admin page.'
            )

        data = r.get_json()
        # Verify the disclosure-only path was taken
        self.assertEqual(data.get('analysis_depth'), 'disclosure_only',
            f'Expected disclosure_only path, got {data.get("analysis_depth")}')
        # Top-level contract that frontend depends on
        self.assertIn('risk_score', data)
        self.assertIn('offer_strategy', data)


class TestNightmareDisclosureCassette(unittest.TestCase):
    """Replay analyze_nightmare_disclosure.yaml — disclosure with red
    flags (HIGH expected risk score). Verifies the orchestrator
    correctly elevates risk on adversarial input."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User, Property, Analysis
        cls.app = app
        cls.db = db
        cls.User = User
        cls.Property = Property
        cls.Analysis = Analysis

    def setUp(self):
        if not _has_cassette('analyze_nightmare_disclosure.yaml'):
            self.skipTest(
                'Cassette analyze_nightmare_disclosure.yaml not yet recorded.'
            )

        pdf_path = Path(__file__).parent / 'test_corpus' / '03_digital_tds_nightmare_no_disclosure.pdf'
        if not pdf_path.exists():
            self.skipTest(f'Test PDF missing: {pdf_path}')
        self.pdf_path = pdf_path

        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            # v5.88.29: robust cleanup tolerates FK constraints on Postgres
            _cleanup_test_users(self.db, self.User, self.Property, self.Analysis)

    def tearDown(self):
        self.setUp()

    def test_nightmare_disclosure_replay_produces_response_envelope(self):
        """Adversarial disclosure (no-disclosure red flags) must replay
        cleanly.

        v5.88.24: the previous assertion (risk_score >= 40) was WRONG for
        the disclosure-only path. The risk model requires inspection
        findings to compute weights (see risk_scoring_model.py:437 — no
        findings → returns 0.0). Disclosure-only legitimately produces
        risk=0. The risk-elevation assertion now lives on the full-path
        nightmare cassette (TestFullNightmareCassette), where inspection
        findings are present.

        This test only verifies the disclosure-only path replays without
        crashing and produces the expected response envelope."""
        import vcr

        with self.app.app_context():
            user = self.User(
                email=_unique_email('nightmare_cassette'), name='NM',
                auth_provider='email', tier='free',
                analysis_credits=10,
            )
            user.set_password('NMTest123!')
            self.db.session.add(user)
            self.db.session.commit()
            uid = user.id
        _login_session(self.client, uid, password='NMTest123!')

        # v5.88.24: use seller_disclosure_text (the field /api/analyze
        # actually accepts) — the previous version sent
        # seller_disclosure_pdf_base64 which was silently ignored,
        # causing all cassettes to fall through to address_only.
        import PyPDF2
        with open(self.pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            disclosure_text = '\n'.join(p.extract_text() or '' for p in reader.pages)

        replay_vcr = vcr.VCR(
            cassette_library_dir=str(CASSETTE_DIR),
            record_mode='none',
            match_on=['method', 'scheme', 'host', 'path'],
        )

        with replay_vcr.use_cassette('analyze_nightmare_disclosure.yaml'):
            r = self.client.post('/api/analyze', json={
                'property_address': '789 Nightmare Rd, Berkeley, CA',
                'property_price': 900000,
                'seller_disclosure_text': disclosure_text,
            }, headers={'Origin': 'https://www.getofferwise.ai'})

        if r.status_code != 200:
            self.fail(
                f'Nightmare cassette replay: {r.status_code}, expected 200. '
                f'Response: {r.data[:500]!r}'
            )

        data = r.get_json()
        # Verify the disclosure-only path was taken (not degenerate
        # address_only fallback)
        self.assertEqual(data.get('analysis_depth'), 'disclosure_only',
            f'Expected disclosure_only, got {data.get("analysis_depth")}. '
            f'PDF text may not have reached the orchestrator.')

        # Verify the response envelope has the expected risk_score shape
        risk_obj = data.get('risk_score') or {}
        self.assertIn('overall_risk_score', risk_obj,
            'risk_score must include overall_risk_score key')
        # Disclosure-only legitimately produces overall_risk_score=0
        # (no inspection findings → risk model returns 0). The
        # full-path nightmare cassette is where we assert risk > 40.


# =============================================================================
# v5.88.24: Full-path cassettes (disclosure + inspection)
# These exercise the risk-scoring model with real findings, which the
# disclosure-only path can't reach.
# =============================================================================

class TestFullCleanCassette(unittest.TestCase):
    """Replay analyze_full_clean.yaml — full disclosure + inspection
    path with the clean test PDFs (low expected risk)."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User, Property, Analysis
        cls.app = app
        cls.db = db
        cls.User = User
        cls.Property = Property
        cls.Analysis = Analysis

    def setUp(self):
        if not _has_cassette('analyze_full_clean.yaml'):
            self.skipTest(
                'Cassette analyze_full_clean.yaml not yet recorded. '
                'Run cassette recording via the admin page.'
            )

        disc_pdf = Path(__file__).parent / 'test_corpus' / '01_digital_tds_clean.pdf'
        insp_pdf = Path(__file__).parent / 'test_corpus' / '02_digital_inspection_clean.pdf'
        for p in (disc_pdf, insp_pdf):
            if not p.exists():
                self.skipTest(f'Test PDF missing: {p}')
        self.disc_pdf = disc_pdf
        self.insp_pdf = insp_pdf

        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            # v5.88.29: robust cleanup tolerates FK constraints on Postgres
            _cleanup_test_users(self.db, self.User, self.Property, self.Analysis)

    def tearDown(self):
        self.setUp()

    def test_full_clean_replay_takes_full_path(self):
        """Clean disclosure + clean inspection must replay cleanly and
        take the full orchestrator path (analysis_depth='full')."""
        import vcr
        import PyPDF2

        with self.app.app_context():
            user = self.User(
                email=_unique_email('full_clean'), name='FullClean',
                auth_provider='email', tier='free', analysis_credits=10,
            )
            user.set_password('FullCleanTest123!')
            self.db.session.add(user)
            self.db.session.commit()
            uid = user.id
        _login_session(self.client, uid, password='FullCleanTest123!')

        def _extract(pdf_path):
            with open(pdf_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                return '\n'.join(p.extract_text() or '' for p in reader.pages)

        disclosure_text = _extract(self.disc_pdf)
        inspection_text = _extract(self.insp_pdf)

        replay_vcr = vcr.VCR(
            cassette_library_dir=str(CASSETTE_DIR),
            record_mode='none',
            match_on=['method', 'scheme', 'host', 'path'],
        )

        with replay_vcr.use_cassette('analyze_full_clean.yaml'):
            r = self.client.post('/api/analyze', json={
                'property_address': '456 Clean Disclosure St, Oakland, CA',
                'property_price': 750000,
                'seller_disclosure_text': disclosure_text,
                'inspection_report_text': inspection_text,
            }, headers={'Origin': 'https://www.getofferwise.ai'})

        if r.status_code != 200:
            self.fail(
                f'Full clean replay: {r.status_code}. Response: {r.data[:500]!r}'
            )

        data = r.get_json()
        self.assertEqual(data.get('analysis_depth'), 'full',
            f'Expected full path, got {data.get("analysis_depth")}')


class TestFullNightmareCassette(unittest.TestCase):
    """Replay analyze_full_nightmare.yaml — adversarial disclosure +
    nightmare inspection. This is the headline cassette: it exercises
    the orchestrator end-to-end on adversarial input.

    If a future PR weakens the contradiction detector or risk model,
    this cassette's risk assertion fails."""

    @classmethod
    def setUpClass(cls):
        from app import app
        from models import db, User, Property, Analysis
        cls.app = app
        cls.db = db
        cls.User = User
        cls.Property = Property
        cls.Analysis = Analysis

    def setUp(self):
        if not _has_cassette('analyze_full_nightmare.yaml'):
            self.skipTest(
                'Cassette analyze_full_nightmare.yaml not yet recorded. '
                'Run cassette recording via the admin page.'
            )

        disc_pdf = Path(__file__).parent / 'test_corpus' / '03_digital_tds_nightmare_no_disclosure.pdf'
        insp_pdf = Path(__file__).parent / 'test_corpus' / '04_digital_inspection_nightmare.pdf'
        for p in (disc_pdf, insp_pdf):
            if not p.exists():
                self.skipTest(f'Test PDF missing: {p}')
        self.disc_pdf = disc_pdf
        self.insp_pdf = insp_pdf

        self.client = self.app.test_client(use_cookies=True)
        with self.app.app_context():
            # v5.88.29: robust cleanup tolerates FK constraints on Postgres
            _cleanup_test_users(self.db, self.User, self.Property, self.Analysis)

    def tearDown(self):
        self.setUp()

    def test_full_nightmare_produces_high_risk_score(self):
        """Adversarial disclosure + nightmare inspection must produce
        HIGH risk score (>= 40). If a future PR weakens the
        contradiction detector or risk model, this catches it."""
        import vcr
        import PyPDF2

        with self.app.app_context():
            user = self.User(
                email=_unique_email('full_nightmare'), name='FullNM',
                auth_provider='email', tier='free', analysis_credits=10,
            )
            user.set_password('FullNMTest123!')
            self.db.session.add(user)
            self.db.session.commit()
            uid = user.id
        _login_session(self.client, uid, password='FullNMTest123!')

        def _extract(pdf_path):
            with open(pdf_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                return '\n'.join(p.extract_text() or '' for p in reader.pages)

        disclosure_text = _extract(self.disc_pdf)
        inspection_text = _extract(self.insp_pdf)

        replay_vcr = vcr.VCR(
            cassette_library_dir=str(CASSETTE_DIR),
            record_mode='none',
            match_on=['method', 'scheme', 'host', 'path'],
        )

        with replay_vcr.use_cassette('analyze_full_nightmare.yaml'):
            r = self.client.post('/api/analyze', json={
                'property_address': '789 Nightmare Rd, Berkeley, CA',
                'property_price': 900000,
                'seller_disclosure_text': disclosure_text,
                'inspection_report_text': inspection_text,
            }, headers={'Origin': 'https://www.getofferwise.ai'})

        if r.status_code != 200:
            self.fail(
                f'Full nightmare replay: {r.status_code}. Response: {r.data[:500]!r}'
            )

        data = r.get_json()
        self.assertEqual(data.get('analysis_depth'), 'full',
            f'Expected full path, got {data.get("analysis_depth")}')

        risk_obj = data.get('risk_score') or {}
        risk = risk_obj.get('overall_risk_score')
        if risk is None:
            risk = risk_obj.get('composite_score', 0)
        self.assertGreaterEqual(risk, 40,
            f'Nightmare full-path should produce risk >= 40, got {risk}. '
            f'Either (a) the recording was made before the contradiction '
            f'detector was properly tuned (re-record), or (b) a code change '
            f'weakened risk scoring (investigate the diff).')


if __name__ == '__main__':
    unittest.main()
