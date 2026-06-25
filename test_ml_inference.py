"""
test_ml_inference.py — v5.89.208

Smoke tests for ml_inference.py — previously the one core module with no direct
tests, sitting on top of a known 67%-"general" label bias.

Two things matter and both are hermetic (no real models, no training, no network):

1. GRACEFUL DEGRADATION. Models live on the persistent disk and can be absent
   after an ephemeral-disk deploy. When a model isn't loaded, classify/predict
   MUST return {'used_ml': False, ...} and never raise, so the analysis pipeline
   falls back to the LLM path instead of crashing the whole analysis.

2. DECODE CONTRACT. With fake models injected, classify() must map argmax to the
   right label, report confidence = min(category, severity), and — critically —
   decode through the CALIBRATOR's own class list when a calibrator is present.
   That last part is the v5.89.83 fix: a prefit calibrator can carry classes in a
   different order/coverage than the encoder, and decoding argmax directly against
   the encoder produces confident-WRONG labels. These tests fail if that
   regresses.
"""
import os
import unittest

os.environ.setdefault('FLASK_ENV', 'testing')
os.environ.setdefault('SECRET_KEY', 'test-secret-mlinf')
os.environ.setdefault('DATABASE_URL', 'sqlite:///test_coverage.db')

import numpy as np


# ── Fakes that stand in for the embedder / XGBoost models / encoders ──────────
class _FakeEmbedder:
    def encode(self, texts):
        n = len(texts) if not isinstance(texts, str) else 1
        return np.zeros((n, 4), dtype='float32')


class _FakeProbaModel:
    """Returns the same probability row for every sample."""
    def __init__(self, proba_row):
        self._row = list(proba_row)

    def predict_proba(self, emb):
        n = emb.shape[0] if hasattr(emb, 'shape') else len(emb)
        return np.array([self._row for _ in range(n)])


class _FakeEncoder:
    def __init__(self, classes):
        self.classes_ = np.array(list(classes))

    def inverse_transform(self, idxs):
        return [self.classes_[int(i)] for i in idxs]


class _FakeCalibrator:
    """Like a model, but also carries .classes_ (encoded int labels in the
    calibrator's own order) the way CalibratedClassifierCV does."""
    def __init__(self, proba_row, classes):
        self._row = list(proba_row)
        self.classes_ = np.array(list(classes))

    def predict_proba(self, emb):
        n = emb.shape[0] if hasattr(emb, 'shape') else len(emb)
        return np.array([self._row for _ in range(n)])


def _ready_classifier(cat_proba, cat_classes, sev_proba, sev_classes,
                      cat_cal=None, sev_cal=None):
    import ml_inference
    clf = ml_inference.FindingClassifier()
    clf._embedder = _FakeEmbedder()
    clf._cat_model = _FakeProbaModel(cat_proba)
    clf._sev_model = _FakeProbaModel(sev_proba)
    clf._cat_encoder = _FakeEncoder(cat_classes)
    clf._sev_encoder = _FakeEncoder(sev_classes)
    clf._cat_calibrator = cat_cal
    clf._sev_calibrator = sev_cal
    clf._ready = True
    return clf


class TestGracefulDegradation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import ml_inference
            cls.mli = ml_inference
            cls.available = True
        except Exception as e:
            cls.available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.available:
            self.skipTest(f"ml_inference unavailable: {self.skip_reason}")
        # Reset singletons so we get fresh, not-ready instances
        self.mli._classifier = None
        self.mli._contradiction_detector = None
        self.mli._cost_predictor = None

    def test_classifier_not_ready_returns_used_ml_false(self):
        clf = self.mli.get_classifier()
        self.assertFalse(clf.is_ready())
        r = clf.classify('Roof shingles are curling and there is attic moisture.')
        self.assertIs(r.get('used_ml'), False)
        self.assertIn('error', r)

    def test_classify_does_not_raise_on_empty_or_none_input(self):
        clf = self.mli.get_classifier()
        for bad in ('', None):
            r = clf.classify(bad)  # must not raise
            self.assertIs(r.get('used_ml'), False)

    def test_classify_batch_returns_one_result_per_input(self):
        clf = self.mli.get_classifier()
        out = clf.classify_batch(['a', 'b', 'c'])
        self.assertEqual(len(out), 3)
        self.assertTrue(all(r.get('used_ml') is False for r in out))

    def test_contradiction_detector_degrades(self):
        cd = self.mli.get_contradiction_detector()
        r = cd.classify('No known roof issues', 'Active roof leak in master bedroom')
        self.assertIs(r.get('used_ml'), False)

    def test_cost_predictor_degrades(self):
        cp = self.mli.get_cost_predictor()
        r = cp.predict('Replace water heater', category='plumbing', severity='major')
        self.assertIs(r.get('used_ml'), False)

    def test_init_ml_inference_returns_false_without_models_and_does_not_raise(self):
        # Patch each model's load to a no-op False so init can't download or
        # touch disk — we are testing the orchestration + return contract only.
        from unittest.mock import patch
        with patch.object(self.mli.FindingClassifier, 'load', return_value=False), \
             patch.object(self.mli.ContradictionDetector, 'load', return_value=False), \
             patch.object(self.mli.RepairCostPredictor, 'load', return_value=False):
            self.assertIs(self.mli.init_ml_inference(), False)


