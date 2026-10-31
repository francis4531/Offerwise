"""test_embedder_cache.py — v5.89.349

2026-10-31 production log: "ML inference: failed to download sentence-transformer:
No space left on device". The 1 GB persistent disk was full. Root cause in code:
the cache check looked for pytorch_model.bin, but sentence-transformers 3.x saves
model.safetensors, so a saved model was never recognized and every boot
downloaded again (HF snapshot + a second saved copy). These tests pin the fix.
"""
import os
import tempfile
import unittest

import ml_inference as mi


class TestEmbedderCache(unittest.TestCase):
    def test_safetensors_save_is_recognized(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(mi._embedder_cached_at(d))
            open(os.path.join(d, 'config.json'), 'w').write('{}')
            self.assertFalse(mi._embedder_cached_at(d))          # weights missing
            open(os.path.join(d, 'model.safetensors'), 'wb').write(b'x')
            self.assertTrue(mi._embedder_cached_at(d))

    def test_legacy_pytorch_bin_still_recognized(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, 'config.json'), 'w').write('{}')
            open(os.path.join(d, 'pytorch_model.bin'), 'wb').write(b'x')
            self.assertTrue(mi._embedder_cached_at(d))

    def test_disk_free_walks_up_to_existing_parent(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsInstance(mi.disk_free_mb(os.path.join(d, 'no', 'such', 'dir')), int)
        self.assertIsNone(mi.disk_free_mb(''))

    def test_low_disk_skips_download_and_does_not_import_torch(self):
        """With too little free space, load() must return False before touching
        sentence_transformers (a download that cannot finish leaves blobs behind)."""
        with tempfile.TemporaryDirectory() as d:
            eng = mi.FindingClassifier()
            orig_free = mi.disk_free_mb
            mi.disk_free_mb = lambda p: 10
            import sys
            sys.modules['sentence_transformers'] = None   # import would raise
            try:
                self.assertFalse(eng.load(base_dir=d))
            finally:
                mi.disk_free_mb = orig_free
                sys.modules.pop('sentence_transformers', None)

    def test_rmtree_quiet_tolerates_missing(self):
        mi._rmtree_quiet('/nonexistent/path/for/test')
        mi._rmtree_quiet(None)


if __name__ == '__main__':
    unittest.main()
