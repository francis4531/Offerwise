"""v5_89_126_wipe_misscaled_reddit_ads_rows

Revision ID: f1a9c3e7b5d2
Revises: d7f4b2e9a6c1
Create Date: 2026-06-02 09:31:00.000000

Wipe all rows in gtm_ad_performance where channel='reddit_ads' so the corrected
v5.89.125 sync can repopulate them accurately from the Reddit Ads API.

Why
---
The Reddit Ads API v3 returns `spend` in MICROCURRENCY (millionths). The sync
prior to v5.89.125 assumed dollars and only divided by 1e6 via a fragile
">10,000 looks like micros" heuristic, which SILENTLY mis-scaled small-spend
days (e.g. $0.008 = 8000 micros, under the threshold, stored as $8,000).
Already-persisted reddit_ads rows are therefore untrustworthy. v5.89.125 now
divides spend by 1e6 unconditionally; this migration clears the bad rows so the
next sync rebuilds from the API (the source of truth).

After this runs
---------------
  - DB has zero reddit_ads rows.
  - Within 6 hours (APScheduler `_ads_job`), or sooner via a manual
    /api/admin/reddit-ads-sync, reddit_ads_sync.sync_to_db repopulates the
    recent window with correctly-scaled spend. Note: Reddit's report window is
    finite (~28 days), so history older than the API still returns will not be
    rebuilt — an accepted trade since the old values were wrong anyway.
  - Google Ads, Zillow Ads, internachi, and all other channels are untouched.

Idempotent: running twice is a no-op (second run finds zero rows). Mirrors the
prior c5e7d9a3b1f8 wipe. Bug fixes belong in the build with a revision ID and an
audit trail — not a shell paste against prod.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import logging


revision: str = 'f1a9c3e7b5d2'
down_revision: Union[str, None] = 'd7f4b2e9a6c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Wipe mis-scaled reddit_ads rows. Logs count + spend total for audit."""
    log = logging.getLogger('alembic.runtime.migration')
    bind = op.get_bind()

    # Count first for a clear audit log. Table may not exist on a fresh DB.
    try:
        before_count = bind.execute(
            sa.text("SELECT COUNT(*) FROM gtm_ad_performance WHERE channel = 'reddit_ads'")
        ).scalar() or 0
    except Exception as e:
        log.info(f'v5.89.126: gtm_ad_performance read failed (likely fresh DB): {e}')
        return

    if before_count == 0:
        log.info('v5.89.126: zero reddit_ads rows to wipe (idempotent no-op)')
        return

    try:
        before_spend = float(bind.execute(
            sa.text("SELECT COALESCE(SUM(spend), 0) FROM gtm_ad_performance WHERE channel = 'reddit_ads'")
        ).scalar() or 0.0)
    except Exception:
        before_spend = -1.0  # unknown; not critical

    op.execute(sa.text("DELETE FROM gtm_ad_performance WHERE channel = 'reddit_ads'"))
    log.info(
        f'v5.89.126: wiped {before_count} mis-scaled reddit_ads rows '
        f'(${before_spend:.2f} total stored spend). Scheduler will repopulate '
        f'within 6 hours via reddit_ads_sync with corrected micro->dollar scaling.'
    )


def downgrade() -> None:
    """No downgrade. The deleted rows were mis-scaled; restoring them would
    re-introduce bad data. Roll back via code, not DB state."""
    pass
