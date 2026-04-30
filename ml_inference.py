"""
OfferWise ML Inference
======================
Loads trained ML models at Flask startup and provides real-time finding classification.
Hybrid routing: ML for high-confidence predictions, Claude for the rest.

Usage:
    from ml_inference import get_classifier
    clf = get_classifier()
    if clf.is_ready():
        result = clf.classify("Water stains on ceiling near bathroom")
        if result['confidence'] >= 0.85:
            category = result['category']    # e.g. "plumbing"
            severity = result['severity']    # e.g. "major"
        else:
            # Fall back to Claude
            ...
"""

import os
import logging
import pickle
import numpy as np

logger = logging.getLogger(__name__)

# Singleton instance
_classifier = None


class FindingClassifier:
    """Classifies inspection findings into category + severity using trained XGBoost models.

    v5.87.0: If calibrator files are present (category_calibrator.pkl,
    severity_calibrator.pkl), uses them to produce well-calibrated confidence
    scores via isotonic regression. Falls back to raw XGBoost softmax if
    calibrators aren't present (e.g., when loading older model files).
    """

    def __init__(self):
        self._ready = False
        self._embedder = None
        self._cat_model = None
        self._sev_model = None
        self._cat_encoder = None
        self._sev_encoder = None
        self._cat_calibrator = None  # v5.87.0 — CalibratedClassifierCV or None
        self._sev_calibrator = None
        self._load_count = 0

    def load(self, base_dir=None):
        """Load all model files. Call once at Flask startup."""
        if base_dir is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))

        models_dir = os.path.join(base_dir, 'models')

        # Sentence-transformer: check multiple locations
        # 1. Persistent disk (survives deploys)
        # 2. Local tarball cache (baked into deploy)
        # 3. Download from HuggingFace (first boot only)
        cache_dir = None
        cache_candidates = [
            '/var/data/docrepo/model-cache/sentence-transformers/all-MiniLM-L6-v2',
            os.path.join(base_dir, 'model-cache', 'sentence-transformers', 'all-MiniLM-L6-v2'),
        ]
        for candidate in cache_candidates:
            if os.path.exists(os.path.join(candidate, 'pytorch_model.bin')):
                cache_dir = candidate
                break

        if cache_dir is None:
            # Download to persistent disk on first boot
            cache_dir = cache_candidates[0]  # persistent disk path
            logger.info(f"ML inference: sentence-transformer not cached, downloading to {cache_dir}...")
            try:
                from sentence_transformers import SentenceTransformer
                os.makedirs(os.path.dirname(cache_dir), exist_ok=True)
                model = SentenceTransformer('all-MiniLM-L6-v2', cache_folder=os.path.dirname(cache_dir))
                # Save to the expected path
                model.save(cache_dir)
                self._embedder = model
                logger.info("ML inference: sentence-transformer downloaded and cached to persistent disk")
            except Exception as dl_err:
                logger.warning(f"ML inference: failed to download sentence-transformer: {dl_err}")
                return False

        # Check required XGBoost files
        required = {
            'cat_model': os.path.join(models_dir, 'finding_category.xgb'),
            'sev_model': os.path.join(models_dir, 'finding_severity.xgb'),
            'cat_enc': os.path.join(models_dir, 'category_encoder.pkl'),
            'sev_enc': os.path.join(models_dir, 'severity_encoder.pkl'),
        }

        missing = [k for k, v in required.items() if not os.path.exists(v)]
        if missing:
            logger.info(f"ML inference: not loading — missing files: {missing}")
            return False

        try:
            # Load sentence-transformer if not already loaded during download
            if self._embedder is None:
                from sentence_transformers import SentenceTransformer
                self._embedder = SentenceTransformer(cache_dir)
                logger.info(f"ML inference: sentence-transformer loaded from {cache_dir}")

            # Load XGBoost models
            import xgboost as xgb
            self._cat_model = xgb.XGBClassifier()
            self._cat_model.load_model(required['cat_model'])

            self._sev_model = xgb.XGBClassifier()
            self._sev_model.load_model(required['sev_model'])

            # Load label encoders
            with open(required['cat_enc'], 'rb') as f:
                self._cat_encoder = pickle.load(f)
            with open(required['sev_enc'], 'rb') as f:
                self._sev_encoder = pickle.load(f)

            # v5.87.0: Load calibrators if present. Optional — we fall back
            # to raw XGBoost softmax if they're missing (e.g., older model
            # files from before calibration was added to the pipeline).
            cat_cal_path = os.path.join(models_dir, 'category_calibrator.pkl')
            sev_cal_path = os.path.join(models_dir, 'severity_calibrator.pkl')
            if os.path.exists(cat_cal_path):
                try:
                    with open(cat_cal_path, 'rb') as f:
                        self._cat_calibrator = pickle.load(f)
                    logger.info("ML inference: category calibrator loaded (isotonic)")
                except Exception as e:
                    logger.warning(f"ML inference: category calibrator failed — {e}")
            if os.path.exists(sev_cal_path):
                try:
                    with open(sev_cal_path, 'rb') as f:
                        self._sev_calibrator = pickle.load(f)
                    logger.info("ML inference: severity calibrator loaded (isotonic)")
                except Exception as e:
                    logger.warning(f"ML inference: severity calibrator failed — {e}")

            self._ready = True
            cat_classes = list(self._cat_encoder.classes_)
            sev_classes = list(self._sev_encoder.classes_)
            cal_status = (
                'calibrated' if (self._cat_calibrator and self._sev_calibrator) else
                'raw softmax' if not (self._cat_calibrator or self._sev_calibrator) else
                'partial calibration'
            )
            logger.info(f"ML inference: ready ({cal_status}) — categories={cat_classes}, severities={sev_classes}")
            return True

        except Exception as e:
            logger.warning(f"ML inference: failed to load — {type(e).__name__}: {e}")
            self._ready = False
            return False

    def is_ready(self):
        return self._ready

    def classify(self, finding_text):
        """
        Classify a finding text into category + severity.

        Returns:
            dict with keys:
                category: str (e.g. "plumbing")
                severity: str (e.g. "major")
                category_confidence: float (0-1)
                severity_confidence: float (0-1)
                confidence: float (min of both confidences)
                used_ml: True
        """
        if not self._ready:
            return {'used_ml': False, 'error': 'Model not loaded'}

        try:
            # Encode text
            embedding = self._embedder.encode([finding_text])

            # v5.87.0: prefer calibrated probas when calibrators are loaded.
            if self._cat_calibrator is not None:
                cat_proba = self._cat_calibrator.predict_proba(embedding)[0]
            else:
                cat_proba = self._cat_model.predict_proba(embedding)[0]
            cat_idx = int(np.argmax(cat_proba))
            cat_conf = float(cat_proba[cat_idx])
            category = self._cat_encoder.inverse_transform([cat_idx])[0]

            if self._sev_calibrator is not None:
                sev_proba = self._sev_calibrator.predict_proba(embedding)[0]
            else:
                sev_proba = self._sev_model.predict_proba(embedding)[0]
            sev_idx = int(np.argmax(sev_proba))
            sev_conf = float(sev_proba[sev_idx])
            severity = self._sev_encoder.inverse_transform([sev_idx])[0]

            self._load_count += 1

            return {
                'category': category,
                'severity': severity,
                'category_confidence': round(cat_conf, 3),
                'severity_confidence': round(sev_conf, 3),
                'confidence': round(min(cat_conf, sev_conf), 3),
                'calibrated': self._cat_calibrator is not None and self._sev_calibrator is not None,
                'used_ml': True,
            }

        except Exception as e:
            logger.warning(f"ML classify error: {e}")
            return {'used_ml': False, 'error': str(e)}

    def classify_batch(self, texts):
        """Classify multiple findings at once (more efficient)."""
        if not self._ready:
            return [{'used_ml': False, 'error': 'Model not loaded'}] * len(texts)

        try:
            embeddings = self._embedder.encode(texts)

            # v5.87.0: prefer calibrated probas when calibrators are loaded.
            # Falls back to raw XGBoost softmax if calibrators aren't present.
            if self._cat_calibrator is not None:
                cat_probas = self._cat_calibrator.predict_proba(embeddings)
            else:
                cat_probas = self._cat_model.predict_proba(embeddings)
            if self._sev_calibrator is not None:
                sev_probas = self._sev_calibrator.predict_proba(embeddings)
            else:
                sev_probas = self._sev_model.predict_proba(embeddings)

            results = []
            for i in range(len(texts)):
                cat_idx = int(np.argmax(cat_probas[i]))
                cat_conf = float(cat_probas[i][cat_idx])
                sev_idx = int(np.argmax(sev_probas[i]))
                sev_conf = float(sev_probas[i][sev_idx])

                # When calibrators are present, cat_idx indexes the calibrator's
                # class list, which is the same as the encoder's since calibrator
                # was fit on encoded labels. So inverse_transform works either way.
                results.append({
                    'category': self._cat_encoder.inverse_transform([cat_idx])[0],
                    'severity': self._sev_encoder.inverse_transform([sev_idx])[0],
                    'category_confidence': round(cat_conf, 3),
                    'severity_confidence': round(sev_conf, 3),
                    'confidence': round(min(cat_conf, sev_conf), 3),
                    'calibrated': self._cat_calibrator is not None and self._sev_calibrator is not None,
                    'used_ml': True,
                })

            self._load_count += len(texts)
            return results

        except Exception as e:
            logger.warning(f"ML batch classify error: {e}")
            return [{'used_ml': False, 'error': str(e)}] * len(texts)

    def stats(self):
        """Return runtime stats."""
        return {
            'ready': self._ready,
            'classifications': self._load_count,
            'categories': list(self._cat_encoder.classes_) if self._cat_encoder else [],
            'severities': list(self._sev_encoder.classes_) if self._sev_encoder else [],
        }


