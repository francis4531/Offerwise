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

v5.88.51 update: write the bootstrap result to a JSON file at
/tmp/bootstrap_alembic_status.json so the admin /api/admin/db-health
endpoint can surface migration failures in the UI. Before this, a failed
migration silently caused 'column does not exist' errors at runtime with
no visible signal that the schema was behind code.

Failure handling: if anything in here crashes, the container still starts
(we call gunicorn next in the Dockerfile regardless). That's intentional —
if migrations fail, the user gets a running app with stale schema that
will show errors in the Diagnostics panel rather than a container that
won't come up. Better visibility, same underlying problem.

Idempotent. Safe to run on every deploy.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback

STATUS_FILE = '/tmp/bootstrap_alembic_status.json'


def _write_status(payload: dict) -> None:
    """Persist the bootstrap result so the admin UI can read it."""
    payload['ts'] = time.time()
    try:
        with open(STATUS_FILE, 'w') as fh:
            json.dump(payload, fh, indent=2, default=str)
    except Exception as e:
        print(f'[bootstrap_alembic] write status file failed: {e}', file=sys.stderr)


def main() -> int:
    db_url = os.environ.get('DATABASE_URL', '')
    if not db_url:
        print('[bootstrap_alembic] No DATABASE_URL set — skipping (dev/local?).', file=sys.stderr)
        _write_status({'ok': True, 'skipped': 'no DATABASE_URL'})
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
        _write_status({'ok': False, 'stage': 'sqlalchemy_import', 'error': str(e)})
        return 0  # don't block container start

    try:
        engine = create_engine(db_url)
    except Exception as e:
        print(f'[bootstrap_alembic] engine create failed: {e}', file=sys.stderr)
        _write_status({'ok': False, 'stage': 'engine_create', 'error': str(e)})
        return 0

    # ── Step 1: check for alembic_version table ───────────────────────
    has_alembic_table = False
    has_legacy_tables = False
    current_revision = None
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        has_alembic_table = 'alembic_version' in tables
        # "Legacy" = we detect any of the tables that would exist on a
        # long-running prod install. This tells us whether we need to stamp.
        has_legacy_tables = bool(tables & {'users', 'ml_finding_labels', 'ml_training_runs'})
        if has_alembic_table:
            with engine.connect() as conn:
                row = conn.execute(text('SELECT version_num FROM alembic_version LIMIT 1')).fetchone()
                if row:
                    current_revision = row[0]
    except Exception as e:
        print(f'[bootstrap_alembic] inspect failed: {e}', file=sys.stderr)
        _write_status({'ok': False, 'stage': 'inspect', 'error': str(e)})
        return 0

    print(f'[bootstrap_alembic] has_alembic_table={has_alembic_table} has_legacy_tables={has_legacy_tables} current_revision={current_revision}', file=sys.stderr)

    # ── Step 1.5 (v5.88.52): one-shot bypass for stuck merge ──────────
    # Background: in commit history we shipped two SIBLING migrations on
    # the same parent (d5a1f8c3b9e7):
    #   e9a3c5d7f1b8  (v5.87.72 columns variant — ran successfully on prod)
    #   e6b2a9f4c8d3  (v5.87.72 sister — was supposed to be merged via a8b3c1f5d9e2)
    # Both add the same 4 columns to infra_invoices, with idempotency guards.
    # The merge migration a8b3c1f5d9e2 requires BOTH siblings to be applied.
    # Production ran e9a3c5d7f1b8 and stopped there. On every subsequent
    # deploy, alembic tries to apply e6b2a9f4c8d3 — its column-add guards
    # succeed (columns already there) but then a raw op.execute UPDATE
    # statement inside the migration poisons the Postgres transaction
    # (InFailedSqlTransaction). The migration body's try/except catches
    # the Python-level exception, but Postgres rejects the subsequent
    # alembic_version INSERT statement, so alembic aborts and never reaches
    # any later migration. This left v5.88.47/.50/.51 unapplied for weeks.
    #
    # Fix: if current_revision is e9a3c5d7f1b8, stamp directly at the merge
    # revision a8b3c1f5d9e2 — the schema is already at that state because
    # e9a3c5d7f1b8 added every column e6b2a9f4c8d3 would have. Then upgrade
    # head continues forward from the merge point.
    STUCK_REV = 'e9a3c5d7f1b8'
    POST_MERGE_REV = 'a8b3c1f5d9e2'
    if current_revision == STUCK_REV:
        print(f'[bootstrap_alembic] Detected stuck merge state at {STUCK_REV}. '
              f'Stamping directly at {POST_MERGE_REV} to bypass the '
              f'transaction-poisoning sibling e6b2a9f4c8d3...', file=sys.stderr)
        try:
            from alembic.config import Config
            from alembic import command
            cfg = Config(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'alembic.ini'))
            cfg.set_main_option('sqlalchemy.url', db_url)
            command.stamp(cfg, POST_MERGE_REV)
            current_revision = POST_MERGE_REV
            print(f'[bootstrap_alembic] Stamp to {POST_MERGE_REV} OK. '
                  f'Upgrade head should now reach v5.88.51.', file=sys.stderr)
        except Exception as e:
            print(f'[bootstrap_alembic] Bypass stamp failed: {e}', file=sys.stderr)
            print(traceback.format_exc(), file=sys.stderr)
            _write_status({
                'ok': False, 'stage': 'bypass_stamp', 'error': str(e),
                'traceback': traceback.format_exc(),
                'current_revision': current_revision,
            })
            return 0

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
            _write_status({
                'ok': False, 'stage': 'stamp', 'error': str(e),
                'traceback': traceback.format_exc(),
                'current_revision': current_revision,
            })
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

        # Read the new HEAD revision for the status file
        new_revision = None
        try:
            with engine.connect() as conn:
                row = conn.execute(text('SELECT version_num FROM alembic_version LIMIT 1')).fetchone()
                if row:
                    new_revision = row[0]
        except Exception:
            pass
        _write_status({
            'ok': True, 'stage': 'upgrade_complete',
            'previous_revision': current_revision,
            'current_revision': new_revision,
        })
    except Exception as e:
        print(f'[bootstrap_alembic] Upgrade failed: {e}', file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        _write_status({
            'ok': False, 'stage': 'upgrade', 'error': str(e),
            'traceback': traceback.format_exc(),
            'current_revision': current_revision,
        })
        # Still don't block the container — let the app surface the problem
        # via the Diagnostics panel rather than failing to start entirely.
        return 0

    return 0


if __name__ == '__main__':
    sys.exit(main())
