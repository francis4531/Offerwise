"""
avm_gate.py — single source of truth for AVM corroboration.

A single-source AVM (RentCast) is only allowed to drive value/discount narrative
if it agrees with an independent signal. Two entry points, one set of thresholds,
so the research-agent path (which feeds the AI narrative) and the market-
intelligence path (which feeds the structured offer) can never drift apart:

  avm_is_comp_outlier(avm, comp_median, comp_count)
      Source-side, comps-only. Conservative: fires ONLY when there is strong comp
      evidence AGAINST the AVM (>=3 sold comps and divergence past tolerance).
      With thin comps it defers (returns not-outlier) to the asking-aware gate.
      Used at the raw source so a distrusted AVM never reaches the narrative.

  avm_is_corroborated(avm, asking, comp_median, comp_count)
      Full gate, used where the asking price is known. Comp median is the strong
      corroborator; asking is a weak sanity check when comps are thin.

This exists because the first cut (v5.89.231) gated only the market-intelligence
path and left the AI-narrative path (analysis_ai_helper / property_research_agent)
reading the raw AVM — so the fabricated value could still reach the buyer.
"""

from __future__ import annotations

# Trusted if the AVM is within this fraction of the independent comp median.
_AVM_COMP_TOL = 0.25      # 25%, requires >=3 sold comps
# Weak fallback when comps are thin: trusted if within this fraction of asking.
_AVM_ASKING_TOL = 0.20    # 20% of the (seller-set) asking price


def avm_is_comp_outlier(avm: int, comp_median: int, comp_count: int):
    """Source-side gate. Return (is_outlier, reason). Conservative — fires only
    on strong comp evidence against the AVM; defers when comps are thin."""
    if avm <= 0 or comp_count < 3 or comp_median <= 0:
        return False, ''
    dev = abs(avm - comp_median) / comp_median
    if dev > _AVM_COMP_TOL:
        return True, (f"AVM ${avm:,} is {round(dev * 100)}% from the comp "
                      f"median ${comp_median:,} ({comp_count} sold comps)")
    return False, ''


def avm_is_corroborated(avm: int, asking: int, comp_median: int, comp_count: int):
    """Full gate (asking known). Return (trusted, reason)."""
    if avm <= 0:
        return True, ''  # nothing to gate
    if comp_count >= 3 and comp_median > 0:
        dev = abs(avm - comp_median) / comp_median
        if dev <= _AVM_COMP_TOL:
            return True, ''
        return False, (f"AVM ${avm:,} is {round(dev * 100)}% from the comp "
                       f"median ${comp_median:,} ({comp_count} sold comps)")
    if asking > 0:
        dev = abs(avm - asking) / asking
        if dev <= _AVM_ASKING_TOL:
            return True, ''
        return False, (f"AVM ${avm:,} is {round(dev * 100)}% from asking "
                       f"${asking:,} with no comp corroboration")
    return False, f"AVM ${avm:,} cannot be corroborated (no comps, no asking price)"


def comp_median(prices) -> int:
    """Median of a list of sold-comp prices (0 if empty)."""
    vals = sorted(int(p) for p in prices if p and int(p) > 0)
    if not vals:
        return 0
    n = len(vals)
    return vals[n // 2]
