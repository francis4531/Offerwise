"""
OfferWise Structured Logging v1.0
=================================
JSON logging for production (machine-parseable by log aggregators).
Human-readable logging for development.

Usage:
    from structured_logging import setup_logging
    setup_logging()  # Call once at app startup
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON for production log aggregation."""

    def format(self, record):
        log_entry = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'msg': record.getMessage(),
        }

        # Add exception info if present
        if record.exc_info and record.exc_info[0]:
            log_entry['exception'] = self.formatException(record.exc_info)

        # Add extra fields if provided via extra={}
        for key in ('endpoint', 'user_id', 'latency_ms', 'status_code',
                     'ip', 'method', 'path', 'tokens', 'violations'):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        return json.dumps(log_entry, default=str)


def setup_logging():
    """Configure logging based on environment.

    Production (FLASK_ENV != 'development'):
        - JSON format to stdout (for Render / log aggregators)
        - WARNING level by default (reduce noise)
        - INFO for offerwise-specific loggers

    Development:
        - Human-readable format
        - DEBUG level
    """
    is_production = os.environ.get('FLASK_ENV') != 'development'
    root = logging.getLogger()

    # Clear any existing handlers
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    if is_production:
        handler.setFormatter(JSONFormatter())
        root.setLevel(logging.WARNING)
        # Allow INFO for our own modules
        for name in ('app', 'offerwise', 'risk_check_engine', 'ai_output_validator',
                      'pdf_handler', 'pdf_worker', 'analysis_ai_helper'):
            logging.getLogger(name).setLevel(logging.INFO)
    else:
        handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)-8s [%(name)s] %(message)s',
            datefmt='%H:%M:%S'
        ))
        root.setLevel(logging.DEBUG)

    root.addHandler(handler)

    # Silence noisy third-party loggers
    for noisy in ('urllib3', 'werkzeug', 'httpcore', 'httpx', 'anthropic'):
        logging.getLogger(noisy).setLevel(logging.WARNING)
