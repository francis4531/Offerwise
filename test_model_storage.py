"""
v5.89.58: tests for model_storage.py.

The module is small (94 lines) but high-stakes — it determines where every
trained ML artifact lives. Bugs here mean models silently disappear after
deploys (the exact problem v5.89.55 was meant to fix). These tests catch
regressions in the path-resolution logic.

Tested:
  * Dev fallback (no /var/data) → <app_dir>/models
  * MODELS_DIR env var override wins over everything
  * Directory is created (makedirs) on first call
  * Result is cached (second call doesn't re-evaluate)
  * reset_cache() forces re-evaluation

Not tested (would require mounting /var/data or running on Render):
  * Production path resolution to /var/data/models. Verified manually in
    Render Shell instead — `python3 -c "from model_storage import
    get_models_dir; print(get_models_dir())"` returns `/var/data/models`.
"""
import os
import unittest
import tempfile
import shutil
import sys


class TestModelStorage(unittest.TestCase):

    def setUp(self):
        # Each test starts with a clean module + clean env
        for mod in list(sys.modules.keys()):
            if 'model_storage' in mod:
                del sys.modules[mod]
        self._saved_env = os.environ.pop('MODELS_DIR', None)

    def tearDown(self):
        # Restore env var if test changed it
        if self._saved_env is not None:
            os.environ['MODELS_DIR'] = self._saved_env
        elif 'MODELS_DIR' in os.environ:
            del os.environ['MODELS_DIR']

    def test_dev_fallback_path(self):
        """No /var/data, no env var → returns <app_dir>/models."""
        from model_storage import get_models_dir, reset_cache
        reset_cache()
        d = get_models_dir()
        # In the dev environment, /var/data doesn't exist, so we fall back
        # to <app_dir>/models. The actual path varies by checkout location;
        # we just verify it ends with /models and is an absolute path.
        self.assertTrue(os.path.isabs(d), f"Expected absolute path, got {d}")
        self.assertTrue(d.endswith('/models'), f"Expected path ending in /models, got {d}")

    def test_makedirs_on_call(self):
        """get_models_dir() creates the directory if it doesn't exist."""
        from model_storage import get_models_dir, reset_cache
        reset_cache()
        d = get_models_dir()
        self.assertTrue(os.path.isdir(d), f"Directory not created: {d}")

    def test_env_var_override(self):
        """MODELS_DIR env var overrides any other resolution."""
        from model_storage import get_models_dir, reset_cache
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'custom_models')
            os.environ['MODELS_DIR'] = target
            reset_cache()
            d = get_models_dir()
            self.assertEqual(d, target)
            self.assertTrue(os.path.isdir(d))

    def test_cache_returns_same_path(self):
        """Repeated calls return the same path without re-evaluation."""
        from model_storage import get_models_dir, reset_cache
        reset_cache()
        d1 = get_models_dir()
        d2 = get_models_dir()
        d3 = get_models_dir()
        self.assertEqual(d1, d2)
        self.assertEqual(d2, d3)

    def test_reset_cache_re_evaluates(self):
        """After reset_cache(), env var changes take effect."""
        from model_storage import get_models_dir, reset_cache
        reset_cache()
        d1 = get_models_dir()
        with tempfile.TemporaryDirectory() as tmp:
            new_target = os.path.join(tmp, 'new_models')
            os.environ['MODELS_DIR'] = new_target
            # Without reset, cache wins
            d2 = get_models_dir()
            self.assertEqual(d2, d1)
            # With reset, new value
            reset_cache()
            d3 = get_models_dir()
            self.assertEqual(d3, new_target)

    def test_env_var_empty_string_ignored(self):
        """MODELS_DIR='' should be treated as unset, not as cwd."""
        from model_storage import get_models_dir, reset_cache
        os.environ['MODELS_DIR'] = ''
        reset_cache()
        d = get_models_dir()
        # Should fall back to default, not return ''
        self.assertTrue(d.endswith('/models'))
        self.assertNotEqual(d, '')


if __name__ == '__main__':
    unittest.main()
