"""
ai_json.py — robust structured-output handling for every Claude call that
returns JSON.

WHY THIS EXISTS
---------------
Across the codebase there are ~10 call sites that do, in effect:

    resp = client.messages.create(..., max_tokens=N)
    data = json.loads(resp.content[0].text)

Every one of them is the same latent bug. When the model's JSON is longer than
the token budget, the output is cut off mid-string, json.loads raises
"Unterminated string starting at ...", and (in the worst offender,
optimized_hybrid_cross_reference) the except-branch silently falls back to raw,
un-enhanced output. The user gets degraded analysis and nothing surfaces except
a Sentry line. It fires on exactly the issue-heavy properties where the analysis
matters most.

The root cause is that NO call site reads resp.stop_reason — so truncation is
never even detected. This module fixes the whole class of bug in one place:

  1. Captures stop_reason and treats stop_reason == 'max_tokens' as a definitive
     truncation signal (no guessing from the parse error).
  2. Retries once at a higher token ceiling when truncated — this resolves the
     common case before any salvage is needed.
  3. Extracts JSON from prose / ```json fences.
  4. Best-effort repair of a genuinely truncated payload (close the unterminated
     string, drop a dangling key/comma, balance brackets) so partial-but-valid
     findings survive instead of the whole analysis being lost.
  5. NEVER raises and NEVER silently returns garbage. It returns an AIJsonResult
     the caller MUST inspect (.ok). A failure is observable, not swallowed.
  6. Records every outcome to durable telemetry (AIParseEvent) so the
     parse-failure / truncation RATE is measurable per endpoint — the same
     forward-looking provenance pattern as cost_provenance (v5.89.225).

This is the shared foundation the model-as-pass rebuild stands on: that engine
emits MORE structured output and will truncate sooner, so robust handling is not
optional there.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from model_config import SONNET

logger = logging.getLogger(__name__)

# Mirror ai_client's transient-error policy so this util is robust standalone.
_RETRY_STATUSES = {429, 500, 503, 529}
_TRANSIENT_ATTEMPTS = 3

# Hard cap so a truncation-retry can never request an unbounded budget.
_TOKEN_CEILING_CAP = 16000


@dataclass
class AIJsonResult:
    """Outcome of a JSON-returning model call. Callers MUST check .ok."""
    ok: bool
    data: Any = None                       # parsed JSON (dict/list) when ok
    raw_text: str = ""                     # the model's raw text (last attempt)
    stop_reason: Optional[str] = None      # 'end_turn' | 'max_tokens' | ...
    truncated: bool = False                # stop_reason == 'max_tokens'
    repaired: bool = False                 # parsed only after salvage
    error: Optional[str] = None            # parse error message when not ok
    attempts: int = 0                      # model calls made (incl. retry)
    output_chars: int = 0                  # len of raw text returned


# ---------------------------------------------------------------------------
# JSON extraction + repair
# ---------------------------------------------------------------------------

def extract_json_text(text: str) -> str:
    """Pull the JSON payload out of a model response.

    Handles ```json fences, plain ``` fences, and leading/trailing prose by
    falling back to the first balanced top-level {...} or [...] span. Returns
    the original (stripped) text if no better candidate is found.
    """
    if not text:
        return ""
    t = text.strip()

    if "```json" in t:
        t = t.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in t:
        # take the content of the first fenced block
        parts = t.split("```")
        if len(parts) >= 3:
            t = parts[1].strip()

    # If there's still surrounding prose, isolate the first balanced span.
    start = _first_json_start(t)
    if start > 0:
        span = _balanced_span(t, start)
        if span:
            return span
    elif start == 0:
        # already begins with { or [ — trust it (may be truncated; repair later)
        return t

    return t


def _first_json_start(t: str) -> int:
    for i, ch in enumerate(t):
        if ch in "{[":
            return i
    return -1


def _balanced_span(t: str, start: int) -> Optional[str]:
    """Return the balanced {...}/[...] span beginning at `start`, or None if it
    never closes (truncated)."""
    open_ch = t[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return t[start:i + 1]
    return None  # never balanced -> truncated


def try_parse_json(text: str) -> tuple[bool, Any, bool, Optional[str]]:
    """Parse `text` as JSON, attempting a bounded repair if it was truncated.

    Returns (ok, data, repaired, error).
    """
    candidate = extract_json_text(text)
    if not candidate:
        return False, None, False, "empty response"

    try:
        return True, json.loads(candidate), False, None
    except json.JSONDecodeError as e:
        first_err = str(e)

    repaired = _repair_truncated_json(candidate)
    if repaired is not None and repaired != candidate:
        try:
            return True, json.loads(repaired), True, None
        except json.JSONDecodeError:
            pass
    return False, None, False, first_err


def _repair_truncated_json(text: str) -> Optional[str]:
    """Best-effort salvage of a JSON payload truncated mid-output.

    Strategy: scan tracking string/escape state and a bracket stack. If the
    scan ends inside a string, cut back to just before that unterminated
    string opened. Then strip a trailing dangling key (`"k":`) or comma, and
    append the closers for every still-open bracket in LIFO order. Returns the
    repaired string, or None if nothing useful could be recovered.

    This recovers the elements that fully serialized (e.g. the issue objects
    that completed) and discards the incomplete tail, rather than losing the
    entire analysis.
    """
    if not text:
        return None

    stack: list[str] = []
    in_str = False
    esc = False
    str_start = -1
    cut = len(text)

    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            str_start = i
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()

    # If we ended mid-string, drop the unterminated string entirely.
    if in_str and str_start >= 0:
        cut = str_start
        # Re-derive the bracket stack for the surviving prefix.
        stack = _bracket_stack(text[:cut])

    prefix = text[:cut].rstrip()

    # Drop a dangling key/colon/comma at the tail so the structure is clean.
    prefix = _strip_dangling_tail(prefix)
    if not prefix:
        return None

    closers = "".join("}" if b == "{" else "]" for b in reversed(_bracket_stack(prefix)))
    return prefix + closers


def _bracket_stack(text: str) -> list[str]:
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
    return stack


def _strip_dangling_tail(prefix: str) -> str:
    """Remove a trailing comma, a dangling `"key":` (key with no value yet), or a
    bare key string, iterating until the prefix ends on a complete element.

    A trailing string that is a *value* (preceded by `:`) is preserved; only a
    string at a key position (preceded by `{` or `,`) is stripped.
    """
    p = prefix.rstrip()
    changed = True
    while changed and p:
        changed = False
        if p.endswith(","):
            p = p[:-1].rstrip()
            changed = True
            continue
        if p.endswith(":"):
            # drop the colon, then the key string that preceded it
            p = p[:-1].rstrip()
            if p.endswith('"'):
                q = _last_string_open(p)
                if q >= 0:
                    p = p[:q].rstrip()
            changed = True
            continue
        if p.endswith('"'):
            q = _last_string_open(p)
            if q >= 0:
                before = p[:q].rstrip()
                if before and before[-1] in "{,":  # key position, not a value
                    p = before.rstrip()
                    changed = True
                    continue
        # ends on a complete element ('}' / ']' / scalar / value-string) — stop
    return p


def _last_string_open(p: str) -> int:
    """Index of the opening quote of the trailing string in p (p ends in '\"')."""
    in_str = False
    esc = False
    start = -1
    for i, ch in enumerate(p):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            start = i
    return start


# ---------------------------------------------------------------------------
# The call wrapper
# ---------------------------------------------------------------------------

def _make_call(ai_client, *, model, prompt, max_tokens, temperature, system, track=None):
    """Single model call with light transient-error retry. Returns
    (text, stop_reason). Raises on a non-transient error or exhausted retries.
    `track`, if given, is called as track(raw_response, elapsed_ms) per call so
    cost accounting stays accurate across truncation retries."""
    messages = [{"role": "user", "content": prompt}]
    last_error = None
    for attempt in range(_TRANSIENT_ATTEMPTS):
        try:
            kwargs = dict(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
            )
            if system:
                kwargs["system"] = system
            _t0 = time.time()
            resp = ai_client.messages.create(**kwargs)
            if track:
                try:
                    track(resp, (time.time() - _t0) * 1000)
                except Exception:
                    pass
            text = ""
            try:
                text = resp.content[0].text
            except Exception:
                text = getattr(resp, "text", "") or ""
            stop_reason = getattr(resp, "stop_reason", None)
            return text or "", stop_reason
        except Exception as e:
            last_error = e
            status = getattr(e, "status_code", None)
            if status in _RETRY_STATUSES and attempt < _TRANSIENT_ATTEMPTS - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    raise last_error if last_error else RuntimeError("AI call failed")


def _default_client():
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set — Claude is the only AI provider.")
    return anthropic.Anthropic(api_key=key)


def call_ai_json(
    prompt: str,
    *,
    max_tokens: int,
    temperature: float = 0,
    system: Optional[str] = None,
    model: Optional[str] = None,
    ai_client: Any = None,
    endpoint: Optional[str] = None,
    analysis_id: Optional[int] = None,
    retry_on_truncation: bool = True,
    max_tokens_ceiling: Optional[int] = None,
    record_telemetry: bool = True,
    track: Optional[Callable] = None,
) -> AIJsonResult:
    """Call Claude expecting JSON back, robustly.

    On truncation (stop_reason == 'max_tokens') retries once at a higher
    ceiling. Extracts + parses + (if needed) repairs. NEVER raises on a parse
    problem — returns an AIJsonResult; check .ok. Records an AIParseEvent row
    so the failure/truncation rate is measurable.
    """
    model = model or SONNET
    client = ai_client or None
    ceiling = max_tokens_ceiling or min(max_tokens * 2, _TOKEN_CEILING_CAP)
    ceiling = max(ceiling, max_tokens)

    result = AIJsonResult(ok=False)
    budget = max_tokens

    try:
        if client is None:
            client = _default_client()

        for attempt in range(2):  # at most: initial + one truncation retry
            result.attempts += 1
            text, stop_reason = _make_call(
                client, model=model, prompt=prompt,
                max_tokens=budget, temperature=temperature, system=system,
                track=track,
            )
            result.raw_text = text
            result.stop_reason = stop_reason
            result.output_chars = len(text)
            result.truncated = (stop_reason == "max_tokens")

            if result.truncated and retry_on_truncation and budget < ceiling and attempt == 0:
                logger.warning(
                    f"[ai_json] truncated at {budget} tokens"
                    f"{f' ({endpoint})' if endpoint else ''} — retrying at {ceiling}"
                )
                budget = ceiling
                continue

            ok, data, repaired, err = try_parse_json(text)
            result.ok = ok
            result.data = data
            result.repaired = repaired
            result.error = err
            break

        if not result.ok:
            logger.error(
                f"[ai_json] unparseable response"
                f"{f' ({endpoint})' if endpoint else ''}: "
                f"stop_reason={result.stop_reason} truncated={result.truncated} "
                f"chars={result.output_chars} err={result.error}"
            )
        elif result.repaired:
            logger.warning(
                f"[ai_json] recovered partial JSON via repair"
                f"{f' ({endpoint})' if endpoint else ''} "
                f"(truncated={result.truncated})"
            )
    except Exception as e:
        # Transport/other failure — still a structured, observable outcome.
        result.ok = False
        result.error = f"call_failed: {e}"
        logger.error(f"[ai_json] call failed{f' ({endpoint})' if endpoint else ''}: {e}")

    if record_telemetry:
        record_parse_event(result, endpoint=endpoint, model=model, analysis_id=analysis_id)
    return result


# ---------------------------------------------------------------------------
# Telemetry (write + read) — mirrors cost_provenance: safe-by-construction,
# never raises, forward-looking. Powers an admin view of the parse-failure rate.
# ---------------------------------------------------------------------------

def record_parse_event(result: AIJsonResult, *, endpoint=None, model=None, analysis_id=None) -> int:
    """Persist one AIParseEvent row. Returns 1 on write, 0 otherwise. Never raises.

    Writes only inside an active Flask app context (i.e. real request/analysis
    paths). Outside one — unit tests, offline harnesses — it skips cleanly
    without importing app or touching a session."""
    try:
        from flask import has_app_context
        if not has_app_context():
            return 0
    except Exception:
        return 0
    try:
        from models import db, AIParseEvent
    except Exception as e:
        logger.debug(f"[ai_json] db/model unavailable, skipping telemetry: {e}")
        return 0
    try:
        row = AIParseEvent(
            analysis_id=analysis_id,
            endpoint=(endpoint or "")[:50] or None,
            model=(model or "")[:50] or None,
            stop_reason=(result.stop_reason or "")[:24] or None,
            ok=bool(result.ok),
            truncated=bool(result.truncated),
            repaired=bool(result.repaired),
            output_chars=int(result.output_chars or 0),
            attempts=int(result.attempts or 0),
        )
        db.session.add(row)
        db.session.commit()
        return 1
    except Exception as e:
        logger.warning(f"[ai_json] telemetry write failed (non-fatal): {e}")
        try:
            from models import db
            db.session.rollback()
        except Exception:
            pass
        return 0


def parse_failure_rate_by_endpoint(window_days: int = 30) -> list[dict]:
    """Read side: per-endpoint counts and rates for the admin panel.

    Returns a list of dicts ranked worst-first by failure_rate:
      {endpoint, total, ok, failed, truncated, repaired, failure_rate, truncation_rate}
    Empty list on any error.
    """
    try:
        from models import db, AIParseEvent
        from datetime import datetime, timedelta
    except Exception:
        return []
    try:
        q = db.session.query(AIParseEvent)
        if window_days:
            since = datetime.utcnow() - timedelta(days=window_days)
            q = q.filter(AIParseEvent.created_at >= since)
        rows = q.all()
    except Exception as e:
        logger.warning(f"[ai_json] telemetry read failed: {e}")
        return []

    buckets: dict[str, dict] = {}
    for r in rows:
        ep = r.endpoint or "(unknown)"
        b = buckets.setdefault(ep, dict(
            endpoint=ep, total=0, ok=0, failed=0, truncated=0, repaired=0))
        b["total"] += 1
        b["ok"] += 1 if r.ok else 0
        b["failed"] += 0 if r.ok else 1
        b["truncated"] += 1 if r.truncated else 0
        b["repaired"] += 1 if r.repaired else 0

    out = []
    for b in buckets.values():
        total = b["total"] or 1
        b["failure_rate"] = round(b["failed"] / total, 4)
        b["truncation_rate"] = round(b["truncated"] / total, 4)
        out.append(b)
    out.sort(key=lambda x: (x["failure_rate"], x["truncation_rate"]), reverse=True)
    return out
