"""
test_prepackage_guard.py — v5.89.274. The prepackage guard is the fix for the two
CI breaks py_compile missed (a boot-time NameError, a coverage regression). This
verifies it distinguishes a real code bug from a merely-missing dependency, and that
the clean tree passes its import check — so the guard can be trusted to block bad
builds without false-failing a bare environment.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scripts'))
import prepackage_guard as g


def test_missing_module_name_classifies_correctly():
    assert g._missing_module_name(ModuleNotFoundError("x", name="flask")) == "flask"
    assert g._missing_module_name(NameError("_dev_only_gate")) is None
    assert g._missing_module_name(AttributeError("boom")) is None


def test_env_deps_recognised():
    # a missing third-party dep must NOT be counted as a code failure
    assert "flask" in g._ENV_DEPS and "flask_compress" in g._ENV_DEPS


def test_clean_tree_has_no_code_import_failures():
    # On a properly-installed tree the route modules import with zero code failures.
    # (If deps are missing this yields env_missing, not code_failures — either way
    # code_failures must be empty on a clean checkout.)
    code_failures, _env_missing = g.check_imports()
    assert code_failures == [], f"unexpected code import failures: {code_failures}"