class ContradictionDetector:
    """Classifies seller claim + inspector finding pairs into contradiction/consistent/omission."""

    def __init__(self):
        self._ready = False
        self._embedder = None
        self._model = None
        self._encoder = None
        self._load_count = 0

    def load(self, base_dir=None, embedder=None):
        """Load contradiction model files. Can share embedder with FindingClassifier."""
        if base_dir is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))

        models_dir = os.path.join(base_dir, 'models')
        model_path = os.path.join(models_dir, 'contradiction_detector.xgb')
        enc_path = os.path.join(models_dir, 'contradiction_encoder.pkl')

        if not os.path.exists(model_path) or not os.path.exists(enc_path):
            logger.info("ML inference: contradiction detector not available (model files missing)")
            return False

        try:
            # Reuse embedder from FindingClassifier if available
            if embedder:
                self._embedder = embedder
            else:
                cache_dir = os.path.join(base_dir, 'model-cache', 'sentence-transformers', 'all-MiniLM-L6-v2')
                from sentence_transformers import SentenceTransformer
                self._embedder = SentenceTransformer(cache_dir)

            import xgboost as xgb
            self._model = xgb.XGBClassifier()
            self._model.load_model(model_path)

            with open(enc_path, 'rb') as f:
                self._encoder = pickle.load(f)

            self._ready = True
            logger.info(f"ML inference: contradiction detector loaded — labels={list(self._encoder.classes_)}")
            return True

        except Exception as e:
            logger.warning(f"ML inference: contradiction detector failed — {e}")
            return False

    def is_ready(self):
        return self._ready

    def classify(self, seller_claim, inspector_finding):
        """
        Classify a seller claim + inspector finding pair.

        Returns:
            dict with keys:
                label: str ('contradiction', 'consistent', 'omission')
                confidence: float (0-1)
                used_ml: True
        """
        if not self._ready:
            return {'used_ml': False, 'error': 'Model not loaded'}

        try:
            combined = f"{seller_claim} [SEP] {inspector_finding}"
            embedding = self._embedder.encode([combined])

            proba = self._model.predict_proba(embedding)[0]
            idx = np.argmax(proba)
            conf = float(proba[idx])
            label = self._encoder.inverse_transform([idx])[0]

            self._load_count += 1

            return {
                'label': label,
                'confidence': round(conf, 3),
                'used_ml': True,
            }
        except Exception as e:
            logger.warning(f"ML contradiction classify error: {e}")
            return {'used_ml': False, 'error': str(e)}

    def classify_batch(self, pairs):
        """Classify multiple (seller_claim, inspector_finding) pairs."""
        if not self._ready:
            return [{'used_ml': False, 'error': 'Model not loaded'}] * len(pairs)

        try:
            texts = [f"{s} [SEP] {f}" for s, f in pairs]
            embeddings = self._embedder.encode(texts)
            probas = self._model.predict_proba(embeddings)

            results = []
            for i in range(len(pairs)):
                idx = np.argmax(probas[i])
                conf = float(probas[i][idx])
                results.append({
                    'label': self._encoder.inverse_transform([idx])[0],
                    'confidence': round(conf, 3),
                    'used_ml': True,
                })
            self._load_count += len(pairs)
            return results
        except Exception as e:
            return [{'used_ml': False, 'error': str(e)}] * len(pairs)

    def stats(self):
        return {
            'ready': self._ready,
            'classifications': self._load_count,
            'labels': list(self._encoder.classes_) if self._encoder else [],
        }


