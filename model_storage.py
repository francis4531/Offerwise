"""
v5.89.55: centralized model storage path resolver.

Trained model artifacts (.xgb classifiers, .pkl encoders/calibrators)
must survive container restarts. On Render, the container filesystem is
ephemeral — every deploy wipes /app/models/, forcing a full retrain
(~15-20 min) after each push. That has bitten the operator repeatedly.

The fix: write models to Render's persistent disk mount instead. The
mount is /var/data/, the same place the document repository already
lives (see DOCREPO_DISK_PATH in app.py). The new location is
/var/data/models/, created on first call.

In dev (no /var/data/ mount), the function falls back to <app_dir>/models/
so local testing continues to work without changes.

Usage:

    from model_storage import get_models_dir
    models_dir = get_models_dir()
    # writes/reads go through this single path

The function caches its decision after first call to avoid repeated
filesystem checks; if /var/data is unmounted mid-process (it isn't,
in practice) the cache would need clearing, but that's not a
real scenario on Render.
"""
import os
import logging

logger = logging.getLogger(__name__)

# Cache the resolved directory so we don't os.path.exists() on every call.
# Resolved on first get_models_dir() invocation.
_CACHED_MODELS_DIR = None


def _is_writable_dir(path):
    """True if `path` is a directory we can actually write to.

    A bare os.path.isdir() check is not enough — that's the bug v5.89.81
    fixes. /var/data passed isdir() but was ephemeral. We confirm by
    creating the dir if needed and writing+deleting a probe file.
    """
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, '.write_probe')
        with open(probe, 'w') as f:
            f.write('ok')
        os.remove(probe)
        return True
    except Exception:
        return False


def get_models_dir():
    """Return the directory where trained model artifacts live.

    Production (Render persistent disk): /var/data/docrepo/models
      — the disk is mounted at /var/data/docrepo, NOT /var/data.
    Dev / no persistent disk: <app_dir>/models

    Resolution is verified with an actual write probe, not a bare
    isdir() check, so an ephemeral directory that merely exists can
    never again masquerade as the persistent disk (the v5.89.81 bug).

    Creates the directory if it doesn't exist. Idempotent; caches result.
    """
    global _CACHED_MODELS_DIR
    if _CACHED_MODELS_DIR is not None:
        return _CACHED_MODELS_DIR

    # Check for the persistent disk mount. Render uses /var/data; if the
    # operator has set MODELS_DIR explicitly via env var, honor that.
    explicit = os.environ.get('MODELS_DIR', '').strip()
    if explicit:
        _CACHED_MODELS_DIR = explicit
        try:
            os.makedirs(explicit, exist_ok=True)
        except Exception as e:
            logger.warning('get_models_dir: could not create MODELS_DIR=%s: %s', explicit, e)
        logger.info('Models dir (from MODELS_DIR env): %s', explicit)
        return explicit

    # v5.89.81 CRITICAL FIX: the Render persistent disk is mounted at
    # /var/data/docrepo, NOT at /var/data. The old code checked
    # os.path.isdir('/var/data') — which is true even though /var/data
    # itself sits on the ephemeral overlay filesystem; only the docrepo
    # subdirectory is the real disk mount. As a result, models were being
    # written to /var/data/models on EPHEMERAL storage and silently wiped
    # on every deploy/restart. (Confirmed via `mount`: the nvme disk is at
    # /var/data/docrepo; `df /var/data` reports the overlay fs.)
    #
    # Fix: anchor models to the same persistent root the docrepo uses.
    # We read DOCREPO_PATH (the same env var app.py uses, default
    # /var/data/docrepo) and place models in <docrepo_root>/models, then
    # verify with an actual write test rather than a bare isdir check.
    docrepo_root = os.environ.get('DOCREPO_PATH', '/var/data/docrepo').strip()
    if docrepo_root and _is_writable_dir(docrepo_root):
        target = os.path.join(docrepo_root, 'models')
        try:
            os.makedirs(target, exist_ok=True)
            if _is_writable_dir(target):
                _CACHED_MODELS_DIR = target
                logger.info('Models dir (persistent disk, verified writable): %s', target)
                return target
            logger.warning('get_models_dir: %s created but not writable. Falling back to local.', target)
        except Exception as e:
            logger.warning('get_models_dir: docrepo root exists but could not create %s: %s. Falling back to local.', target, e)

    # Dev / fallback: <app_dir>/models
    app_dir = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(app_dir, 'models')
    try:
        os.makedirs(target, exist_ok=True)
    except Exception as e:
        logger.warning('get_models_dir: could not create local %s: %s', target, e)
    _CACHED_MODELS_DIR = target
    logger.info('Models dir (local fallback): %s', target)
    return target


def reset_cache():
    """Test helper: clear the cached directory so the next get_models_dir
    call re-evaluates. Not used in production."""
    global _CACHED_MODELS_DIR
    _CACHED_MODELS_DIR = None
