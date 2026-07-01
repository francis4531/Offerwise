"""Regression gate: the reasoning core must produce the Pendleton answer key.

Runs Layer B only (deterministic, offline) so it's a fast CI gate. The full
model-as-pass path (Layer A) runs on staging where an API key is present — see
reasoning_pendleton_regression.py.
"""
from reasoning_pendleton_regression import run_layer_b


def test_pendleton_reasoning_core_surfaces_answer_key():
    # 0 failures => the engine surfaced FPE (#3, silent), flue/CO (#3b, silent),
    # water intrusion (#1/#2), active leak (#6), and did NOT invent a foundation
    # concern (#4 is a risk-down). These are exactly the items the live keyword
    # engine got wrong on this deal.
    assert run_layer_b() == 0
