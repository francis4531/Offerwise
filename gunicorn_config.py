# Gunicorn Configuration for OfferWise
# Optimized for OCR processing which can take 30-90 seconds

import multiprocessing
import os

# Server socket - use PORT from environment (Render sets this)
port = os.environ.get('PORT', '10000')
bind = f"0.0.0.0:{port}"
backlog = 2048

# Worker processes
# HARDCODED to 1 for memory stability on 512 MB plan
# BUT we use threads so the site stays responsive during long analysis requests
workers = 1
worker_class = "gthread"
threads = 4  # Handle 4 concurrent requests per worker
worker_connections = 1000

# Timeouts - CRITICAL for OCR processing
# Respect GUNICORN_TIMEOUT or TIMEOUT env vars, fallback to 300 seconds (5 minutes)
timeout = int(os.environ.get('GUNICORN_TIMEOUT', os.environ.get('TIMEOUT', '300')))
graceful_timeout = 30
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

# Preload app for better performance
preload_app = True

# Worker management
max_requests = 1000  # Restart workers after N requests to prevent memory leaks
max_requests_jitter = 50
