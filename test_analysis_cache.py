"""Tests for AnalysisCache — deterministic caching."""
import unittest, sys, os, tempfile
sys.path.insert(0, os.path.dirname(__file__))
from analysis_cache import AnalysisCache

class TestCacheKey(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix='.db')
        self.c = AnalysisCache(db_path=self.tmp)
    def tearDown(self):
        if os.path.exists(self.tmp): os.remove(self.tmp)

    def test_same_inputs_same_key(self):
        k1 = self.c.generate_cache_key('insp text', 'disc text', 500000, {})
        k2 = self.c.generate_cache_key('insp text', 'disc text', 500000, {})
        self.assertEqual(k1, k2)
    def test_different_text_different_key(self):
        k1 = self.c.generate_cache_key('insp A', 'disc', 500000, {})
        k2 = self.c.generate_cache_key('insp B', 'disc', 500000, {})
        self.assertNotEqual(k1, k2)
    def test_different_price_different_key(self):
        k1 = self.c.generate_cache_key('insp', 'disc', 500000, {})
        k2 = self.c.generate_cache_key('insp', 'disc', 600000, {})
        self.assertNotEqual(k1, k2)
    def test_key_is_string(self):
        k = self.c.generate_cache_key('insp', 'disc', 100000, {})
        self.assertIsInstance(k, str)
        self.assertGreater(len(k), 10)

class TestGetSet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix='.db')
        self.c = AnalysisCache(db_path=self.tmp)
    def tearDown(self):
        if os.path.exists(self.tmp): os.remove(self.tmp)

    def test_miss(self): self.assertIsNone(self.c.get('nonexistent'))
    def test_roundtrip(self):
        self.c.set('k1', {'risk': 45}, property_address='a', asking_price=500000)
        r = self.c.get('k1')
        self.assertIsNotNone(r)
        self.assertEqual(r['risk'], 45)
    def test_overwrite(self):
        self.c.set('k', {'v': 1}, property_address='a', asking_price=1)
        self.c.set('k', {'v': 2}, property_address='a', asking_price=1)
        self.assertEqual(self.c.get('k')['v'], 2)

class TestStats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix='.db')
        self.c = AnalysisCache(db_path=self.tmp)
    def tearDown(self):
        if os.path.exists(self.tmp): os.remove(self.tmp)

    def test_empty(self):
        s = self.c.get_stats()
        self.assertEqual(s['total_entries'], 0)
    def test_after_insert(self):
        self.c.set('k1', {'v': 1}, property_address='a', asking_price=1)
        self.assertEqual(self.c.get_stats()['total_entries'], 1)

class TestCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix='.db')
        self.c = AnalysisCache(db_path=self.tmp)
    def tearDown(self):
        if os.path.exists(self.tmp): os.remove(self.tmp)
    def test_cleanup(self):
        self.c.set('k1', {'v': 1}, property_address='a', asking_price=1)
        self.c.cleanup_old_entries(days=0)
        self.assertEqual(self.c.get_stats()['total_entries'], 0)

if __name__ == '__main__': unittest.main()
