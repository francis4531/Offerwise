"""
ai_client.py — Resilient AI client with automatic OpenAI fallback.

Usage:
    from ai_client import get_ai_response

    text = get_ai_response(prompt, max_tokens=1000)
    # Returns response text, or raises RuntimeError if both providers fail.

Environment vars:
    ANTHROPIC_API_KEY  — primary provider (Claude Sonnet)
    OPENAI_API_KEY     — fallback provider (GPT-4o), optional but recommended

The function tries Anthropic first. On 429/500/503/529 it retries once after
2 seconds, then falls back to OpenAI. If OPENAI_API_KEY is not set the fallback
is skipped and the Anthropic error is re-raised.
"""

import logging
import os
import time

logger = logging.getLogger(__name__)

ANTHROPIC_MODEL = "claude-sonnet-4-6"
OPENAI_MODEL    = "gpt-4o"
RETRY_STATUSES  = {429, 500, 503, 529}


def get_ai_response(
    prompt: str,
    *,
    max_tokens: int = 1000,
    temperature: float = 0,
    system: str | None = None,
) -> str:
    """
    Send a prompt to Claude (primary) or GPT-4o (fallback).
    Returns the response text string.
    Raises RuntimeError if both providers fail.
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_key    = os.environ.get("OPENAI_API_KEY", "")

    messages = [{"role": "user", "content": prompt}]
    last_error = None

    # ── Anthropic (primary) ───────────────────────────────────────
    if anthropic_key:
        for attempt in range(2):  # one retry before fallback
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=anthropic_key)
                kwargs = dict(
                    model=ANTHROPIC_MODEL,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=messages,
                )
                if system:
                    kwargs["system"] = system
                resp = client.messages.create(**kwargs)
                return resp.content[0].text
            except Exception as e:
                status = getattr(e, "status_code", None)
                if status in RETRY_STATUSES:
                    last_error = e
                    if attempt == 0:
                        logger.warning(
                            f"Anthropic returned {status} — retrying in 2s"
                        )
                        time.sleep(2)
                        continue
                    logger.warning(
                        f"Anthropic still unavailable ({status}) after retry "
                        f"— falling back to OpenAI"
                    )
                    break
                # Non-retryable error — propagate unless we have OpenAI
                last_error = e
                logger.error(f"Anthropic non-retryable error: {e}")
                break

    # ── OpenAI GPT-4o (fallback) ──────────────────────────────────
    if openai_key:
        try:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            oai_messages = []
            if system:
                oai_messages.append({"role": "system", "content": system})
            oai_messages.extend(messages)
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=oai_messages,
            )
            text = resp.choices[0].message.content or ""
            logger.info("✅ OpenAI GPT-4o fallback succeeded")
            return text
        except Exception as e:
            logger.error(f"OpenAI fallback failed: {e}")
            last_error = e

    raise RuntimeError(
        f"All AI providers failed. Last error: {last_error}"
    )