# Singletons
_contradiction_detector = None
_cost_predictor = None


class RepairCostPredictor:
    """Predicts repair cost from finding text + metadata.

    v5.87.0: Uses quantile regression with p10, p50, p90 models. Returns a
    real prediction interval plus a confidence score derived from the width
    of that interval relative to the median prediction.

    Legacy compatibility: if only the old single-model `repair_cost.xgb` file
    is on disk (not yet retrained with quantile objective), falls back to
    point-estimate mode with heuristic bounds and confidence=0.7 (neutral).
    """

    def __init__(self):
        self._ready = False
        self._embedder = None
        self._model_p10 = None
        self._model_p50 = None
        self._model_p90 = None
        self._feature_meta = None
        self._quantile_mode = False  # True once quantile models load
        self._load_count = 0

    def load(self, base_dir=None, embedder=None):
        if base_dir is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))

        models_dir = os.path.join(base_dir, 'models')
        meta_path = os.path.join(models_dir, 'cost_feature_meta.pkl')

        # Prefer quantile models. Fall back to legacy single-model file.
        p10_path = os.path.join(models_dir, 'repair_cost_p10.xgb')
        p50_path = os.path.join(models_dir, 'repair_cost_p50.xgb')
        p90_path = os.path.join(models_dir, 'repair_cost_p90.xgb')
        legacy_path = os.path.join(models_dir, 'repair_cost.xgb')

        have_quantile = all(os.path.exists(p) for p in (p10_path, p50_path, p90_path))
        have_legacy = os.path.exists(legacy_path)

        if not (have_quantile or have_legacy) or not os.path.exists(meta_path):
            logger.info("ML inference: repair cost model not available")
            return False

        try:
            if embedder:
                self._embedder = embedder
            else:
                cache_dir = os.path.join(base_dir, 'model-cache', 'sentence-transformers', 'all-MiniLM-L6-v2')
                from sentence_transformers import SentenceTransformer
                self._embedder = SentenceTransformer(cache_dir)

            import xgboost as xgb

            with open(meta_path, 'rb') as f:
                self._feature_meta = pickle.load(f)

            if have_quantile and self._feature_meta.get('quantile_version') == 'v1':
                self._model_p10 = xgb.XGBRegressor(); self._model_p10.load_model(p10_path)
                self._model_p50 = xgb.XGBRegressor(); self._model_p50.load_model(p50_path)
                self._model_p90 = xgb.XGBRegressor(); self._model_p90.load_model(p90_path)
                self._quantile_mode = True
                logger.info("ML inference: repair cost quantile models loaded (p10/p50/p90)")
            else:
                # Legacy single-model mode — use repair_cost.xgb as p50-only
                self._model_p50 = xgb.XGBRegressor(); self._model_p50.load_model(legacy_path)
                self._quantile_mode = False
                logger.info("ML inference: repair cost loaded in legacy point-estimate mode")

            self._ready = True
            return True
        except Exception as e:
            logger.warning(f"ML inference: repair cost model failed — {e}")
            return False

    def is_ready(self):
        return self._ready

    def predict(self, finding_text, category='', severity='', zip_code='', property_price=0):
        """
        Predict repair cost for a finding.

        Returns dict with:
          cost_low, cost_mid, cost_high: dollar estimates
          confidence: float 0-1 indicating model certainty
            Quantile mode: derived from interval width relative to median
            Legacy mode:   fixed at 0.70 (neutral; no distributional info)
          used_ml: bool
        """
        if not self._ready:
            return {'used_ml': False, 'error': 'Model not loaded'}

        try:
            embedding = self._embedder.encode([finding_text])

            meta = self._feature_meta
            cat_cols = meta.get('category_columns', [])
            sev_cols = meta.get('severity_columns', [])

            cat_features = np.zeros(len(cat_cols))
            for i, col in enumerate(cat_cols):
                if col == f'cat_{category}':
                    cat_features[i] = 1.0

            sev_features = np.zeros(len(sev_cols))
            for i, col in enumerate(sev_cols):
                if col == f'sev_{severity}':
                    sev_features[i] = 1.0

            zip_num = float(zip_code) / 100000.0 if zip_code and zip_code.isdigit() else 0.0
            price_norm = float(property_price or 0) / 1_000_000.0

            structured = np.concatenate([cat_features, sev_features, [zip_num, price_norm]])
            X = np.hstack([embedding, structured.reshape(1, -1)])

            if self._quantile_mode:
                # Three-model quantile prediction
                pred_p50_log = float(self._model_p50.predict(X)[0])
                pred_p10_log = float(self._model_p10.predict(X)[0])
                pred_p90_log = float(self._model_p90.predict(X)[0])

                # Convert back from log space. Guard against p10 > p50 or p50 > p90
                # edge cases (can happen rarely due to model noise).
                cost_p50 = max(0.0, float(np.expm1(pred_p50_log)))
                cost_p10 = max(0.0, float(np.expm1(pred_p10_log)))
                cost_p90 = max(0.0, float(np.expm1(pred_p90_log)))
                # Enforce ordering
                cost_p10 = min(cost_p10, cost_p50)
                cost_p90 = max(cost_p90, cost_p50)

                # Confidence from relative interval width, matching the formula
                # used during training (see feature_meta['confidence_decay_factor'])
                decay = float(meta.get('confidence_decay_factor', 1.5))
                rel_width = (cost_p90 - cost_p10) / max(cost_p50, 1.0)
                confidence = float(np.exp(-rel_width / decay))
                confidence = max(0.0, min(1.0, confidence))

                cost_low = round(cost_p10, -1)
                cost_mid = round(cost_p50, -1)
                cost_high = round(cost_p90, -1)
            else:
                # Legacy point-estimate mode — heuristic bounds, neutral confidence
                pred_log = float(self._model_p50.predict(X)[0])
                cost_mid = max(0.0, float(np.expm1(pred_log)))
                cost_low = round(cost_mid * 0.7, -1)
                cost_high = round(cost_mid * 1.4, -1)
                cost_mid = round(cost_mid, -1)
                confidence = 0.70

            self._load_count += 1

            return {
                'cost_low': cost_low,
                'cost_mid': cost_mid,
                'cost_high': cost_high,
                'confidence': round(confidence, 3),
                'used_ml': True,
                'quantile_mode': self._quantile_mode,
            }
        except Exception as e:
            logger.warning(f"ML cost predict error: {e}")
            return {'used_ml': False, 'error': str(e)}

    def stats(self):
        return {
            'ready': self._ready,
            'quantile_mode': self._quantile_mode,
            'predictions': self._load_count,
        }


