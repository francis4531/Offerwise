"""
OfferWise AI Cost Tracker v1.0
================================
Aggregates token usage, latency, and cost from the AI audit log.
Provides dashboard data and alerts for cost spikes.

Usage:
    from ai_cost_tracker import AICostTracker
    tracker = AICostTracker()
    summary = tracker.get_daily_summary()
    monthly = tracker.get_monthly_cost()
"""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Anthropic pricing per 1M tokens (as of 2025)
MODEL_PRICING = {
    'claude-sonnet-4-5-20250514': {'input': 3.00, 'output': 15.00},
    'claude-sonnet-4-20250514': {'input': 3.00, 'output': 15.00},
    'claude-haiku-4-5-20251001': {'input': 0.80, 'output': 4.00},
    # Fallback for unknown models
    'default': {'input': 3.00, 'output': 15.00},
}

AUDIT_LOG_PATH = os.environ.get('AI_AUDIT_LOG', 'logs/ai_audit.jsonl')


class AICostTracker:
    """Reads AI audit log and computes cost/usage metrics."""

    def __init__(self, log_path=None):
        self.log_path = log_path or AUDIT_LOG_PATH

    def _read_log_entries(self, since=None):
        """Read audit log entries, optionally filtered by timestamp."""
        entries = []
        if not os.path.exists(self.log_path):
            return entries
        try:
            with open(self.log_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if since:
                            ts = entry.get('timestamp', '')
                            if ts < since.isoformat():
                                continue
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.warning(f"Could not read AI audit log: {e}")
        return entries

    def _estimate_cost(self, entry):
        """Estimate cost for a single AI call from its token counts."""
        model = entry.get('model', 'default')
        pricing = MODEL_PRICING.get(model, MODEL_PRICING['default'])

        input_tokens = entry.get('input_tokens', 0) or 0
        output_tokens = entry.get('output_tokens', 0) or 0

        input_cost = (input_tokens / 1_000_000) * pricing['input']
        output_cost = (output_tokens / 1_000_000) * pricing['output']
        return input_cost + output_cost

    def get_daily_summary(self, date=None):
        """Get usage summary for a specific date (default: today)."""
        if date is None:
            date = datetime.now(timezone.utc).date()

        since = datetime.combine(date, datetime.min.time()).replace(tzinfo=timezone.utc)
        until = since + timedelta(days=1)

        entries = self._read_log_entries(since=since)
        entries = [e for e in entries if e.get('timestamp', '') < until.isoformat()]

        if not entries:
            return {
                'date': str(date),
                'total_calls': 0,
                'total_cost': 0.0,
                'total_input_tokens': 0,
                'total_output_tokens': 0,
                'avg_latency_ms': 0,
                'by_endpoint': {},
                'violations': 0,
            }

        total_cost = 0.0
        total_input = 0
        total_output = 0
        latencies = []
        by_endpoint = defaultdict(lambda: {'calls': 0, 'cost': 0.0, 'tokens': 0})
        violations = 0

        for entry in entries:
            cost = self._estimate_cost(entry)
            total_cost += cost
            total_input += entry.get('input_tokens', 0) or 0
            total_output += entry.get('output_tokens', 0) or 0
            if entry.get('latency_ms'):
                latencies.append(entry['latency_ms'])
            endpoint = entry.get('endpoint', 'unknown')
            by_endpoint[endpoint]['calls'] += 1
            by_endpoint[endpoint]['cost'] += cost
            by_endpoint[endpoint]['tokens'] += (entry.get('input_tokens', 0) or 0) + (entry.get('output_tokens', 0) or 0)
            if entry.get('violations'):
                violations += len(entry['violations'])

        return {
            'date': str(date),
            'total_calls': len(entries),
            'total_cost': round(total_cost, 4),
            'total_input_tokens': total_input,
            'total_output_tokens': total_output,
            'avg_latency_ms': round(sum(latencies) / len(latencies)) if latencies else 0,
            'by_endpoint': dict(by_endpoint),
            'violations': violations,
        }

    def get_monthly_cost(self, year=None, month=None):
        """Get total estimated cost for a month."""
        now = datetime.now(timezone.utc)
        year = year or now.year
        month = month or now.month

        start = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year, month + 1, 1, tzinfo=timezone.utc)

        entries = self._read_log_entries(since=start)
        entries = [e for e in entries if e.get('timestamp', '') < end.isoformat()]

        total_cost = sum(self._estimate_cost(e) for e in entries)
        return {
            'year': year,
            'month': month,
            'total_calls': len(entries),
            'total_cost': round(total_cost, 4),
            'daily_avg': round(total_cost / max(1, (now - start).days), 4) if entries else 0,
            'projected_monthly': round((total_cost / max(1, (now - start).days)) * 30, 2) if entries else 0,
        }

    def check_cost_alerts(self, daily_budget=5.00, monthly_budget=100.00):
        """Check if spending exceeds budget thresholds."""
        alerts = []
        daily = self.get_daily_summary()
        monthly = self.get_monthly_cost()

        if daily['total_cost'] > daily_budget:
            alerts.append({
                'level': 'warning',
                'message': f"Daily AI cost ${daily['total_cost']:.2f} exceeds budget ${daily_budget:.2f}",
                'cost': daily['total_cost'],
                'budget': daily_budget,
            })

        if monthly['total_cost'] > monthly_budget:
            alerts.append({
                'level': 'critical',
                'message': f"Monthly AI cost ${monthly['total_cost']:.2f} exceeds budget ${monthly_budget:.2f}",
                'cost': monthly['total_cost'],
                'budget': monthly_budget,
            })
        elif monthly['projected_monthly'] > monthly_budget:
            alerts.append({
                'level': 'warning',
                'message': f"Projected monthly AI cost ${monthly['projected_monthly']:.2f} will exceed budget ${monthly_budget:.2f}",
                'projected': monthly['projected_monthly'],
                'budget': monthly_budget,
            })

        return alerts

    def is_over_budget(self, daily_budget=None, monthly_budget=None):
        """Circuit breaker — returns True if spending has exceeded hard limits.
        
        Call this before making any AI call. If True, skip non-essential AI calls
        to protect budget for core analysis features.
        
        Hard limits are 150% of soft budgets (configurable via env vars).
        """
        import os
        daily_hard  = float(os.environ.get('AI_DAILY_HARD_LIMIT',  daily_budget  or 7.50))
        monthly_hard = float(os.environ.get('AI_MONTHLY_HARD_LIMIT', monthly_budget or 150.00))

        daily   = self.get_daily_summary()
        monthly = self.get_monthly_cost()

        if daily['total_cost'] >= daily_hard:
            logger.warning(f"🚨 AI circuit breaker OPEN: daily ${daily['total_cost']:.2f} >= ${daily_hard:.2f}")
            return True
        if monthly['total_cost'] >= monthly_hard:
            logger.warning(f"🚨 AI circuit breaker OPEN: monthly ${monthly['total_cost']:.2f} >= ${monthly_hard:.2f}")
            return True
        return False


