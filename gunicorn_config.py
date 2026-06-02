# Gunicorn Configuration for OfferWise
# Optimized for OCR processing which can take 30-90 seconds

import multiprocessing
import os

# Server socket - use PORT from environment (Render sets this)
port = os.environ.get('PORT', '10000')
bind = f"0.0.0.0:{port}"
backlog = 2048

# Worker processes
# Standard plan (2GB RAM, 1 CPU): 1 worker × 4 threads = 4 concurrent requests.
# v5.86.51: Dropped from 2 → 1 worker. Each worker loads its own sentence-transformer
# embedder (~250MB) at startup (via init_ml_inference), so 2 workers consumed ~800MB
# steady-state, leaving insufficient room for the training subprocess to spike to
# ~800MB without tripping the cgroup OOM killer. With 1 worker we free ~400MB for
# training headroom. At current traffic levels (solo founder, low concurrency),
# 4 threads on 1 worker is more than sufficient. Revisit after product-market fit.
workers = 1
worker_class = "gthread"
threads = 4
worker_connections = 1000

# Timeouts - CRITICAL for OCR processing
# Respect GUNICORN_TIMEOUT or TIMEOUT env vars, fallback to 300 seconds (5 minutes)
timeout = int(os.environ.get('GUNICORN_TIMEOUT', os.environ.get('TIMEOUT', '300')))
# graceful_timeout raised from 30 → 120: if a worker recycle happens mid-training,
# this gives the training thread 2 minutes to finish its current chunk + save
# state before being SIGKILLed. Most training phases (encode chunk, xgb fit
# iteration) complete well within this window.
graceful_timeout = 120
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "offerwise"

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# SSL (if needed)
# keyfile = None
# certfile = None

# preload_app=False: port binds immediately, each worker initializes independently.
# This prevents Render's port-scan timeout. Workers run the migration init block
# themselves, but the persistent-disk stamp guard makes it fast (sub-100ms on
# deploys after the first). No DDL lock contention, no worker crashes.
preload_app = False

# Worker management
# max_requests raised from 1000 → 20000: the old value caused workers to recycle
# every ~32-35 hours under normal uptime-bot + real traffic (1440 bot pings/day +
# frontend polls). Training runs of 3-20 min that spanned a recycle got killed
# silently because gunicorn's graceful shutdown doesn't wait for threads spawned
# outside a request. At 20000 requests the recycle happens weekly, so the chance
# of a training run spanning one is negligible. Python's GC + explicit del calls
# in training handle memory leaks fine without needing aggressive recycling.
max_requests = 20000
max_requests_jitter = 500