def get_classifier():
    """Get or create the singleton FindingClassifier."""
    global _classifier
    if _classifier is None:
        _classifier = FindingClassifier()
    return _classifier


def get_contradiction_detector():
    """Get or create the singleton ContradictionDetector."""
    global _contradiction_detector
    if _contradiction_detector is None:
        _contradiction_detector = ContradictionDetector()
    return _contradiction_detector


def get_cost_predictor():
    """Get or create the singleton RepairCostPredictor."""
    global _cost_predictor
    if _cost_predictor is None:
        _cost_predictor = RepairCostPredictor()
    return _cost_predictor


def init_ml_inference(app_base_dir=None):
    """Initialize all ML models at Flask startup."""
    clf = get_classifier()
    if not clf.is_ready():
        clf.load(base_dir=app_base_dir)

    shared_embedder = clf._embedder if clf.is_ready() else None

    cd = get_contradiction_detector()
    if not cd.is_ready():
        cd.load(base_dir=app_base_dir, embedder=shared_embedder)

    cp = get_cost_predictor()
    if not cp.is_ready():
        cp.load(base_dir=app_base_dir, embedder=shared_embedder or (cd._embedder if cd.is_ready() else None))

    ready_models = []
    if clf.is_ready():
        ready_models.append("FindingClassifier")
    if cd.is_ready():
        ready_models.append("ContradictionDetector")
    if cp.is_ready():
        ready_models.append("RepairCostPredictor")

    return len(ready_models) > 0
