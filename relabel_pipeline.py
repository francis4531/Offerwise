"""Bulk relabel pipeline — v5.89.47.

Reads the trained category + severity classifiers, runs inference over
the entire ml_finding_labels corpus, and updates labels where the
model's prediction disagrees with the current label AND the model's
confidence exceeds the threshold (default 0.90).

Two modes:
  - dry_run: computes everything, writes nothing to ml_finding_labels.
             Only the MLRelabelRun stats row is updated.
  - commit:  same plus actually mutates corpus rows + auto-triggers
             a training run on completion.

Memory: chunked at 500 rows per pass. Peak RSS should stay under 2GB
(embedder ~250MB + chunk encoding ~5MB + model ~50MB).

Time: ~3 hours for 257K rows on Render's free tier. This MUST run
as a subprocess, not a synchronous HTTP request.

Cancellation: checks MLRelabelRun.cancel_requested at chunk boundaries.
Sets status='cancelled' and exits cleanly when set.

Idempotency: persistent progress in MLRelabelRun.rows_processed lets
a future enhancement resume from a partial run. Current implementation
doesn't resume — failed runs are restarted from row 0.

Usage:
    python relabel_pipeline.py --job-id <uuid> --mode dry_run --threshold 0.90
    python relabel_pipeline.py --job-id <uuid> --mode commit --threshold 0.90
"""
from __future__ import annotations
import os
import sys
import gc
import json
import argparse
import traceback
import subprocess
import uuid
from datetime import datetime
from collections import defaultdict

from model_storage import get_models_dir

# v5.89.41: refuse to proceed if DATABASE_URL is polluted/missing
def _validate_db_url_or_die():
    url = (os.environ.get('DATABASE_URL', '') or '').strip()
    if not url:
        print('[relabel] FATAL: DATABASE_URL is not set.', file=sys.stderr)
        sys.exit(2)
    if url.startswith('sqlite:'):
        print(f'[relabel] FATAL: DATABASE_URL points at SQLite ({url!r}). '
              f'Production relabel requires Postgres.', file=sys.stderr)
        sys.exit(2)
    if not (url.startswith('postgres://') or url.startswith('postgresql://')
            or url.startswith('postgres+') or url.startswith('postgresql+')):
        print(f'[relabel] FATAL: DATABASE_URL has unrecognized scheme.', file=sys.stderr)
        sys.exit(2)


# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────
CHUNK_SIZE = 500       # findings per inference batch
PROGRESS_COMMIT_EVERY = 5  # commit progress to MLRelabelRun every N chunks


# ─────────────────────────────────────────────────────────────────────
# Logging helpers — write to stderr; relabel UI polls MLRelabelRun
# for human-facing progress, no need for a separate log table
# ─────────────────────────────────────────────────────────────────────
def _log(msg):
    """Lightweight stderr logger with timestamp."""
    ts = datetime.utcnow().strftime('%H:%M:%S')
    print(f'[relabel {ts}] {msg}', file=sys.stderr, flush=True)


