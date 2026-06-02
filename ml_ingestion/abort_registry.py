"""Crawler abort registry — v5.89.34.

Central place where the drain worker registers a crawler instance for the
duration of its run, so the progress-staleness watcher in app.py can find
it and set its `_abort_requested` flag without import cycles.

Lives here (not in admin_routes.py) to avoid circular imports — app.py's
watcher imports this module, admin_routes.py's drain worker also imports
this module. Neither imports the other.

The registry is process-local and stateless across restarts. If a worker
restart loses an entry, the orphaned crawler subprocess will eventually
die with the worker — not a problem.

Concurrency: register/deregister/get/set_abort all hold an internal lock.
The set_abort + get_crawler dance is intentionally lock-protected to avoid
the case where a crawler deregisters between the watcher's lookup and the
abort-flag write.
"""
from __future__ import annotations
import threading
from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ml_ingestion.base import BaseIngestionJob


class CrawlAborted(Exception):
    """Raised when a crawler should stop because external code (typically
    the progress-staleness watcher in app.py) set its `_abort_requested`
    flag to True.

    Caught by the runner in admin_routes.py (`_run_existing_job`) so the
    MLIngestionJob row is marked failed cleanly rather than as an
    unexpected crash. Operator sees a clear "Aborted by watcher" error
    rather than a generic exception traceback.
    """
    pass


# Module-level state. Initialized empty; entries added by drain worker
# at job start, removed at job end (succeed/fail/abort — all paths).
_active_crawlers: Dict[int, 'BaseIngestionJob'] = {}
_lock = threading.RLock()  # reentrant in case of nested calls during abort


def register(job_id: int, crawler: 'BaseIngestionJob') -> None:
    """Mark `crawler` as actively running under MLIngestionJob.id=job_id.

    Called once at the start of `_run_existing_job` before the crawler's
    `run_job()` is invoked. After this point, the staleness watcher can
    find this crawler via `request_abort(job_id)`.

    Idempotent — re-registering the same job_id replaces the prior entry,
    which is what you want if a previous run leaked a registration.
    """
    with _lock:
        _active_crawlers[job_id] = crawler


def deregister(job_id: int) -> None:
    """Remove `job_id` from the registry. Called from the `finally`
    block of `_run_existing_job` so it fires on every exit path
    (succeed/fail/abort/exception). Safe to call when the id isn't
    present (idempotent)."""
    with _lock:
        _active_crawlers.pop(job_id, None)


def request_abort(job_id: int) -> bool:
    """Signal the crawler associated with `job_id` to abort at its next
    natural pause point (typically the next `_maybe_flush_progress` call,
    which happens every ~5 seconds during pagination).

    Returns True if the crawler was found and signaled; False if no
    crawler is currently registered for that id (either it finished
    already, or it was never started, or this process never knew about
    it — e.g. another web worker is running the drain).
    """
    with _lock:
        crawler = _active_crawlers.get(job_id)
        if crawler is None:
            return False
        # The crawler's __init__ sets _abort_requested = False. We're
        # flipping it to True here. The crawler checks it in
        # _maybe_flush_progress().
        crawler._abort_requested = True
        return True


def is_abort_requested(job_id: int) -> bool:
    """True if the crawler for `job_id` has had its abort flag set.

    Used by the runner (`_run_existing_job`) AFTER the crawler returns,
    to decide whether to honor a 'succeeded' result or override to
    'failed' (the case where the crawler finished its current batch
    successfully even though abort was requested mid-flight).
    """
    with _lock:
        crawler = _active_crawlers.get(job_id)
        if crawler is None:
            return False
        return bool(getattr(crawler, '_abort_requested', False))


def list_active() -> Dict[int, str]:
    """Return {job_id: SOURCE_NAME} for all currently-registered
    crawlers. Used for diagnostics / observability only — not for
    coordination."""
    with _lock:
        return {
            jid: getattr(c, 'SOURCE_NAME', '?')
            for jid, c in _active_crawlers.items()
        }


def clear_all() -> int:
    """Empty the registry. Returns the count of entries cleared. Only
    used in test setup/teardown — never call this from production code."""
    with _lock:
        n = len(_active_crawlers)
        _active_crawlers.clear()
        return n
