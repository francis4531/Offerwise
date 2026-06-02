"""v5_87_77_wipe_inflated_reddit_ads_rows

Revision ID: c5e7d9a3b1f8
Revises: a8b3c1f5d9e2
Create Date: 2026-05-04 22:30:00.000000

Wipes all existing rows in gtm_ad_performance where channel='reddit_ads'.
Pre-v5.87.76 sync code persisted multi-day aggregated totals as single-day
rows, causing ~3x inflation in reported spend, clicks, and impressions.
The v5.87.76 sync now correctly fetches one day at a time, but the
already-persisted inflated rows must be wiped so the next scheduled sync
(every 6 hours via APScheduler `_ads_job`) can repopulate the table with
accurate per-day data.

After this migration runs:
  - DB has zero reddit_ads rows
  - Within the next 6 hours (or sooner if operator manually triggers
    /api/admin/reddit-ads-sync), APScheduler fires reddit_ads_sync.sync_to_db
    which uses v5.87.76's per-day fetch loop to populate the last 3 days
    accurately
  - Operator can also force a longer backfill by temporarily increasing
    lookback_days, then reverting

This is idempotent — running twice is a no-op (second run finds zero rows
to delete). Google Ads, Zillow Ads, and other channels are untouched.

Why a migration and not a one-off script:
  Bug fixes belong in the build. Operating against production via shell
  pastes or hand-invoked scripts has no audit trail, no review, no
  staging path, no rollback. A migration runs automatically on deploy,
  is idempotent, has a revision ID, and can be validated in CI.
"""
from typing import Sequence, Union
from alembic import op
import logging


revision: str = 'c5e7d9a3b1f8'
down_revision: Union[str, None] = 'a8b3c1f5d9e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Wipe inflated reddit_ads rows. Logs row count for audit."""
    log = logging.getLogger('alembic.runtime.migration')
    bind = op.get_bind()

    # Count first so we have a clear audit log of what we wiped
    try:
        result = bind.execute(
            "SELECT COUNT(*) FROM gtm_ad_performance WHERE channel = 'reddit_ads'"
        )
        row = result.fetchone()
        before_count = row[0] if row else 0
    except Exception as e:
        # Table might not exist yet on a fresh install — that's fine.
        log.info(f'v5.87.77: gtm_ad_performance read failed (likely fresh DB): {e}')
        return

    if before_count == 0:
        log.info('v5.87.77: zero reddit_ads rows to wipe (idempotent no-op)')
        return

    # Capture spend total for the audit log so we can compare
    # against Reddit's own dashboard later if needed
    try:
        result = bind.execute(
            "SELECT COALESCE(SUM(spend), 0) FROM gtm_ad_performance "
            "WHERE channel = 'reddit_ads'"
        )
        row = result.fetchone()
        before_spend = float(row[0]) if row and row[0] is not None else 0.0
    except Exception:
        before_spend = -1.0  # unknown; not critical

    op.execute("DELETE FROM gtm_ad_performance WHERE channel = 'reddit_ads'")
    log.info(
        f'v5.87.77: wiped {before_count} inflated reddit_ads rows '
        f'(${before_spend:.2f} total inflated spend). Scheduler will '
        f'repopulate within 6 hours via reddit_ads_sync per-day fetch.'
    )


def downgrade() -> None:
    """No downgrade. The deleted rows were corrupt; reverting would
    re-introduce bad data. If we need to roll back v5.87.76's per-day
    sync, do it via code rollback, not by restoring DB state."""
    pass
