#!/usr/bin/env python3
"""ML training orchestrator.

Runs the 4-stage training pipeline as sequential subprocesses. Each subprocess
starts fresh, loads what it needs, trains one model, writes results, and exits
— so peak memory from one stage cannot compound with another. This is the
bulletproof architecture for 2GB-RAM environments.

Stages:
    1. finding_classifier     — ~800MB peak, ~2-3 min
    2. contradiction_detector — ~500MB peak, ~30s-1min
    3. cost_predictor         — ~800MB peak, ~1-2 min
    4. post_training          — ~300MB (reload + smoke tests + dashboard)

Communication with the gunicorn web worker is through the job state file on
disk — the web worker's heartbeat thread keeps the mtime fresh for liveness
detection. Each stage subprocess appends to the shared log and writes its own
results key.

Usage:
    python run_training.py --job-id <uuid12>

Called by admin_routes.admin_ml_train via subprocess.Popen. The web worker
writes the initial state file before spawning this orchestrator.
"""

import argparse
import os
import subprocess
import sys
import time


STAGES = [
    # (stage_name, display_label, required)
    # required=True: if this stage fails, abort the run.
    # required=False: log the failure, continue to the next stage.
    ('finding_classifier',     'Finding Classifier',     False),
    ('contradiction_detector', 'Contradiction Detector', False),
    ('cost_predictor',         'Cost Predictor',         False),
    ('post_training',          'Post-training',          False),
]


def _log(job_id, msg, level, app_dir, t_start):
    """Append a log line to the job state file from the orchestrator."""
    try:
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        from admin_routes import _load_job_state, _save_job_state
        state = _load_job_state(job_id) or {}
        log = state.setdefault('log', [])
        elapsed = time.time() - (t_start or state.get('started_at', time.time()))
        log.append({'t': round(elapsed, 1), 'msg': msg, 'level': level})
        _save_job_state(job_id, state)
    except Exception as e:
        print(f'[orchestrator] log failed: {e}', file=sys.stderr)


def _mark_failed(job_id, error_msg, app_dir, t_start):
    """Mark the whole job as failed."""
    try:
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        from admin_routes import _load_job_state, _save_job_state
        state = _load_job_state(job_id) or {}
        state['status'] = 'failed'
        state['error'] = error_msg[:2000]
        state['elapsed_total'] = time.time() - (t_start or state.get('started_at', time.time()))
        _save_job_state(job_id, state)
    except Exception as e:
        print(f'[orchestrator] _mark_failed failed: {e}', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job-id', required=True)
    args = parser.parse_args()
    job_id = args.job_id

    app_dir = os.path.dirname(os.path.abspath(__file__))
    pipeline_script = os.path.join(app_dir, 'ml_training_pipeline.py')

    t_start = time.time()
    _log(job_id, '🚀 Training orchestrator started — stages run in isolated subprocesses', 'header', app_dir, t_start)

    # Each stage inherits OFFERWISE_TRAINING_SUBPROCESS=1 so app.py skips APScheduler
    child_env = os.environ.copy()
    child_env['OFFERWISE_TRAINING_SUBPROCESS'] = '1'

    overall_ok = True
    for stage, label, required in STAGES:
        stage_start = time.time()
        _log(job_id, f'▶ Stage: {label} (spawning subprocess)', 'info', app_dir, t_start)

        try:
            # No timeout — stage subprocesses write heartbeats via _log, and the
            # web worker's heartbeat thread keeps the state file mtime fresh.
            proc = subprocess.Popen(
                [sys.executable, pipeline_script, stage, '--job-id', job_id],
                cwd=app_dir,
                env=child_env,
            )
            exit_code = proc.wait()
        except Exception as spawn_err:
            msg = f'❌ {label}: failed to spawn subprocess — {spawn_err}'
            _log(job_id, msg, 'error', app_dir, t_start)
            if required:
                _mark_failed(job_id, msg, app_dir, t_start)
                sys.exit(1)
            overall_ok = False
            continue

        stage_elapsed = time.time() - stage_start

        if exit_code == 0:
            _log(job_id, f'✅ {label} complete in {stage_elapsed:.1f}s', 'success', app_dir, t_start)
        else:
            # Linux returns 137 for SIGKILL (= 128 + 9). Python subprocess.wait()
            # returns -signum for signal death on POSIX. Check both.
            oom_hint = ''
            if exit_code in (-9, 137, 9):
                oom_hint = ' (likely OOM kill — SIGKILL)'
            elif exit_code < 0:
                oom_hint = f' (killed by signal {-exit_code})'
            msg = f'❌ {label} exited with code {exit_code} after {stage_elapsed:.1f}s{oom_hint}'
            _log(job_id, msg, 'error', app_dir, t_start)
            overall_ok = False
            # Continue — partial training is better than nothing.
            # The model's results dict already shows FAILED (set by _update_results
            # in the stage subprocess, or will appear as missing if it died pre-write).

    # Finalize job state. post_training normally writes status='complete', but if
    # it died we need to mark the job done ourselves so the UI doesn't hang.
    try:
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        from admin_routes import _load_job_state, _save_job_state
        state = _load_job_state(job_id) or {}
        if state.get('status') == 'running':
            state['status'] = 'complete' if overall_ok else 'failed'
            if not overall_ok and not state.get('error'):
                state['error'] = 'One or more training stages failed. See the log for details.'
            state['elapsed_total'] = time.time() - t_start
            # Frontend reads _log and _elapsed from results (not the top-level state)
            results = state.get('results') or {}
            results['_log'] = state.get('log', [])
            results['_elapsed'] = f"{state['elapsed_total']:.1f}s"
            state['results'] = results
            _save_job_state(job_id, state)
    except Exception as e:
        print(f'[orchestrator] final state write failed: {e}', file=sys.stderr)

    sys.exit(0 if overall_ok else 1)


if __name__ == '__main__':
    main()
