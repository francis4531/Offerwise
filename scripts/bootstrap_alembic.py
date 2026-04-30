#!/usr/bin/env python3
"""Alembic bootstrap for production.

Context: before v5.86.71 the Dockerfile ran gunicorn directly, so Alembic
migrations NEVER ran on production. Production has been operating on
`db.create_all()` alone — which creates tables but does NOT add columns to
existing tables. Any Alembic migration that added columns was silently
skipped for months.

This script runs at container startup (in the Dockerfile CMD) and does the
following, in order:

1. Check whether alembic_version table exists in the DB.
2. If NO (first run after v5.86.71 deploy on an existing prod DB):
   stamp at the initial full-schema revision `1f857ccea478` so Alembic
   recognizes that the base tables already exist. This avoids the
   create_table conflict that would otherwise occur.
3. Run `alembic upgrade head` to apply all pending migrations. On the
   first run post-stamp, this applies v5.86.67, v5.86.68, v5.86.69 (all
   idempotent by design). On subsequent runs, no-op if already at head.

Failure handling: if anything in here crashes, the container still starts
(we call gunicorn next in the Dockerfile regardless). That's intentional —
if migrations fail, the user gets a running app with stale schema that
will show errors in the Diagnostics panel rather than a container that
won't come up. Better visibility, same underlying problem.

Idempotent. Safe to run on every deploy.
"""
from __future__ import annotations

import os
import sys
import traceback


def main() -> int:
    db_url = os.environ.get('DATABASE_URL', '')
    if not db_url:
        print('[bootstrap_alembic] No DATABASE_URL set — skipping (dev/local?).', file=sys.stderr)
        return 0

    # Heads-up if we're on SQLite in production (don't block, just warn)
    if db_url.startswith('sqlite:'):
        print('[bootstrap_alembic] SQLite detected — proceeding, but migrations ' +
              'are intended for Postgres production.', file=sys.stderr)

    # Normalize postgres:// → postgresql+psycopg2:// for SQLAlchemy 2.x
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)

    try:
        from sqlalchemy import create_engine, inspect, text
    except Exception as e:
        print(f'[bootstrap_alembic] SQLAlchemy import failed: {e}', file=sys.stderr)
        return 0  # don't block container start

    try:
        engine = create_engine(db_url)
    except Exception as e:
        print(f'[bootstrap_alembic] engine create failed: {e}', file=sys.stderr)
        return 0

    # ── Step 1: check for alembic_version table ───────────────────────
    has_alembic_table = False
    has_legacy_tables = False
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        has_alembic_table = 'alembic_version' in tables
        # "Legacy" = we detect any of the tables that would exist on a
        # long-running prod install. This tells us whether we need to stamp.
        has_legacy_tables = bool(tables & {'users', 'ml_finding_labels', 'ml_training_runs'})
    except Exception as e:
        print(f'[bootstrap_alembic] inspect failed: {e}', file=sys.stderr)
        return 0

    print(f'[bootstrap_alembic] has_alembic_table={has_alembic_table} has_legacy_tables={has_legacy_tables}', file=sys.stderr)

    # ── Step 2: stamp if needed ───────────────────────────────────────
    if not has_alembic_table and has_legacy_tables:
        # Prod DB has tables but no alembic tracking. Stamp at the
        # original full-schema revision so Alembic knows the base tables
        # exist, then migrations from that point forward can apply.
        print('[bootstrap_alembic] Legacy prod DB detected. Stamping at 1f857ccea478...', file=sys.stderr)
        try:
            from alembic.config import Config
            from alembic import command
            cfg = Config(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'alembic.ini'))
            cfg.set_main_option('sqlalchemy.url', db_url)
            command.stamp(cfg, '1f857ccea478')
            print('[bootstrap_alembic] Stamp OK.', file=sys.stderr)
        except Exception as e:
            print(f'[bootstrap_alembic] Stamp failed: {e}', file=sys.stderr)
            print(traceback.format_exc(), file=sys.stderr)
            return 0  # don't block container

    # ── Step 3: alembic upgrade head ──────────────────────────────────
    try:
        from alembic.config import Config
        from alembic import command
        cfg = Config(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'alembic.ini'))
        cfg.set_main_option('sqlalchemy.url', db_url)
        print('[bootstrap_alembic] Running alembic upgrade head...', file=sys.stderr)
        command.upgrade(cfg, 'head')
        print('[bootstrap_alembic] Upgrade OK.', file=sys.stderr)
    except Exception as e:
        print(f'[bootstrap_alembic] Upgrade failed: {e}', file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        # Still don't block the container — let the app surface the problem
        # via the Diagnostics panel rather than failing to start entirely.
        return 0

    return 0


if __name__ == '__main__':
    sys.exit(main())
