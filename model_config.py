"""model_config.py — single source of truth for Claude model ids.

Every call site imports the tier it needs (SONNET / HAIKU / OPUS) from here
instead of hardcoding a dated model string. When the provider retires a model,
update the one line here and the whole app moves with it.

This module deliberately has ZERO imports so any module can import it without
circular-import risk.
"""

# Current production model ids — update HERE on a provider retirement.
SONNET = "claude-sonnet-4-6"
HAIKU = "claude-haiku-4-5-20251001"
OPUS = "claude-opus-4-8"

# General-purpose default (matches ai_client's historical ANTHROPIC_MODEL).
DEFAULT = SONNET