# Module-level helper for quick inline checks
_default_tracker = None

def is_ai_over_budget():
    """Quick check — import and call from any route that makes optional AI calls.
    
    Usage:
        from ai_cost_tracker import is_ai_over_budget
        if is_ai_over_budget():
            return cached_result  # skip the AI call
    """
    global _default_tracker
    if _default_tracker is None:
        _default_tracker = AICostTracker()
    return _default_tracker.is_over_budget()


# ---------------------------------------------------------------------------
# DB-backed call tracker — call this immediately after every messages.create()
# ---------------------------------------------------------------------------

def is_user_over_ai_budget(user_id, free_tier_limit_usd=0.50) -> bool:
    """Check if a specific user has exceeded their AI cost budget.

    Free-tier users (0 past purchases) are capped at free_tier_limit_usd
    total AI spend. Paid users are uncapped (the global circuit breaker
    protects against runaway costs at the account level).

    Never raises — returns False on any error so it never blocks a request.
    """
    if not user_id:
        return False
    try:
        from app import db as _db, app as _app
        from models import AICallLog, User
        with _app.app_context():
            user = User.query.get(user_id)
            if not user:
                return False
            # Paid users are not individually capped
            if (user.analysis_credits or 0) > 1 or getattr(user, 'stripe_customer_id', None):
                return False
            # Sum this user's total AI spend
            from sqlalchemy import func
            total = _db.session.query(func.sum(AICallLog.cost_usd)).filter(
                AICallLog.user_id == user_id
            ).scalar() or 0.0
            if total >= free_tier_limit_usd:
                logger.warning(
                    f"🚨 Free-tier user #{user_id} AI budget exhausted: "
                    f"${total:.4f} >= ${free_tier_limit_usd:.2f}"
                )
                return True
    except Exception as e:
        logger.debug(f"is_user_over_ai_budget check failed (non-blocking): {e}")
    return False


