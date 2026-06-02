"""Tests for v5.89.47 relabel_pipeline module.

Like test_access_gate.py, split into:
  1. Pure-function tests — no DB, no Flask context needed
  2. DB-backed tests — uses app context, skips on import failures

Coverage check requires this file PLUS a `from relabel_pipeline import` line
somewhere in integrity_tests.py — that string-match is what the coverage
sub-test scans for. See _test_relabel_pipeline() in integrity_tests.py.

Added/updated in v5.89.48 in response to coverage check flagging
relabel_pipeline as untested after v5.89.47 added it.
"""
import os
import sys
import unittest

os.environ['FLASK_ENV'] = 'testing'
os.environ['SECRET_KEY'] = 'test-secret-relabel-pipeline'
os.environ['DATABASE_URL'] = 'sqlite:///test_relabel_pipeline.db'
os.environ.setdefault('ADMIN_KEY', 'test-admin-key-relabel-pipeline')

_db_path = 'test_relabel_pipeline.db'
if os.path.exists(_db_path):
    os.remove(_db_path)


# ─────────────────────────────────────────────────────────────────────
# Layer 1: pure-function tests (no DB)
# ─────────────────────────────────────────────────────────────────────

class TestValidateDbUrlOrDie(unittest.TestCase):
    """v5.89.47 includes _validate_db_url_or_die — same pattern as
    ml_training_pipeline's validator (v5.89.41)."""

    def setUp(self):
        self._saved_db_url = os.environ.get('DATABASE_URL')

    def tearDown(self):
        if self._saved_db_url is not None:
            os.environ['DATABASE_URL'] = self._saved_db_url
        else:
            os.environ.pop('DATABASE_URL', None)

    def test_accepts_postgres(self):
        from relabel_pipeline import _validate_db_url_or_die
        os.environ['DATABASE_URL'] = 'postgres://user@host/db'
        try:
            _validate_db_url_or_die()
        except SystemExit:
            self.fail('rejected legitimate postgres:// URL')

    def test_accepts_postgresql(self):
        from relabel_pipeline import _validate_db_url_or_die
        os.environ['DATABASE_URL'] = 'postgresql://user@host/db'
        try:
            _validate_db_url_or_die()
        except SystemExit:
            self.fail('rejected legitimate postgresql:// URL')

    def test_accepts_postgresql_dialect(self):
        from relabel_pipeline import _validate_db_url_or_die
        os.environ['DATABASE_URL'] = 'postgresql+psycopg2://user@host/db'
        try:
            _validate_db_url_or_die()
        except SystemExit:
            self.fail('rejected legitimate postgresql+psycopg2:// URL')

    def test_rejects_sqlite(self):
        from relabel_pipeline import _validate_db_url_or_die
        os.environ['DATABASE_URL'] = 'sqlite:///test.db'
        with self.assertRaises(SystemExit) as ctx:
            _validate_db_url_or_die()
        self.assertEqual(ctx.exception.code, 2)

    def test_rejects_empty(self):
        from relabel_pipeline import _validate_db_url_or_die
        os.environ.pop('DATABASE_URL', None)
        with self.assertRaises(SystemExit) as ctx:
            _validate_db_url_or_die()
        self.assertEqual(ctx.exception.code, 2)

    def test_rejects_unknown_scheme(self):
        from relabel_pipeline import _validate_db_url_or_die
        os.environ['DATABASE_URL'] = 'mysql://user@host/db'
        with self.assertRaises(SystemExit) as ctx:
            _validate_db_url_or_die()
        self.assertEqual(ctx.exception.code, 2)


class TestModuleImports(unittest.TestCase):
    """Sanity: the module imports cleanly and exposes its expected API."""

    def test_module_imports(self):
        import relabel_pipeline
        self.assertTrue(hasattr(relabel_pipeline, '_validate_db_url_or_die'))
        self.assertTrue(hasattr(relabel_pipeline, '_load_artifacts'))
        self.assertTrue(hasattr(relabel_pipeline, '_predict_chunk'))
        self.assertTrue(hasattr(relabel_pipeline, 'run_relabel'))
        self.assertTrue(hasattr(relabel_pipeline, 'main'))

    def test_constants_reasonable(self):
        import relabel_pipeline
        self.assertGreater(relabel_pipeline.CHUNK_SIZE, 0)
        self.assertLessEqual(relabel_pipeline.CHUNK_SIZE, 5000)
        self.assertGreater(relabel_pipeline.PROGRESS_COMMIT_EVERY, 0)


# ─────────────────────────────────────────────────────────────────────
# Layer 2: DB-backed lifecycle tests
# ─────────────────────────────────────────────────────────────────────