class TestSingletons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import ml_inference
            cls.mli = ml_inference
            cls.available = True
        except Exception as e:
            cls.available = False
            cls.skip_reason = str(e)

    def setUp(self):
        if not self.available:
            self.skipTest(f"ml_inference unavailable: {self.skip_reason}")
        self.mli._classifier = None
        self.mli._contradiction_detector = None
        self.mli._cost_predictor = None

    def test_get_classifier_is_idempotent_singleton(self):
        a = self.mli.get_classifier()
        b = self.mli.get_classifier()
        self.assertIs(a, b)
        self.assertEqual(type(a).__name__, 'FindingClassifier')

    def test_get_contradiction_detector_is_idempotent_singleton(self):
        self.assertIs(self.mli.get_contradiction_detector(),
                      self.mli.get_contradiction_detector())

    def test_get_cost_predictor_is_idempotent_singleton(self):
        self.assertIs(self.mli.get_cost_predictor(),
                      self.mli.get_cost_predictor())

    def test_stats_shape_when_not_ready(self):
        s = self.mli.get_classifier().stats()
        self.assertIs(s['ready'], False)
        self.assertEqual(s['categories'], [])
        self.assertEqual(s['severities'], [])
        self.assertIn('classifications', s)


class TestFindingClassifierDecodeContract(unittest.TestCase):
    """Drive the real classify() decode path with injected fakes."""

    def test_raw_path_decodes_argmax_to_label(self):
        clf = _ready_classifier(
            cat_proba=[0.1, 0.7, 0.2], cat_classes=['electrical', 'plumbing', 'general'],
            sev_proba=[0.2, 0.8], sev_classes=['minor', 'major'])
        r = clf.classify('leaky pipe under the sink')
        self.assertIs(r['used_ml'], True)
        self.assertEqual(r['category'], 'plumbing')   # argmax=1
        self.assertEqual(r['severity'], 'major')       # argmax=1
        self.assertEqual(r['category_confidence'], 0.7)
        self.assertEqual(r['severity_confidence'], 0.8)
        self.assertIs(r['calibrated'], False)

    def test_confidence_is_min_of_category_and_severity(self):
        clf = _ready_classifier(
            cat_proba=[0.1, 0.7, 0.2], cat_classes=['electrical', 'plumbing', 'general'],
            sev_proba=[0.2, 0.8], sev_classes=['minor', 'major'])
        r = clf.classify('x')
        self.assertEqual(r['confidence'], min(r['category_confidence'],
                                              r['severity_confidence']))
        self.assertEqual(r['confidence'], 0.7)

    def test_calibrated_path_decodes_through_calibrator_classes(self):
        """v5.89.83 guard. The calibrator's argmax must be mapped to a label
        THROUGH calibrator.classes_, not straight through the encoder. The
        fakes are arranged so the correct answer ('plumbing'/'minor') differs
        from the buggy direct-decode answer ('general'/'major')."""
        cat_cal = _FakeCalibrator(proba_row=[0.1, 0.2, 0.7], classes=[2, 0, 1])
        #   argmax=2 -> classes_[2]=1 -> encoder['plumbing']   (buggy: encoder[2]='general')
        sev_cal = _FakeCalibrator(proba_row=[0.3, 0.7], classes=[1, 0])
        #   argmax=1 -> classes_[1]=0 -> encoder['minor']      (buggy: encoder[1]='major')
        clf = _ready_classifier(
            cat_proba=[0, 0, 0], cat_classes=['electrical', 'plumbing', 'general'],
            sev_proba=[0, 0], sev_classes=['minor', 'major'],
            cat_cal=cat_cal, sev_cal=sev_cal)
        r = clf.classify('x')
        self.assertEqual(r['category'], 'plumbing',
            'calibrated category must decode through calibrator.classes_ (v5.89.83)')
        self.assertEqual(r['severity'], 'minor',
            'calibrated severity must decode through calibrator.classes_ (v5.89.83)')
        self.assertIs(r['calibrated'], True)

    def test_classify_batch_matches_single_classify(self):
        kw = dict(
            cat_proba=[0.1, 0.7, 0.2], cat_classes=['electrical', 'plumbing', 'general'],
            sev_proba=[0.2, 0.8], sev_classes=['minor', 'major'])
        single = _ready_classifier(**kw).classify('x')
        batch = _ready_classifier(**kw).classify_batch(['x', 'y'])
        self.assertEqual(len(batch), 2)
        for b in batch:
            self.assertEqual(b['category'], single['category'])
            self.assertEqual(b['severity'], single['severity'])
            self.assertEqual(b['confidence'], single['confidence'])


if __name__ == '__main__':
    unittest.main()