def track_ai_call(response, endpoint: str, latency_ms: float = 0,
                  user_id=None, context_note: str = None,
                  db=None, app=None):
    """Persist one Anthropic API call to the AICallLog DB table.

    Call this right after every client.messages.create() response:

        t0 = time.time()
        response = client.messages.create(...)
        track_ai_call(response, 'full-analysis', (time.time()-t0)*1000)

    Pass db= and app= when calling from worker threads (avoids circular import):

        from app import app as _app, db as _db
        track_ai_call(response, 'pdf-ocr', latency_ms, db=_db, app=_app)

    Never raises — cost tracking must never break the calling code.
    """
    try:
        import time as _time
        from datetime import datetime as _dt

        model = getattr(response, 'model', 'unknown') or 'unknown'
        usage = getattr(response, 'usage', None)
        input_tokens  = getattr(usage, 'input_tokens',  0) or 0
        output_tokens = getattr(usage, 'output_tokens', 0) or 0

        pricing = MODEL_PRICING.get(model, MODEL_PRICING['default'])
        cost = (input_tokens / 1_000_000) * pricing['input'] + \
               (output_tokens / 1_000_000) * pricing['output']

        # Write to DB — try injected db first, then import fallback
        _db = db
        _app = app
        if _db is None:
            try:
                from app import db as _db2, app as _app2
                _db = _db2
                _app = _app2
            except Exception:
                pass

        if _db is not None:
            try:
                from models import AICallLog
                def _do_write():
                    log = AICallLog(
                        ts=_dt.utcnow(),
                        endpoint=endpoint,
                        model=model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_ms=round(latency_ms, 1),
                        cost_usd=round(cost, 6),
                        user_id=user_id,
                        context_note=(context_note or '')[:200] or None,
                    )
                    _db.session.add(log)
                    _db.session.commit()

                if _app is not None:
                    # Worker thread — need app context
                    with _app.app_context():
                        _do_write()
                else:
                    # Already in request context
                    _do_write()
            except Exception as db_err:
                logger.warning(f"AICallLog DB write failed: {db_err}")

        # Always write to flat audit log as backup (survives DB failures)
        try:
            _write_audit_entry(endpoint, model, input_tokens, output_tokens,
                               latency_ms, cost)
        except Exception:
            pass

    except Exception as e:
        logger.warning(f"track_ai_call failed silently: {e}")


def _write_audit_entry(endpoint, model, input_tokens, output_tokens,
                       latency_ms, cost):
    """Write a minimal entry to the flat JSONL audit log."""
    import json as _json
    from datetime import timezone as _tz, datetime as _dt2
    entry = {
        'ts': _dt2.now(_tz.utc).isoformat(),
        'endpoint': endpoint,
        'model': model,
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'latency_ms': round(latency_ms, 1),
        'cost_usd': round(cost, 6),
    }
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
        with open(AUDIT_LOG_PATH, 'a') as f:
            f.write(_json.dumps(entry) + '\n')
    except Exception:
        pass


def get_cost_summary_from_db(days: int = 1):
    """Read AI cost summary from the DB (preferred over flat log).

    Returns same shape as AICostTracker.get_daily_summary() so the
    dashboard API needs no changes.
    """
    try:
        from datetime import timedelta as _td, datetime as _dt3
        from sqlalchemy import func
        from app import db
        from models import AICallLog

        since = _dt3.utcnow() - _td(days=days if days > 0 else 30)

        rows = db.session.query(
            AICallLog.endpoint,
            func.count(AICallLog.id).label('calls'),
            func.sum(AICallLog.input_tokens).label('input_tokens'),
            func.sum(AICallLog.output_tokens).label('output_tokens'),
            func.sum(AICallLog.cost_usd).label('cost'),
            func.avg(AICallLog.latency_ms).label('avg_latency'),
        ).filter(AICallLog.ts >= since).group_by(AICallLog.endpoint).all()

        total_cost = 0.0
        total_input = 0
        total_output = 0
        total_calls = 0
        latencies = []
        by_endpoint = {}

        for r in rows:
            ep_cost = float(r.cost or 0)
            total_cost += ep_cost
            total_input += int(r.input_tokens or 0)
            total_output += int(r.output_tokens or 0)
            total_calls += int(r.calls or 0)
            if r.avg_latency:
                latencies.append(float(r.avg_latency))
            by_endpoint[r.endpoint] = {
                'calls': int(r.calls or 0),
                'cost': round(ep_cost, 6),
                'input_tokens': int(r.input_tokens or 0),
                'output_tokens': int(r.output_tokens or 0),
                'tokens': int((r.input_tokens or 0) + (r.output_tokens or 0)),
            }

        return {
            'total_calls': total_calls,
            'total_cost': round(total_cost, 6),
            'total_input_tokens': total_input,
            'total_output_tokens': total_output,
            'avg_latency_ms': round(sum(latencies) / len(latencies)) if latencies else 0,
            'by_endpoint': by_endpoint,
            'source': 'db',
        }

    except Exception as e:
        logger.warning(f"get_cost_summary_from_db failed: {e}")
        return None