# ─────────────────────────────────────────────────────────────────────
# Model loading — XGBoost classifiers + calibrators + sentence-transformer
# ─────────────────────────────────────────────────────────────────────
def _load_artifacts(_log, diag_lines=None):
    """Load category/severity classifiers, calibrators, and label encoders.
    Returns dict with keys: cat_model, cat_cal, cat_enc, sev_model, sev_cal,
    sev_enc, embedder. Returns None on any load failure.

    v5.89.48: also appends human-readable diagnostic context to
    `diag_lines` (a list passed in by the caller) so the caller can
    persist it into MLRelabelRun.error_message for UI surfacing.
    """
    import pickle
    import xgboost as xgb

    def _diag(msg):
        """Log AND append to diag_lines if provided."""
        _log(msg)
        if diag_lines is not None:
            diag_lines.append(msg)

    # Where the training pipeline saves artifacts. v5.89.55: now on
    # persistent disk via get_models_dir(). The save calls use cat_enc →
    # 'category_encoder.pkl' (not _label_encoder).
    base = os.path.dirname(os.path.abspath(__file__))
    models_dir = get_models_dir()
    paths = {
        'cat_model':       os.path.join(models_dir, 'finding_category.xgb'),
        'cat_cal':         os.path.join(models_dir, 'category_calibrator.pkl'),
        'cat_enc':         os.path.join(models_dir, 'category_encoder.pkl'),
        'sev_model':       os.path.join(models_dir, 'finding_severity.xgb'),
        'sev_cal':         os.path.join(models_dir, 'severity_calibrator.pkl'),
        'sev_enc':         os.path.join(models_dir, 'severity_encoder.pkl'),
    }

    missing = [k for k, p in paths.items() if not os.path.exists(p)]
    if missing:
        # v5.89.48: surface diagnostics so the operator can act.
        # Without this the "Train first" message gives no signal whether
        # training actually saved files, whether they're at a different
        # path, etc.
        _diag(f'FATAL: missing model artifacts: {missing}')
        _diag(f'Looked in: {models_dir}')

        # What IS in the models dir?
        try:
            if os.path.exists(models_dir):
                actual_files = sorted(os.listdir(models_dir))
                _diag(f'Files actually in {models_dir}: {actual_files if actual_files else "(empty)"}')
            else:
                _diag(f'{models_dir} does NOT exist as a directory')
                # Walk up to see what IS there
                parent_listing = []
                try:
                    parent_listing = sorted(os.listdir(base))[:30]
                except Exception:
                    pass
                _diag(f'Files in parent dir {base} (up to 30): {parent_listing}')
        except Exception as e:
            _diag(f'(Could not introspect filesystem: {e})')

        # Check common alternate locations
        candidates = [
            '/var/data/models',         # Render persistent disk
            '/var/data/docrepo/models', # Render persistent disk with docrepo prefix
            '/tmp/models',
            os.path.join(os.getcwd(), 'models'),
        ]
        for cand in candidates:
            if cand == models_dir:
                continue
            try:
                if os.path.exists(cand):
                    listing = sorted(os.listdir(cand))[:15]
                    _diag(f'NOTE: {cand} exists, contains: {listing}')
            except Exception:
                pass

        _diag(f'Run a training job first if no models/ contents exist anywhere.')
        return None

    out = {}
    try:
        cat_model = xgb.XGBClassifier()
        cat_model.load_model(paths['cat_model'])
        out['cat_model'] = cat_model
        with open(paths['cat_cal'], 'rb') as f:
            out['cat_cal'] = pickle.load(f)
        with open(paths['cat_enc'], 'rb') as f:
            out['cat_enc'] = pickle.load(f)

        sev_model = xgb.XGBClassifier()
        sev_model.load_model(paths['sev_model'])
        out['sev_model'] = sev_model
        with open(paths['sev_cal'], 'rb') as f:
            out['sev_cal'] = pickle.load(f)
        with open(paths['sev_enc'], 'rb') as f:
            out['sev_enc'] = pickle.load(f)
    except Exception as e:
        _diag(f'FATAL: failed to load XGBoost/calibrator/encoder: {type(e).__name__}: {e}')
        return None

    # Sentence-transformer — same model the training pipeline uses
    try:
        from sentence_transformers import SentenceTransformer
        # MiniLM-L6-v2 is what ml_training_pipeline.py uses (~80MB, 384 dim)
        out['embedder'] = SentenceTransformer('all-MiniLM-L6-v2')
    except Exception as e:
        _diag(f'FATAL: failed to load sentence-transformer: {type(e).__name__}: {e}')
        return None

    _log('All artifacts loaded successfully')
    return out


# ─────────────────────────────────────────────────────────────────────
# Inference helpers
# ─────────────────────────────────────────────────────────────────────
def _predict_chunk(artifacts, texts):
    """Given a list of finding texts, return per-row (cat_pred, cat_conf,
    sev_pred, sev_conf) where preds are decoded strings and confs are
    calibrated max probabilities."""
    import numpy as np

    embeddings = artifacts['embedder'].encode(
        texts,
        batch_size=32,
        show_progress_bar=False,
        convert_to_numpy=True,
    )

    # Category
    cat_probas = artifacts['cat_cal'].predict_proba(embeddings)
    cat_idx = cat_probas.argmax(axis=1)
    cat_conf = cat_probas.max(axis=1)
    cat_pred_strs = artifacts['cat_enc'].inverse_transform(cat_idx)

    # Severity
    sev_probas = artifacts['sev_cal'].predict_proba(embeddings)
    sev_idx = sev_probas.argmax(axis=1)
    sev_conf = sev_probas.max(axis=1)
    sev_pred_strs = artifacts['sev_enc'].inverse_transform(sev_idx)

    return list(zip(cat_pred_strs, cat_conf, sev_pred_strs, sev_conf))


