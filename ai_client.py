"""
ai_client.py — AI client. Anthropic / Claude is the SOLE model provider.

Usage:
    from ai_client import get_ai_response
    text = get_ai_response(prompt, max_tokens=1000)

Environment vars:
    ANTHROPIC_API_KEY — the model provider (Claude)

By design there is no cross-provider fallback: all model behavior stays
confined to one model family. On a transient error (429/500/503/529) the call
retries with a short backoff, then raises RuntimeError.
"""

from model_config import SONNET
import logging
import os
import time

logger = logging.getLogger(__name__)

ANTHROPIC_MODEL = SONNET
RETRY_STATUSES  = {429, 500, 503, 529}
MAX_ATTEMPTS    = 3


def get_anthropic_client():
    """Return a configured Anthropic client, or None when ANTHROPIC_API_KEY is unset.

    v5.89.343: permit_lookup has done `from ai_client import get_anthropic_client`
    since v5.87.82. The function never existed here, so the import fell into the
    module's `except ImportError` stub that returns None, `_llm_batch_lookup` logged
    "no Anthropic client available" and returned [], and every permit row on every
    analysis became the "temporarily unavailable" placeholder. The feature never ran
    once in production. (v5.89.102 fixed the jurisdiction string feeding a call that
    was never made.) The unit tests patch this symbol, which is why they passed.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=key)
    except Exception as e:  # pragma: no cover - SDK missing/misconfigured
        logger.warning("get_anthropic_client: %s", e)
        return None


def get_ai_response(
    prompt: str,
    *,
    max_tokens: int = 1000,
    temperature: float = 0,
    system: str | None = None,
) -> str:
    """
    Send a prompt to Claude (the sole provider) and return the response text.
    Retries on a transient error with backoff, then raises RuntimeError.
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — Claude is the only AI provider."
        )

    messages = [{"role": "user", "content": prompt}]
    last_error = None

    for attempt in range(MAX_ATTEMPTS):
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
            last_error = e
            status = getattr(e, "status_code", None)
            if status in RETRY_STATUSES and attempt < MAX_ATTEMPTS - 1:
                backoff = 2 * (attempt + 1)
                logger.warning(
                    f"Anthropic returned {status} — retry "
                    f"{attempt + 1}/{MAX_ATTEMPTS - 1} in {backoff}s"
                )
                time.sleep(backoff)
                continue
            logger.error(f"Anthropic error ({status}): {e}")
            break

    raise RuntimeError(f"Claude request failed. Last error: {last_error}")
