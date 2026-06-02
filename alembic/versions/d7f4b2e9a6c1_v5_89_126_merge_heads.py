"""v5_89_126_merge_heads

Revision ID: d7f4b2e9a6c1
Revises: b2d5e8a1c4f7, c9d5f2b8a173
Create Date: 2026-06-02 09:30:00.000000

Merge the two open alembic heads into one so `alembic upgrade head` resolves
again. This is a pure bookkeeping merge — NO schema changes.

Why this exists
---------------
The migration graph had diverged into two genuine heads:
  - c9d5f2b8a173 (v5.88.88 offerwatch_columns) — the main line; prod's
    alembic_version is stamped here.
  - b2d5e8a1c4f7 (v5.89.86 reasoning_tiers) — an orphan branched off the old
    v5.87.38 node (a1c4e7f9b2d6), never folded back into the main line.

Because both were open heads, `command.upgrade(cfg, 'head')` (run by
scripts/bootstrap_alembic.py on every deploy) raised
alembic.util.exc.CommandError: "Multiple head revisions are present", so the
upgrade step silently failed on deploy. There was no immediate breakage only
because neither branch had pending DDL the DB lacked — confirmed live via
/api/admin/db-migration-status: current_revision_in_db=c9d5f2b8a173,
drift_count=0, schema_drift=[]. That is a latent landmine: the next real
migration would not have applied. This merge defuses it.

Safety
------
Both branches are additive and idempotent, so applying b2d5e8a1c4f7 on the way
to this merge (prod is on the c9d5f2b8a173 side) is a guaranteed no-op:
  - b2d5e8a1c4f7 creates reasoning_* tables, each guarded by an inspector
    `if 'table' not in existing` check (and its docstring explicitly notes it is
    safe on DBs where db.create_all() already created them — which is prod's
    case, hence drift 0).
  - c9d5f2b8a173 adds columns via `ADD COLUMN IF NOT EXISTS`.
This merge itself performs no operations; it only unifies the lineages.
"""
from typing import Sequence, Union


revision: str = 'd7f4b2e9a6c1'
down_revision: Union[str, Sequence[str], None] = ('b2d5e8a1c4f7', 'c9d5f2b8a173')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: pure head merge. All schema already present (drift 0)."""
    pass


def downgrade() -> None:
    """No-op: a merge revision has nothing to undo. To split the lineages
    again, remove this file and re-stamp — do not attempt a data/schema
    rollback here."""
    pass