# ─────────────────────────────────────────────────────────────────────
# Main relabel loop
# ─────────────────────────────────────────────────────────────────────
def run_relabel(job_id, mode, threshold):
    """Main loop. Updates the MLRelabelRun row associated with job_id."""
    if mode not in ('dry_run', 'commit'):
        _log(f'FATAL: invalid mode {mode!r}')
        sys.exit(2)
    if not (0.0 < threshold <= 1.0):
        _log(f'FATAL: invalid threshold {threshold!r}')
        sys.exit(2)

    # Set up Flask app context
    app_dir = os.path.dirname(os.path.abspath(__file__))
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    os.environ['OFFERWISE_TRAINING_SUBPROCESS'] = '1'

    from app import app
    from models import db, MLFindingLabel, MLRelabelRun

    with app.app_context():
        # Locate the run row
        run = MLRelabelRun.query.filter_by(job_id=job_id).first()
        if not run:
            _log(f'FATAL: MLRelabelRun row not found for job_id={job_id}')
            sys.exit(2)

        run.status = 'running'
        run.started_at = datetime.utcnow()
        # v5.89.52: also set last_progress_at so zombie detection has a
        # baseline before the first progress commit (which may not fire
        # for several minutes during model load + embedding warmup).
        run.last_progress_at = datetime.utcnow()
        db.session.commit()
        _log(f'Run {job_id} started in {mode} mode, threshold={threshold}')

        # Load artifacts. v5.89.48: collect diagnostic context so the UI
        # can show the actual missing-files / paths-tried info instead of
        # a generic "Train first" message.
        diag_lines = []
        artifacts = _load_artifacts(_log, diag_lines=diag_lines)
        if artifacts is None:
            run.status = 'failed'
            run.completed_at = datetime.utcnow()

            # v5.89.48: belt-and-suspenders — even if diag_lines is empty
            # for some reason (unexpected execution path), emit a minimum
            # diagnostic so the operator gets actionable info. The
            # [v5.89.48] marker lets us verify on next failure that the
            # deployed code is actually this version.
            if not diag_lines:
                base_dir = os.path.dirname(os.path.abspath(__file__))
                # v5.89.55: report the actual lookup path (persistent disk)
                models_dir = get_models_dir()
                diag_lines = [
                    'Failed to load model artifacts. Train first. [v5.89.48 minimum fallback / v5.89.55 path]',
                    f'cwd={os.getcwd()}',
                    f'__file__={os.path.abspath(__file__)}',
                    f'computed base={base_dir}',
                    f'expected models dir={models_dir}',
                    f'models dir exists={os.path.isdir(models_dir)}',
                ]
                if os.path.isdir(models_dir):
                    try:
                        diag_lines.append(f'models dir contents={sorted(os.listdir(models_dir))[:30]}')
                    except Exception as e:
                        diag_lines.append(f'(could not list: {e})')

            # Cap message size so DB column stays sane. Most diagnostics
            # are well under 2KB.
            diag_text = '\n'.join(diag_lines)
            if len(diag_text) > 4000:
                diag_text = diag_text[:4000] + '\n... (truncated)'
            run.error_message = diag_text
            db.session.commit()
            sys.exit(1)

        # v5.89.49: fetch all IDs upfront. This (a) gives rows_total for
        # free, (b) gives the main loop a deterministic ID list to iterate
        # without needing a server-side cursor that would die at the first
        # progress commit.
        _log('Fetching corpus IDs upfront (one-time cost)...')
        all_ids = [r[0] for r in db.session.query(MLFindingLabel.id)
                                   .filter(MLFindingLabel.excluded_from_training == False)
                                   .all()]
        total = len(all_ids)
        run.rows_total = total
        db.session.commit()
        _log(f'Corpus: {total:,} rows to process')

        # Tracking aggregates
        cat_change_dist = defaultdict(int)   # (from, to) -> count
        sev_change_dist = defaultdict(int)
        rows_changed_category = 0
        rows_changed_severity = 0
        rows_low_confidence = 0
        rows_agreement = 0
        rows_failed = 0
        rows_processed = 0
        chunks_since_commit = 0
        now_utc = datetime.utcnow()

        # v5.89.49: design note — we chunk by ID range rather than streaming
        # via .yield_per(). The original v5.89.47 implementation used
        # .yield_per() which creates a Postgres named server-side cursor.
        # Named cursors only stay valid within their original transaction —
        # calling db.session.commit() (which we do every PROGRESS_COMMIT_EVERY
        # chunks for progress reporting) invalidated the cursor. After 2,500
        # rows the next fetch crashed with "named cursor isn't valid anymore".
        #
        # all_ids was fetched up at the rows_total computation above. Now we
        # chunk-fetch the actual row data using .filter(id.in_(chunk_ids)) —
        # each chunk fetch is a fresh query, no cursor state to maintain.

        # Accumulate chunks
        chunk_buffer = []  # list of dicts: { id, text, category, severity, has_audit_orig_cat, has_audit_orig_sev }

        def _flush_chunk():
            nonlocal rows_changed_category, rows_changed_severity
            nonlocal rows_low_confidence, rows_agreement, rows_failed, rows_processed, chunks_since_commit

            if not chunk_buffer:
                return

            # Check for cancellation between chunks. v5.89.49: re-query
            # rather than session.refresh() because after intermittent
            # commits we don't want to depend on session attachment state.
            cancel_check = (db.session.query(MLRelabelRun.cancel_requested)
                            .filter_by(job_id=run.job_id).first())
            if cancel_check and cancel_check[0]:
                _log('Cancellation requested — stopping.')
                run.cancel_requested = True
                run.status = 'cancelled'
                run.completed_at = datetime.utcnow()
                db.session.commit()
                sys.exit(0)

            texts = [r['text'] for r in chunk_buffer]
            try:
                predictions = _predict_chunk(artifacts, texts)
            except Exception as e:
                _log(f'Chunk prediction failed: {type(e).__name__}: {e}')
                rows_failed += len(chunk_buffer)
                chunk_buffer.clear()
                return

            # Per-row processing
            for row_info, (cat_pred, cat_conf, sev_pred, sev_conf) in zip(chunk_buffer, predictions):
                rows_processed += 1
                orig_cat = (row_info['category'] or '').strip().lower()
                orig_sev = (row_info['severity'] or '').strip().lower()
                cat_pred_str = str(cat_pred).strip().lower()
                sev_pred_str = str(sev_pred).strip().lower()

                cat_disagrees = (orig_cat and cat_pred_str and orig_cat != cat_pred_str)
                sev_disagrees = (orig_sev and sev_pred_str and orig_sev != sev_pred_str)

                if not cat_disagrees and not sev_disagrees:
                    rows_agreement += 1
                    continue

                # Disagreement: check confidence
                will_change_cat = cat_disagrees and float(cat_conf) >= threshold
                will_change_sev = sev_disagrees and float(sev_conf) >= threshold

                if not will_change_cat and not will_change_sev:
                    rows_low_confidence += 1
                    continue

                # Track aggregate distribution
                if will_change_cat:
                    cat_change_dist[(orig_cat, cat_pred_str)] += 1
                    rows_changed_category += 1
                if will_change_sev:
                    sev_change_dist[(orig_sev, sev_pred_str)] += 1
                    rows_changed_severity += 1

                # In dry_run mode, we DON'T mutate the corpus
                if mode == 'dry_run':
                    continue

                # In commit mode: update the actual row.
                # First-writer-wins on audit_original_* — preserves the true
                # original even if a prior bulk-relabel or operator audit
                # already touched this row.
                try:
                    update_kwargs = {
                        'last_relabel_at': now_utc,
                        'last_relabel_confidence': float(max(cat_conf if will_change_cat else 0,
                                                              sev_conf if will_change_sev else 0)),
                    }
                    if will_change_cat:
                        # Preserve original only if not already preserved
                        if not row_info['audit_original_category']:
                            update_kwargs['audit_original_category'] = orig_cat
                        update_kwargs['category'] = cat_pred_str
                        # If category_v2 is set, update that too — v2 is the
                        # current source-of-truth for the training pipeline
                        if row_info['category_v2']:
                            update_kwargs['category_v2'] = cat_pred_str
                    if will_change_sev:
                        if not row_info['audit_original_severity']:
                            update_kwargs['audit_original_severity'] = orig_sev
                        update_kwargs['severity'] = sev_pred_str
                        if row_info['severity_v2']:
                            update_kwargs['severity_v2'] = sev_pred_str

                    # Targeted UPDATE — avoids loading the full ORM object
                    db.session.query(MLFindingLabel).filter_by(id=row_info['id']).update(update_kwargs)
                except Exception as e:
                    _log(f'Update failed for row {row_info["id"]}: {type(e).__name__}: {e}')
                    rows_failed += 1

            chunk_buffer.clear()
            chunks_since_commit += 1

            # Commit progress periodically
            if chunks_since_commit >= PROGRESS_COMMIT_EVERY:
                run.rows_processed = rows_processed
                run.rows_changed_category = rows_changed_category
                run.rows_changed_severity = rows_changed_severity
                run.rows_low_confidence = rows_low_confidence
                run.rows_agreement = rows_agreement
                run.rows_failed = rows_failed
                # v5.89.52: heartbeat for zombie detection. Status endpoint
                # uses this to detect a stuck subprocess.
                run.last_progress_at = datetime.utcnow()
                try:
                    db.session.commit()
                except Exception as e:
                    _log(f'Progress commit failed: {type(e).__name__}: {e}')
                    db.session.rollback()
                chunks_since_commit = 0
                pct = (rows_processed / max(total, 1)) * 100
                _log(f'Progress: {rows_processed:,}/{total:,} ({pct:.1f}%) '
                     f'cat_changed={rows_changed_category} sev_changed={rows_changed_severity} '
                     f'low_conf={rows_low_confidence} agreement={rows_agreement}')
                # Force memory release between commits
                gc.collect()

        # Main loop: chunk IDs, fetch rows per chunk, process, commit.
        # This pattern (vs yield_per) survives the periodic progress commits
        # because each chunk fetch is its own query.
        for chunk_start in range(0, len(all_ids), CHUNK_SIZE):
            chunk_ids = all_ids[chunk_start:chunk_start + CHUNK_SIZE]

            # Fetch row data for this chunk
            rows = (db.session.query(
                        MLFindingLabel.id,
                        MLFindingLabel.finding_text,
                        MLFindingLabel.category,
                        MLFindingLabel.severity,
                        MLFindingLabel.category_v2,
                        MLFindingLabel.severity_v2,
                        MLFindingLabel.audit_original_category,
                        MLFindingLabel.audit_original_severity,
                    )
                    .filter(MLFindingLabel.id.in_(chunk_ids))
                    .all())

            for row_tuple in rows:
                (fid, ftext, fcat, fsev, fcat_v2, fsev_v2,
                 audit_orig_cat, audit_orig_sev) = row_tuple

                if not ftext or len(ftext.strip()) < 8:
                    rows_failed += 1
                    rows_processed += 1
                    continue

                # Use v2 labels as source-of-truth (same as training does)
                cat_source = (fcat_v2 or fcat or '').strip().lower()
                sev_source = (fsev_v2 or fsev or '').strip().lower()

                chunk_buffer.append({
                    'id': fid,
                    'text': ftext[:5000],  # cap for embedder
                    'category': cat_source,
                    'severity': sev_source,
                    'category_v2': fcat_v2,
                    'severity_v2': fsev_v2,
                    'audit_original_category': audit_orig_cat,
                    'audit_original_severity': audit_orig_sev,
                })

            # Process & commit at end of each chunk
            _flush_chunk()

        # Tail flush (no-op if buffer already empty, defensive)
        _flush_chunk()

        # Final stats persist
        run.rows_processed = rows_processed
        run.rows_changed_category = rows_changed_category
        run.rows_changed_severity = rows_changed_severity
        run.rows_low_confidence = rows_low_confidence
        run.rows_agreement = rows_agreement
        run.rows_failed = rows_failed

        # Stats JSON — top change distributions
        stats = {
            'category_change_dist': {f'{k[0]}->{k[1]}': v for k, v in
                                      sorted(cat_change_dist.items(), key=lambda x: -x[1])[:50]},
            'severity_change_dist': {f'{k[0]}->{k[1]}': v for k, v in
                                      sorted(sev_change_dist.items(), key=lambda x: -x[1])[:20]},
            'threshold': threshold,
            'mode': mode,
        }
        run.stats_json = json.dumps(stats)
        run.status = 'completed'
        run.completed_at = datetime.utcnow()

        try:
            db.session.commit()
        except Exception as e:
            _log(f'Final commit failed: {type(e).__name__}: {e}')
            db.session.rollback()
            sys.exit(1)

        _log(f'✅ Run {job_id} completed: {rows_processed:,} processed, '
             f'{rows_changed_category} cat changes, {rows_changed_severity} sev changes, '
             f'{rows_low_confidence} low-confidence skipped, {rows_agreement} agreements')

        # Auto-trigger retrain on commit-mode success.
        # Concurrency guard: check the job-state directory used by
        # admin_routes.admin_ml_train. If a training job state file
        # exists and is recent (≤10 min old, matching admin_routes'
        # liveness threshold), DON'T spawn — two concurrent trainings
        # would corrupt models/ and MLTrainingRun rows.
        if mode == 'commit' and rows_changed_category + rows_changed_severity > 0:
            try:
                import glob, time as _time
                state_dir = '/var/data/docrepo/.ml_jobs'
                concurrent_running = False
                if os.path.exists(state_dir):
                    for f in glob.glob(os.path.join(state_dir, '*.json')):
                        fname = os.path.basename(f)
                        if fname.startswith('_'):  # _crawl, _agent_status etc.
                            continue
                        try:
                            age = _time.time() - os.path.getmtime(f)
                            if age < 600:  # less than 10 min old
                                concurrent_running = True
                                _log(f'Concurrent training detected ({fname}, age {age:.0f}s) — skipping auto-trigger')
                                break
                        except Exception:
                            pass

                if concurrent_running:
                    _log('💡 Retrain not auto-triggered (another training in flight). '
                         'Trigger manually after it completes.')
                else:
                    training_job_id = str(uuid.uuid4())
                    python = sys.executable
                    training_script = os.path.join(app_dir, 'run_training.py')
                    if not os.path.exists(training_script):
                        _log(f'run_training.py not found at {training_script} — '
                             f'operator should trigger training manually')
                    else:
                        # Seed the job-state file the orchestrator expects.
                        # admin_routes.admin_ml_train normally does this BEFORE
                        # spawning the subprocess; we mimic it minimally here so
                        # the orchestrator + status endpoint behave normally.
                        try:
                            os.makedirs(state_dir, exist_ok=True)
                            state_path = os.path.join(state_dir, f'{training_job_id}.json')
                            seed_state = {
                                'job_id': training_job_id,
                                'status': 'running',
                                'started_at': _time.time(),
                                'triggered_by': f'relabel:{job_id}',
                                'log': [],
                                'results': {},
                            }
                            with open(state_path, 'w') as f:
                                json.dump(seed_state, f)
                        except Exception as seed_err:
                            _log(f'Could not seed job state file ({seed_err}); '
                                 f'orchestrator may not be visible to status endpoint, '
                                 f'but training will still run')

                        child_env = os.environ.copy()
                        child_env['OFFERWISE_TRAINING_SUBPROCESS'] = '1'
                        subprocess.Popen(
                            [python, training_script, '--job-id', training_job_id],
                            env=child_env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            cwd=app_dir,
                        )
                        run.triggered_training_job_id = training_job_id
                        db.session.commit()
                        _log(f'✅ Auto-triggered retrain (training_job_id={training_job_id})')
            except Exception as e:
                _log(f'Auto-retrain trigger failed (non-fatal): {type(e).__name__}: {e}')
                # Don't fail the relabel run — operator can trigger manually


# ─────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--mode', required=True, choices=['dry_run', 'commit'])
    parser.add_argument('--threshold', type=float, default=0.90)
    args = parser.parse_args()

    _validate_db_url_or_die()

    try:
        run_relabel(args.job_id, args.mode, args.threshold)
        sys.exit(0)
    except SystemExit:
        raise  # don't swallow argparse / explicit exits
    except Exception as e:
        print(f'[relabel] FATAL: {type(e).__name__}: {e}', file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        # Try to mark the run as failed
        try:
            app_dir = os.path.dirname(os.path.abspath(__file__))
            if app_dir not in sys.path:
                sys.path.insert(0, app_dir)
            from app import app
            from models import db, MLRelabelRun
            with app.app_context():
                run = MLRelabelRun.query.filter_by(job_id=args.job_id).first()
                if run:
                    run.status = 'failed'
                    run.completed_at = datetime.utcnow()
                    run.error_message = f'{type(e).__name__}: {str(e)[:1000]}'
                    db.session.commit()
        except Exception:
            pass  # best effort
        sys.exit(1)


if __name__ == '__main__':
    main()
