"""Tests for v5.89.34 cooperative cancellation of crawl jobs.

The flow under test:
  1. Drain worker registers a crawler instance via abort_registry.register
  2. Watcher (or operator) signals abort via abort_registry.request_abort
  3. Crawler's next _maybe_flush_progress call sees _abort_requested=True
     and raises CrawlAborted
  4. Runner catches CrawlAborted, marks job failed, deregisters
  5. Drain worker continues to next queued job

These tests don't need DB or Flask context — they exercise the
abort_registry + base.BaseIngestionJob._maybe_flush_progress directly.
"""
import time
import unittest
from unittest.mock import patch, MagicMock

from ml_ingestion.abort_registry import (
    register,
    deregister,
    request_abort,
    is_abort_requested,
    list_active,
    clear_all,
    CrawlAborted,
)


class _FakeCrawler:
    """Minimum surface needed by abort_registry.request_abort."""

    SOURCE_NAME = 'fake_test_crawler'

    def __init__(self):
        self._abort_requested = False
        self._rows_added = 0
        self._rows_processed = 0
        self._last_flush = 0.0
        self._FLUSH_INTERVAL_SECONDS = 5.0

    # Mimic BaseIngestionJob._maybe_flush_progress just enough to exercise
    # the abort path. We don't want to inherit from BaseIngestionJob here
    # because that pulls in DB/SQLAlchemy machinery the test doesn't need.
    def _maybe_flush_progress(self):
        if self._abort_requested:
            raise CrawlAborted(
                f'aborted (rows_added={self._rows_added}, '
                f'rows_processed={self._rows_processed})'
            )
        # Pretend we'd flush here. In real code this hits the DB.
        self._last_flush = time.time()


class TestAbortRegistry(unittest.TestCase):

    def setUp(self):
        clear_all()

    def tearDown(self):
        clear_all()

    def test_register_then_signal_sets_flag(self):
        c = _FakeCrawler()
        register(42, c)
        self.assertFalse(c._abort_requested)
        result = request_abort(42)
        self.assertTrue(result, 'request_abort should return True when crawler is registered')
        self.assertTrue(c._abort_requested)

    def test_signal_unknown_job_returns_false(self):
        result = request_abort(999)
        self.assertFalse(result)

    def test_deregister_removes_entry(self):
        c = _FakeCrawler()
        register(7, c)
        deregister(7)
        self.assertFalse(request_abort(7))

    def test_deregister_idempotent(self):
        # Should not raise for unregistered id
        deregister(123)
        deregister(123)

    def test_is_abort_requested_reflects_flag_state(self):
        c = _FakeCrawler()
        register(11, c)
        self.assertFalse(is_abort_requested(11))
        request_abort(11)
        self.assertTrue(is_abort_requested(11))

    def test_is_abort_requested_for_unknown_id_returns_false(self):
        self.assertFalse(is_abort_requested(404))

    def test_list_active_returns_source_names(self):
        c1 = _FakeCrawler()
        c1.SOURCE_NAME = 'crawler_one'
        c2 = _FakeCrawler()
        c2.SOURCE_NAME = 'crawler_two'
        register(1, c1)
        register(2, c2)
        active = list_active()
        self.assertEqual(active, {1: 'crawler_one', 2: 'crawler_two'})

    def test_register_same_id_replaces(self):
        c1 = _FakeCrawler()
        c2 = _FakeCrawler()
        register(5, c1)
        register(5, c2)  # replaces
        request_abort(5)
        self.assertFalse(c1._abort_requested,
                         'original crawler should not be signaled — it was replaced')
        self.assertTrue(c2._abort_requested,
                        'replacement crawler should be signaled')


class TestAbortViaFlush(unittest.TestCase):
    """End-to-end exercise: register → signal → flush_progress raises."""

    def setUp(self):
        clear_all()

    def tearDown(self):
        clear_all()

    def test_flush_raises_after_abort(self):
        c = _FakeCrawler()
        register(100, c)
        c._maybe_flush_progress()  # no abort yet; should not raise
        request_abort(100)
        with self.assertRaises(CrawlAborted) as ctx:
            c._maybe_flush_progress()
        self.assertIn('rows_added', str(ctx.exception))

    def test_flush_does_not_raise_before_abort(self):
        c = _FakeCrawler()
        register(200, c)
        for _ in range(5):
            c._maybe_flush_progress()  # all should succeed

    def test_abort_message_includes_progress(self):
        c = _FakeCrawler()
        c._rows_added = 1500
        c._rows_processed = 2000
        register(300, c)
        request_abort(300)
        with self.assertRaises(CrawlAborted) as ctx:
            c._maybe_flush_progress()
        self.assertIn('1500', str(ctx.exception))
        self.assertIn('2000', str(ctx.exception))


class TestRealBaseIngestionJobAbort(unittest.TestCase):
    """Use the actual BaseIngestionJob class (not a fake) to verify the
    real _maybe_flush_progress honors the abort flag. This exercises the
    real code path that crawlers use in production."""

    def setUp(self):
        clear_all()

    def tearDown(self):
        clear_all()

    def test_real_base_class_aborts(self):
        # Import inside test to avoid module-level Flask app context
        # requirement from any of base.py's imports
        from ml_ingestion.base import BaseIngestionJob

        # Subclass since BaseIngestionJob requires run_job() implementation
        class _MinimalJob(BaseIngestionJob):
            JOB_TYPE = 'test'
            SOURCE_NAME = 'test_minimal'
            def run_job(self):
                # Loop calling _maybe_flush_progress until it raises
                for _ in range(100):
                    self._maybe_flush_progress()
                    time.sleep(0.01)
                # If we get here, abort didn't fire — fail the test
                raise AssertionError('expected CrawlAborted but loop completed')

        job = _MinimalJob()
        job._rows_added = 42

        # Bypass _last_flush debounce so abort check is reached on every call
        job._FLUSH_INTERVAL_SECONDS = 0.0

        # Patch _flush_progress so we don't need a DB
        with patch.object(job, '_flush_progress'):
            register(555, job)
            # Signal abort, then call _maybe_flush_progress directly.
            # Should raise immediately (the abort check is BEFORE the
            # debounce check, so timing doesn't matter).
            request_abort(555)
            with self.assertRaises(CrawlAborted):
                job._maybe_flush_progress()


if __name__ == '__main__':
    unittest.main()
