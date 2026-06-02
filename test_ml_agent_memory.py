"""test_ml_agent_memory.py — Static guards against the v5.87.44 OOM regression.

The 3am ML training agent has crashed with OOM on Render's 2GB tier when the
data load pattern was MLFindingLabel.query.all() — eagerly pulling the full
~121K-row corpus into memory before any RAM check could intervene. v5.87.44
replaced that with a memory-aware streamed load:

  - psutil.virtual_memory().available is sampled BEFORE rows are loaded
  - row count is capped against the available memory budget
  - rows arrive via .yield_per() and are stream-cleaned into the data list
  - the order_by/limit ensures we keep newest data when capping

These tests do not run the agent (which requires the live app, a real DB,
and the sentence-transformer model). They verify the source code shape,
which is sufficient to catch a regression where someone reintroduces
.query.all() on a known-large table inside the training pipeline.

Run as part of the integrity suite. No Flask context needed.
"""
import os
import unittest


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def _read(rel_path):
    with open(os.path.join(REPO_ROOT, rel_path), 'r', encoding='utf-8') as f:
        return f.read()


def _extract_function_source(file_src, func_name):
    """Pull a function's source out of file_src by line scan.

    Returns the def line through (but not including) the next top-level
    def/class, or end-of-file. Sufficient for static-content assertions;
    not a real Python parser.
    """
    lines = file_src.split('\n')
    out = []
    in_func = False
    for line in lines:
        if not in_func:
            stripped = line.lstrip()
            if stripped.startswith(f'def {func_name}('):
                in_func = True
                out.append(line)
            continue
        if line and not line[0].isspace() and (line.startswith('def ') or line.startswith('class ')):
            break
        out.append(line)
    return '\n'.join(out)


class TestFindingClassifierMemoryGuard(unittest.TestCase):
    """v5.87.44: the Finding Classifier path must not eagerly load the full
    MLFindingLabel corpus before checking RAM. The eager-load pattern was
    the source of the recurring 3am OOM on Render's 2GB tier."""

    def test_no_unbounded_query_all_on_finding_labels(self):
        """The training function must NOT contain MLFindingLabel.query.all().

        That pattern eagerly pulls ~121K rows into memory before any RAM
        guard can act. v5.87.44 replaced it with a memory-aware streamed
        load using yield_per + an upstream row cap.

        Note: this check ignores occurrences inside comments and docstrings
        (the v5.87.44 changelog explanation references the old pattern by
        name). The check is only against code-level usage.
        """
        src = _read('admin_routes.py')
        fn_src = _extract_function_source(src, '_execute_training')
        self.assertTrue(fn_src, '_execute_training not found in admin_routes.py')

        # Walk the function line-by-line, skipping comment-only lines and
        # the contents of triple-quoted docstrings.
        in_docstring = False
        for line in fn_src.split('\n'):
            stripped = line.lstrip()
            # Track docstring state — toggles on lines containing """ or '''
            if '"""' in stripped or "'''" in stripped:
                # Count delimiters; an odd count toggles state
                marker = '"""' if '"""' in stripped else "'''"
                if stripped.count(marker) % 2 == 1:
                    in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            # Strip line-comment portion before searching
            code = line.split('#', 1)[0]
            self.assertNotIn(
                'MLFindingLabel.query.all()',
                code,
                f'MLFindingLabel.query.all() found in code at: {line.strip()!r}\n'
                'This pattern was removed in v5.87.44 to prevent OOM on '
                'Render\'s 2GB tier. Use a streamed load with yield_per() '
                'and an upstream row cap instead.',
            )

    def test_memory_check_runs_before_load(self):
        """psutil.virtual_memory().available must be sampled and used to
        compute a row budget BEFORE any MLFindingLabel rows are pulled.

        The previous bug shape was: load all rows → check RAM → cap. The
        check fired against already-depleted RAM. Fix: check RAM first,
        cap the query itself.
        """
        src = _read('admin_routes.py')
        fn_src = _extract_function_source(src, '_execute_training')

        # Find the index of the first appearance of MLFindingLabel rows
        # actually being loaded, vs. the first appearance of a RAM check.
        ram_check_marker = 'psutil.virtual_memory().available'
        load_markers = [
            'MLFindingLabel.query.yield_per',
            'MLFindingLabel.query.order_by',
            'MLFindingLabel.query.limit',
            'MLFindingLabel.query.filter',
        ]

        ram_idx = fn_src.find(ram_check_marker)
        self.assertGreater(
            ram_idx, 0,
            'No psutil.virtual_memory().available check found in '
            '_execute_training — expected one to size the row budget.',
        )

        # The first MLFindingLabel load that actually pulls rows should
        # come AFTER the first RAM check.
        load_idx = min(
            (fn_src.find(m) for m in load_markers if m in fn_src),
            default=-1,
        )
        self.assertGreater(
            load_idx, 0,
            'No memory-aware MLFindingLabel load found — expected a '
            'yield_per()/order_by()/limit() call after the RAM check.',
        )
        self.assertLess(
            ram_idx, load_idx,
            'RAM check appears AFTER the row load — this is the bug-shape '
            'that caused the v5.87.x OOM. The check must precede the load.',
        )

    def test_uses_yield_per_for_streaming(self):
        """Memory-bounded loading requires .yield_per(). A bare .all() or
        .limit().all() can still spike memory if N is large.
        """
        src = _read('admin_routes.py')
        fn_src = _extract_function_source(src, '_execute_training')
        self.assertIn(
            'yield_per',
            fn_src,
            'Streaming load via yield_per() not present — required to '
            'keep memory bounded during the row-iteration pass.',
        )

    def test_session_expunge_after_load(self):
        """After streaming the rows into the cleaned data list, the ORM
        session should be expunged so SQLAlchemy doesn't hold references
        that prevent garbage collection of the row objects.
        """
        src = _read('admin_routes.py')
        fn_src = _extract_function_source(src, '_execute_training')
        self.assertIn(
            'expunge_all',
            fn_src,
            'db.session.expunge_all() not present after the streamed load. '
            'Without it, ORM identity-map references keep row objects alive '
            'past the cleaning pass, defeating the memory savings of '
            'yield_per().',
        )


class TestKnownLargeTableLoadPatterns(unittest.TestCase):
    """Adjacent guards: other large tables in the same file should be
    audited for the same eager-load anti-pattern. These tests are
    informational rather than blocking — they document where the next
    OOM is most likely to come from."""

    def test_repair_cost_path_documented_for_future(self):
        """MLCostData.query.all() exists in the Repair Cost training path.
        It's tolerable today (the table is much smaller than MLFindingLabel)
        but follows the same anti-pattern. This test documents the known
        risk so a future audit catches it before it manifests as an OOM.
        """
        src = _read('admin_routes.py')
        fn_src = _extract_function_source(src, '_execute_training')
        if 'MLCostData.query.all()' in fn_src:
            # Not yet a bug — the table is small. But flag it for the future.
            # If MLCostData ever crosses ~50K rows, this test should be
            # promoted from skip to fail and the same fix applied.
            self.skipTest(
                'MLCostData.query.all() still uses eager load. Tolerable while '
                'the table is small; promote this test to an assertion if the '
                'table grows past ~50K rows.'
            )


if __name__ == '__main__':
    unittest.main(verbosity=2)