class TestRelabelRunLifecycle(unittest.TestCase):
    """Verify MLRelabelRun model lifecycle (create, transition, persist).
    Doesn't run the relabel pipeline itself — that needs trained models +
    sentence-transformer which we don't have in unit tests."""

    @classmethod
    def setUpClass(cls):
        try:
            from app import app
            from models import db, MLRelabelRun
            cls.app = app
            cls.db = db
            cls.MLRelabelRun = MLRelabelRun
            cls.available = True
        except Exception as e:
            cls.available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.available:
            self.skipTest(f"App not available: {self.skip_reason}")
        with self.app.app_context():
            self.MLRelabelRun.query.delete()
            self.db.session.commit()

    def tearDown(self):
        if self.available:
            with self.app.app_context():
                self.MLRelabelRun.query.delete()
                self.db.session.commit()

    def test_create_run_with_required_fields(self):
        import uuid
        with self.app.app_context():
            run = self.MLRelabelRun(
                job_id=str(uuid.uuid4()),
                mode='dry_run',
                confidence_threshold=0.90,
                triggered_by='test',
                status='queued',
            )
            self.db.session.add(run)
            self.db.session.commit()
            self.assertIsNotNone(run.id)
            self.assertEqual(run.status, 'queued')
            self.assertEqual(run.rows_processed, 0)
            self.assertFalse(run.cancel_requested)

    def test_status_transitions(self):
        import uuid
        from datetime import datetime
        with self.app.app_context():
            run = self.MLRelabelRun(
                job_id=str(uuid.uuid4()),
                mode='commit',
                confidence_threshold=0.85,
                triggered_by='test',
                status='queued',
            )
            self.db.session.add(run)
            self.db.session.commit()

            run.status = 'running'
            run.started_at = datetime.utcnow()
            self.db.session.commit()
            self.assertEqual(run.status, 'running')

            run.status = 'completed'
            run.completed_at = datetime.utcnow()
            run.rows_processed = 100
            run.rows_changed_category = 25
            self.db.session.commit()
            self.assertEqual(run.status, 'completed')
            self.assertEqual(run.rows_changed_category, 25)

    def test_cancel_request_persists(self):
        import uuid
        with self.app.app_context():
            run = self.MLRelabelRun(
                job_id=str(uuid.uuid4()),
                mode='dry_run',
                confidence_threshold=0.90,
                triggered_by='test',
                status='running',
            )
            self.db.session.add(run)
            self.db.session.commit()

            run.cancel_requested = True
            self.db.session.commit()

            re_fetched = self.MLRelabelRun.query.filter_by(job_id=run.job_id).first()
            self.assertTrue(re_fetched.cancel_requested)

    def test_error_message_with_v5_89_48_marker_stored(self):
        # v5.89.48 added a minimum-fallback diagnostic with a version
        # marker so operators can verify the deployed code is current.
        import uuid
        with self.app.app_context():
            err_msg = 'Failed to load model artifacts. [v5.89.48 minimum fallback]\ncwd=/app\n...'
            run = self.MLRelabelRun(
                job_id=str(uuid.uuid4()),
                mode='dry_run',
                confidence_threshold=0.90,
                triggered_by='test',
                status='failed',
                error_message=err_msg,
            )
            self.db.session.add(run)
            self.db.session.commit()

            re_fetched = self.MLRelabelRun.query.filter_by(job_id=run.job_id).first()
            self.assertIn('v5.89.48', re_fetched.error_message)


class TestNoYieldPerInPipeline(unittest.TestCase):
    """v5.89.49 regression marker.

    The v5.89.47 pipeline used .yield_per() to stream the corpus, which
    creates a Postgres named server-side cursor. Named cursors die when
    you commit() — which we do every 5 chunks for progress reporting.
    After 2,500 rows the next fetch crashed with
    "named cursor isn't valid anymore".

    This test guards against the bug coming back: scans the source file
    and refuses to allow `.yield_per(` to appear in relabel_pipeline.py.

    If a future change legitimately needs yield_per, it must also handle
    cursor invalidation — at which point this test should be replaced
    with one that exercises the commit-during-iteration path end-to-end.
    """

    def test_no_yield_per_in_relabel_pipeline(self):
        import relabel_pipeline
        src_path = relabel_pipeline.__file__
        with open(src_path, 'r') as f:
            src = f.read()
        # Allow yield_per to appear in comments but not in code.
        # Simple heuristic: split by lines, ignore comment-only lines.
        offending_lines = []
        for i, line in enumerate(src.split('\n'), 1):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if '.yield_per(' in line:
                offending_lines.append((i, line.strip()))
        self.assertEqual(
            offending_lines, [],
            f"yield_per() reintroduced into relabel_pipeline.py — see v5.89.49 "
            f"changelog for context. Offending lines: {offending_lines}"
        )


if __name__ == '__main__':
    unittest.main()
