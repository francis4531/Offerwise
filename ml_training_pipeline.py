"""OfferWise ML Training Pipeline — per-model subprocess entrypoints.

Each training step runs in its own Python process, so peak memory from one
model's encoding + fitting phase cannot compound with another's. When a
subprocess exits, the OS fully reclaims its memory. This is the bulletproof
architecture for 2GB-RAM environments where the embedder (~250MB) + XGBoost
training matrices + intermediate numpy arrays can collectively exceed limits
if they all coexist in one process.

Each function:
  1. Reads job state from disk (for logging into the shared log list)
  2. Loads embedder, queries DB, trains one model, saves artifacts
  3. Writes its portion of `results` back to the state file
  4. Returns (allowing the subprocess to exit cleanly)

Called via:
    python ml_training_pipeline.py <step> --job-id <uuid>

Steps: finding_classifier, contradiction_detector, cost_predictor, post_training
"""

import argparse
import json as _json
import os
import pickle
import sys
import time
import traceback
from collections import Counter


# ── Shared helpers ────────────────────────────────────────────────────────────

def _make_logger(job_id, t_start):
    """Build a _log function scoped to this subprocess's job state file.

    Each subprocess appends to the job's `log` list on disk. Persists every 10s.
    The web-worker heartbeat keeps the file's mtime fresh between persist calls.
    """
    from admin_routes import _load_job_state, _save_job_state

    def _log(msg, level='info'):
        # Read-modify-write the state file on every call. Without this, between
        # 10s-interval persists, the disk file is stale and the next _log call
        # would load it and overwrite in-memory appends — effectively throwing
        # away every log line between saves. Writing every call is fine: the
        # file is <100KB, training emits <200 lines total, disk I/O is trivial.
        try:
            state = _load_job_state(job_id) or {}
            log = state.setdefault('log', [])
            log.append({'t': round(time.time() - t_start, 1), 'msg': msg, 'level': level})
            state['elapsed_total'] = time.time() - state.get('started_at', t_start)
            _save_job_state(job_id, state)
        except Exception:
            pass  # logging must never break training

    return _log


def _update_results(job_id, partial_results):
    """Merge partial_results into the job's results dict on disk."""
    from admin_routes import _load_job_state, _save_job_state
    try:
        state = _load_job_state(job_id) or {}
        existing = state.get('results') or {}
        existing.update(partial_results)
        state['results'] = existing
        _save_job_state(job_id, state)
    except Exception as e:
        print(f'[pipeline] _update_results failed: {e}', file=sys.stderr)


def _emit_classification_report(_log, y_true, y_pred, target_names, indent='  '):
    """Emit a sklearn-style classification report, one log line per row.

    Mirrors what sklearn.metrics.classification_report prints — header row,
    per-class precision/recall/f1/support, and accuracy/macro-avg/weighted-avg
    summary. This is the train.sh-style detail the user wants to see in the
    admin console.
    """
    try:
        from sklearn.metrics import classification_report
        report = classification_report(
            y_true, y_pred,
            target_names=target_names,
            zero_division=0,
            digits=2,
        )
        # Emit each line of the formatted report — preserves alignment.
        for line in report.split('\n'):
            if line.strip():
                _log(f'{indent}{line}')
            else:
                _log('')  # blank line for visual separation
    except Exception as e:
        _log(f'{indent}(could not generate per-class report: {e})', 'warn')


def _emit_distribution(_log, label, counts, indent='  '):
    """Emit a histogram-style distribution summary like train.sh does.

    Example output:
        Category distribution:
           foundation_structure            199  ################
           hvac_systems                     95  ########
    """
    if not counts:
        return
    _log(f'{label}:')
    max_cnt = max(counts.values())
    for key, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        bar_len = max(1, int(cnt / max_cnt * 20))
        bar = '#' * bar_len
        _log(f'{indent}{key:28s} {cnt:5d}  {bar}')


def _get_embedder(_log):
    """Load the sentence-transformer embedder. Raises if it can't."""
    from ml_inference import get_classifier
    clf = get_classifier()
    if clf._embedder is None:
        # init_ml_inference didn't fire or failed. Force-load.
        from sentence_transformers import SentenceTransformer
        base_dir = os.path.dirname(os.path.abspath(__file__))
        cache_dir = os.path.join(base_dir, '.sentence_transformer_cache')
        _log('Loading sentence-transformer directly...')
        clf._embedder = SentenceTransformer('all-MiniLM-L6-v2', cache_folder=cache_dir)
    if clf._embedder is None:
        raise RuntimeError('Could not load sentence-transformer. Check model cache and Render logs.')
    return clf._embedder


# ── Step 1: Finding Classifier ────────────────────────────────────────────────

def train_finding_classifier(job_id):
    """Train category + severity classifiers. ~800MB peak memory."""
    import numpy as np
    from sklearn.preprocessing import LabelEncoder
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score, classification_report
    import xgboost as xgb
    from models import MLFindingLabel

    t_start = time.time()
    _log = _make_logger(job_id, t_start)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    models_dir = os.path.join(base_dir, 'models')
    os.makedirs(models_dir, exist_ok=True)

    _log('═══ MODEL 1: FINDING CLASSIFIER ═══', 'header')
    try:
        # v5.86.99 OOM fix: previously we materialized all 79K rows as full
        # ORM objects via .all(), which consumed ~200MB just for the corpus
        # load before any ML work. Combined with the sentence-transformer
        # model (~90MB) and XGBoost training memory (~500MB), we blew past
        # Render's 2GB cgroup limit and got OOM-killed.
        #
        # Fix: query only the columns we use as tuples (no ORM overhead),
        # and stream via yield_per so rows flow through the filter loop
        # instead of all sitting in memory at once. Same semantic behavior,
        # ~80% lower memory for this phase.
        _log('Streaming finding rows from DB (columns-only, batched)...')
        row_query = (MLFindingLabel.query
                     .with_entities(
                         MLFindingLabel.finding_text,
                         MLFindingLabel.category,
                         MLFindingLabel.severity,
                         MLFindingLabel.category_v2,
                         MLFindingLabel.severity_v2,
                         MLFindingLabel.is_real_finding,
                         MLFindingLabel.source,
                     )
                     .yield_per(2000))
        # We still need a total count for logging
        total_count = MLFindingLabel.query.count()
        _log(f'Streaming {total_count:,} finding labels')

        # v5.86.80: prefer v2 labels (from Stream 3 relabeler) when present.
        # The relabeler also flags boilerplate/disclaimer rows as
        # is_real_finding=False — those rows are skipped here so the
        # classifier doesn't learn from junk text.
        #
        # Row selection logic:
        #   - is_real_finding=False  → SKIP (Haiku flagged as junk)
        #   - is_real_finding=True   → use, prefer v2 labels
        #   - is_real_finding=None   → use (row hasn't been relabeled yet,
        #                              fall back to v1 labels)
        #
        # This means the corpus transitions smoothly as the relabel job
        # progresses: partially-labeled corpus still trains, and each
        # relabel pass shifts more rows from "v1 fallback" to "v2 primary".
        cat_map = {"foundation": "foundation_structure", "exterior": "roof_exterior",
                   "foundation & structure": "foundation_structure", "roof & exterior": "roof_exterior",
                   "hvac & systems": "hvac_systems", "hvac": "hvac_systems",
                   "roof": "roof_exterior", "general": "general",
                   "water_damage": "environmental", "pest": "environmental",
                   "safety": "electrical", "permits": "general", "legal & title": "general"}

        data = []
        stats = {'total': total_count, 'junk_filtered': 0, 'used_v2': 0,
                 'used_v1_fallback': 0, 'dropped_invalid': 0, 'dropped_short_text': 0,
                 'augmented_skipped': 0}

        # Stream rows in column-tuple form (from with_entities() + yield_per()).
        # Each row is a 7-tuple matching the with_entities() column order above.
        for row_tuple in row_query:
            (finding_text, category_v1, severity_v1,
             category_v2, severity_v2, is_real_finding, source) = row_tuple

            # Skip Haiku-flagged junk (disclaimers, boilerplate, taxonomy labels)
            if is_real_finding is False:
                stats['junk_filtered'] += 1
                continue

            # v5.86.92: skip ai_augmented rows. They were generated to oversample
            # critical severity when natural critical was 0.09% of corpus. After
            # the v2 re-label, critical is now 16.8% naturally — augmentation no
            # longer needed and may be hurting: augmented rows use vivid technical
            # language ("imminent failure") that creates spurious correlation
            # between linguistic register and severity, causing minor/moderate
            # findings with descriptive language to be over-predicted as critical.
            if source == 'ai_augmented':
                stats['augmented_skipped'] += 1
                continue

            text = (finding_text or '').strip()
            if len(text) <= 10:
                stats['dropped_short_text'] += 1
                continue

            # Prefer v2 labels; fall back to v1 with cat_map normalization
            v2_cat = (category_v2 or '').strip().lower()
            v2_sev = (severity_v2 or '').strip().lower()

            if v2_cat and v2_sev:
                cat, sev = v2_cat, v2_sev
                stats['used_v2'] += 1
            else:
                cat = (category_v1 or '').strip().lower()
                sev = (severity_v1 or '').strip().lower()
                cat = cat_map.get(cat, cat)
                stats['used_v1_fallback'] += 1

            if cat and sev and sev in ('critical', 'major', 'moderate', 'minor'):
                data.append({'text': text, 'category': cat, 'severity': sev})
            else:
                stats['dropped_invalid'] += 1

        # v5.86.99: Release the streaming query's session resources now that
        # we've accumulated the compact `data` list. SQLAlchemy may otherwise
        # hold references to result rows in an identity map.
        from models import db as _db
        _db.session.expunge_all()
        _db.session.close()

        _log(f'Selection stats: v2_labeled={stats["used_v2"]}, v1_fallback={stats["used_v1_fallback"]}, '
             f'junk_filtered={stats["junk_filtered"]}, augmented_skipped={stats["augmented_skipped"]}, '
             f'short_text={stats["dropped_short_text"]}, invalid={stats["dropped_invalid"]}')
        _log(f'Training set: {len(data)} rows ({stats["used_v2"]} v2, {stats["used_v1_fallback"]} v1 fallback)')

        # Dedup
        seen = set()
        deduped = []
        for d in data:
            if d['text'] not in seen:
                seen.add(d['text'])
                deduped.append(d)
        data = deduped
        _log(f'After cleaning + dedup: {len(data)} unique findings')

        cat_counts = Counter(d['category'] for d in data)
        sev_counts = Counter(d['severity'] for d in data)
        _log('')
        _emit_distribution(_log, 'Category distribution', cat_counts)
        _log('')
        _emit_distribution(_log, 'Severity distribution', sev_counts)

        if len(data) < 20:
            _log(f'Only {len(data)} findings — need 20+ to train', 'warn')
            _update_results(job_id, {'Finding Classifier': {'status': 'NOT ENOUGH DATA', 'data_points': len(data)}})
            return

        # Augmentation
        aug_count = 0
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if api_key:
            try:
                _log('Checking class balance for augmentation...')
                target_cat = int(max(cat_counts.values()) * 0.75)
                target_sev = int(max(sev_counts.values()) * 0.75)
                # Per-batch cap: prevents max_tokens overflow on the API response.
                # At ~60 tokens per finding JSON object, 30 items × 8 batches ≈ 14K
                # tokens worth of generation, comfortably under the 8000 max_tokens
                # even with prompt overhead. Previously: unbounded needs produced
                # 3000+ item requests that silently truncated mid-string.
                AUG_PER_BATCH = 30
                needs = []
                for cat, cnt in cat_counts.items():
                    if cnt < target_cat:
                        need = min(target_cat - cnt, cnt * 2, AUG_PER_BATCH)
                        if need >= 3:
                            # Pull diverse examples — don't just grab the first 2,
                            # which tend to be the same style. Sample across severities
                            # so Claude sees the range of what's in this category.
                            import random as _rnd
                            cat_rows = [d for d in data if d['category'] == cat]
                            _rnd.seed(hash(cat) % 10000)
                            _rnd.shuffle(cat_rows)
                            examples = [d['text'] for d in cat_rows[:3]]
                            needs.append(f'{need} findings for category "{cat}" (examples of existing style: {" | ".join(examples)})')
                            _log(f'  Need +{need} for {cat} (has {cnt}, target {target_cat})')
                for sev, cnt in sev_counts.items():
                    if cnt < target_sev:
                        need = min(target_sev - cnt, cnt * 2, AUG_PER_BATCH)
                        if need >= 3:
                            cats_for_sev = list(set(d['category'] for d in data if d['severity'] == sev))
                            # Same diversity fix for severity examples
                            import random as _rnd
                            sev_rows = [d for d in data if d['severity'] == sev]
                            _rnd.seed(hash(sev) % 10000)
                            _rnd.shuffle(sev_rows)
                            examples = [d['text'] for d in sev_rows[:3]]
                            needs.append(f'{need} findings with severity "{sev}" across {", ".join(cats_for_sev)} (examples of existing style: {" | ".join(examples)})')
                            _log(f'  Need +{need} for severity {sev} (has {cnt}, target {target_sev})')
                if needs:
                    _log(f'Calling Claude to generate {len(needs)} augmentation batches...')
                    from anthropic import Anthropic
                    client = Anthropic(api_key=api_key)
                    needs_text = '\n'.join(f'- {n}' for n in needs)
                    # The crucial instruction: generate REALISTIC DIVERSITY, not just
                    # worst-case examples. Previous prompt produced only extreme cases
                    # (e.g. every radon finding at 18+ pCi/L severity=critical), which
                    # taught the model "keyword radon → critical" regardless of level.
                    prompt = f"""Generate realistic, DIVERSE home inspection findings to balance a training dataset.

Critical instruction: produce a REALISTIC RANGE of severity within each finding, not just worst-case examples. Include borderline and moderate cases, not only catastrophic ones.

Severity guidelines (use all levels appropriately):
- critical: active safety hazard or imminent catastrophic failure (gas leak, active fire risk, structural collapse imminent, lead exposure to children above 40 μg/dL, radon above 20 pCi/L in occupied space)
- major: significant defect requiring prompt repair ($5K-$25K typical, still functional but degrading — radon 10-20 pCi/L, moderate mold, roof at end of life)
- moderate: defect that warrants attention within 6-12 months ($500-$5K — radon 4-10 pCi/L at EPA action level, minor water intrusion, HVAC near end of life, roof granule loss with 3-5 years remaining)
- minor: cosmetic or low-urgency item (<$500 — caulking, minor paint peeling, worn weather stripping, slight granule loss on newer roof)

Realistic diversity rules:
- For environmental findings (radon, mold, asbestos, lead): include BORDERLINE values near EPA action thresholds, not just catastrophic levels. Mix moderate/major with critical.
- For roof/shingle findings: include normal-aging cases (moderate granule loss, brittleness) with severity moderate or major, not every example as critical.
- For HVAC findings: include routine maintenance issues (severity minor/moderate), not just total system failures.
- For each category, aim for roughly: 15% critical, 35% major, 35% moderate, 15% minor unless the need explicitly targets one severity.

Sound like a professional home inspector. Use specific technical language with concrete measurements.

Categories: {', '.join(cat_counts.keys())}
Severities: critical, major, moderate, minor

Generate these quantities:
{needs_text}

Respond with ONLY a JSON array:
[{{"text": "finding", "category": "cat", "severity": "sev"}}]"""
                    resp = client.messages.create(model='claude-sonnet-4-20250514', max_tokens=8000,
                        messages=[{'role': 'user', 'content': prompt}])
                    raw = resp.content[0].text.strip()
                    if raw.startswith('```'):
                        raw = raw.split('\n', 1)[-1]
                    if raw.endswith('```'):
                        raw = raw[:-3]
                    raw = raw.strip()
                    if raw.startswith('json'):
                        raw = raw[4:].strip()
                    items = _json.loads(raw)
                    for item in items:
                        if isinstance(item, dict) and item.get('text') and item.get('category') and item.get('severity'):
                            data.append({'text': item['text'], 'category': item['category'].lower().strip(),
                                'severity': item['severity'].lower().strip()})
                            aug_count += 1
                    _log(f'✅ Augmented with {aug_count} synthetic findings (total: {len(data)})', 'success')
                else:
                    _log('Classes well-balanced, no augmentation needed')
            except Exception as aug_err:
                _log(f'⚠ Augmentation skipped: {aug_err}', 'warn')

        # Row cap for 2GB Render Standard plan.
        # History: v5.86.51=5K (tests failing, underfit), v5.86.54=15K (OOM-killed),
        # v5.86.55=10K (compromise). The issue at 15K wasn't the resulting embedding
        # matrix (that's only 23MB) — it was transient torch activation memory
        # during forward passes. 10K + smaller chunks should fit comfortably.
        MAX_TRAINING_ROWS = 10000
        if len(data) > MAX_TRAINING_ROWS:
            _log(f'⚠ Dataset has {len(data):,} rows — capping to {MAX_TRAINING_ROWS:,} (severity-stratified within category)', 'warn')
            import random
            random.seed(42)

            # v5.86.92: Sample severity-stratified WITHIN each category instead of
            # uniform random within category. Goal: force the model to learn
            # severity gradations from substance, not from linguistic register.
            # Previous behavior: random.sample() within each category gave whatever
            # severity mix the corpus happened to have, which was minor-heavy.
            # New behavior: aim for 15/35/35/15 critical/major/moderate/minor
            # within each category. Best-effort — if a (category, severity) bucket
            # has fewer rows than its target, take what's available; remainder
            # spills to other severities to maintain per_cat total.
            SEVERITY_TARGETS = {'critical': 0.15, 'major': 0.35, 'moderate': 0.35, 'minor': 0.15}

            by_cat = {}
            for d in data:
                by_cat.setdefault(d['category'], []).append(d)

            sampled = []
            per_cat = max(100, MAX_TRAINING_ROWS // len(by_cat))

            sev_dist_log = []  # for surfacing what the sampler actually produced
            for cat, items in by_cat.items():
                if len(items) <= per_cat:
                    sampled.extend(items)
                    sev_counts = {}
                    for d in items:
                        sev_counts[d['severity']] = sev_counts.get(d['severity'], 0) + 1
                    sev_dist_log.append(f'{cat}: all {len(items)} (sev: {sev_counts})')
                    continue

                # Bucket items by severity
                by_sev = {'critical': [], 'major': [], 'moderate': [], 'minor': []}
                for d in items:
                    if d['severity'] in by_sev:
                        by_sev[d['severity']].append(d)

                # Compute desired count per severity, then take what's available
                cat_sample = []
                deficits = {}  # how many rows each severity bucket couldn't supply
                for sev, target_frac in SEVERITY_TARGETS.items():
                    want = int(per_cat * target_frac)
                    avail = by_sev[sev]
                    if len(avail) >= want:
                        cat_sample.extend(random.sample(avail, want))
                    else:
                        cat_sample.extend(avail)
                        deficits[sev] = want - len(avail)

                # If any severity was short, top up from severities that have surplus.
                # Cycle through major→moderate→minor→critical so we don't bias toward any one.
                shortfall = sum(deficits.values())
                if shortfall > 0:
                    surplus_pool = []
                    already_picked_ids = set(id(d) for d in cat_sample)
                    for sev in ('major', 'moderate', 'minor', 'critical'):
                        for d in by_sev[sev]:
                            if id(d) not in already_picked_ids:
                                surplus_pool.append(d)
                    if surplus_pool:
                        random.shuffle(surplus_pool)
                        cat_sample.extend(surplus_pool[:shortfall])

                sampled.extend(cat_sample)
                sev_counts = {}
                for d in cat_sample:
                    sev_counts[d['severity']] = sev_counts.get(d['severity'], 0) + 1
                sev_dist_log.append(f'{cat}: {len(cat_sample)} (sev: {sev_counts})')

            data = sampled[:MAX_TRAINING_ROWS]
            _log(f'  Sampled: {len(data):,} rows across {len(by_cat)} categories')
            for line in sev_dist_log:
                _log(f'    {line}')

            # v5.86.99: Release intermediate bucket structures before the
            # memory-heavy encoding phase. by_sev and surplus_pool are scoped
            # to the for-loop iterations (may or may not exist at outer scope
            # depending on short-circuit); by_cat and sampled are always set
            # when we reach this point. Reassign to None for safety.
            by_cat = None
            sampled = None
            import gc
            gc.collect()

        # Log memory so we know how close we are to the 2GB cgroup limit
        def _log_mem(tag):
            try:
                import psutil
                mb = psutil.Process().memory_info().rss / (1024 * 1024)
                _log(f'  [mem:{tag}] {mb:.0f}MB RSS')
            except Exception:
                pass
        _log_mem('pre-encode')

        embedder = _get_embedder(_log)
        texts = [d['text'] for d in data]
        cats = [d['category'] for d in data]
        sevs = [d['severity'] for d in data]

        # Chunked encoding. chunk_size was 2000 in v5.86.54 which OOMed during
        # encoding at 15K rows — the transient torch activation tensors during
        # forward passes are the real memory peak, not the output matrix.
        # 500 rows/chunk × 32-batch = much smaller transient peak. Also gc.collect()
        # between chunks to force torch to release intermediate buffers.
        import gc
        chunk_size = 500
        encode_batch = 32
        n_texts = len(texts)
        _log(f'Encoding {n_texts} texts in chunks of {chunk_size} (batch={encode_batch})...')
        emb_chunks = []
        for start in range(0, n_texts, chunk_size):
            end = min(start + chunk_size, n_texts)
            chunk = embedder.encode(texts[start:end], batch_size=encode_batch, show_progress_bar=False)
            emb_chunks.append(chunk)
            if n_texts > chunk_size and (end % 2000 == 0 or end == n_texts):
                _log(f'  Encoded {end}/{n_texts} ({end*100//n_texts}%)')
                _log_mem('encoding')
            gc.collect()  # release torch intermediate tensors immediately
        emb = np.vstack(emb_chunks)
        del emb_chunks
        gc.collect()
        _log(f'Encoded: {emb.shape} embedding matrix')
        _log_mem('post-encode')

        cat_enc = LabelEncoder().fit(cats)
        sev_enc = LabelEncoder().fit(sevs)
        y_cat = cat_enc.transform(cats)
        y_sev = sev_enc.transform(sevs)

        try:
            X_tr, X_te, yc_tr, yc_te, ys_tr, ys_te = train_test_split(
                emb, y_cat, y_sev, test_size=0.2, random_state=42, stratify=y_cat)
        except ValueError:
            X_tr, X_te, yc_tr, yc_te, ys_tr, ys_te = train_test_split(
                emb, y_cat, y_sev, test_size=0.2, random_state=42)
        _log(f'Train/test split: {len(X_tr)} train, {len(X_te)} test')
        del emb, texts, cats, sevs, y_cat, y_sev
        gc.collect()
        _log_mem('post-split')

        # Class weights — inverse frequency. Tells XGBoost: "predicting a rare
        # class wrong is N× worse than predicting common class wrong."
        # Without this, severity classifier ignores 'critical' (49/56K = 0.09%)
        # because predicting 'moderate' is statistically rational.
        from collections import Counter as _C
        _cat_counts_arr = _C(yc_tr.tolist())
        _sev_counts_arr = _C(ys_tr.tolist())
        _n_cat = len(yc_tr)
        _n_sev = len(ys_tr)
        # Weight = total_samples / (n_classes × class_count). Standard sklearn formula.
        cat_weights = np.array([
            _n_cat / (len(_cat_counts_arr) * _cat_counts_arr[y]) for y in yc_tr
        ], dtype=np.float32)
        sev_weights = np.array([
            _n_sev / (len(_sev_counts_arr) * _sev_counts_arr[y]) for y in ys_tr
        ], dtype=np.float32)
        _log(f'Class weights — category: min={cat_weights.min():.2f} max={cat_weights.max():.2f}')
        _log(f'Class weights — severity: min={sev_weights.min():.2f} max={sev_weights.max():.2f}')

        # Train category
        _log('')
        _log('--- Category Classifier ---')
        n_trees = 500 if len(data) > 10000 else 300
        cm = xgb.XGBClassifier(n_estimators=n_trees, max_depth=7, learning_rate=0.05,
            min_child_weight=2, subsample=0.8, colsample_bytree=0.8,
            objective='multi:softprob', eval_metric='mlogloss', n_jobs=1, random_state=42,
            early_stopping_rounds=30)
        cm.fit(X_tr, yc_tr, sample_weight=cat_weights, eval_set=[(X_te, yc_te)], verbose=False)
        cp = cm.predict(X_te)
        ca = accuracy_score(yc_te, cp)
        _emit_classification_report(_log, yc_te, cp, list(cat_enc.classes_))
        _log(f'Category accuracy: {ca:.1%}', 'success' if ca >= 0.75 else 'warn')

        # v5.87.0: Calibrate confidence scores so predict_proba outputs genuine
        # probabilities. Raw XGBoost softmax is overconfident — it can output
        # 0.95 on predictions that are right only 70% of the time. Isotonic
        # regression on a holdout set fixes this.
        #
        # We calibrate on the test set (cv='prefit'), which uses the held-out
        # data to map raw probas to empirical accuracy. Inference code then
        # wraps the calibrated model transparently.
        _log('  Calibrating probability outputs (isotonic regression)...')
        from sklearn.calibration import CalibratedClassifierCV
        cm_calibrated = CalibratedClassifierCV(cm, method='isotonic', cv='prefit')
        cm_calibrated.fit(X_te, yc_te)
        # Sanity-check: calibration shouldn't change top-1 accuracy much
        cp_cal = cm_calibrated.predict(X_te)
        ca_cal = accuracy_score(yc_te, cp_cal)
        _log(f'  Post-calibration accuracy: {ca_cal:.1%} (was {ca:.1%})')

        # Measure calibration quality: bucket predictions by confidence,
        # compare bucket accuracy to bucket mean confidence. Well-calibrated
        # model has |accuracy - confidence| small in each bucket.
        cat_probas_cal = cm_calibrated.predict_proba(X_te)
        cat_max_confs = cat_probas_cal.max(axis=1)
        for low, high in [(0.85, 1.01), (0.70, 0.85), (0.50, 0.70), (0.0, 0.50)]:
            mask = (cat_max_confs >= low) & (cat_max_confs < high)
            if mask.sum() > 0:
                bucket_acc = accuracy_score(yc_te[mask], cp_cal[mask])
                _log(f'    Conf {low:.2f}-{high:.2f}: n={mask.sum()} bucket_acc={bucket_acc:.1%} mean_conf={cat_max_confs[mask].mean():.2f}')

        cm.save_model(os.path.join(models_dir, 'finding_category.xgb'))
        pickle.dump(cat_enc, open(os.path.join(models_dir, 'category_encoder.pkl'), 'wb'))
        pickle.dump(cm_calibrated, open(os.path.join(models_dir, 'category_calibrator.pkl'), 'wb'))
        _log('  >> Saved finding_category.xgb + category_calibrator.pkl')
        cm = None
        cp = None
        cm_calibrated = None
        yc_tr = None
        yc_te = None
        gc.collect()

        # Train severity
        _log('')
        _log('--- Severity Classifier ---')
        sm = xgb.XGBClassifier(n_estimators=n_trees, max_depth=7, learning_rate=0.05,
            min_child_weight=2, subsample=0.8, colsample_bytree=0.8,
            objective='multi:softprob', eval_metric='mlogloss', n_jobs=1, random_state=42,
            early_stopping_rounds=30)
        sm.fit(X_tr, ys_tr, sample_weight=sev_weights, eval_set=[(X_te, ys_te)], verbose=False)
        sp = sm.predict(X_te)
        sa = accuracy_score(ys_te, sp)
        _emit_classification_report(_log, ys_te, sp, list(sev_enc.classes_))
        _log(f'Severity accuracy: {sa:.1%}', 'success' if sa >= 0.75 else 'warn')

        # v5.87.0: Same calibration treatment for severity.
        _log('  Calibrating probability outputs (isotonic regression)...')
        sm_calibrated = CalibratedClassifierCV(sm, method='isotonic', cv='prefit')
        sm_calibrated.fit(X_te, ys_te)
        sp_cal = sm_calibrated.predict(X_te)
        sa_cal = accuracy_score(ys_te, sp_cal)
        _log(f'  Post-calibration accuracy: {sa_cal:.1%} (was {sa:.1%})')

        sev_probas_cal = sm_calibrated.predict_proba(X_te)
        sev_max_confs = sev_probas_cal.max(axis=1)
        for low, high in [(0.85, 1.01), (0.70, 0.85), (0.50, 0.70), (0.0, 0.50)]:
            mask = (sev_max_confs >= low) & (sev_max_confs < high)
            if mask.sum() > 0:
                bucket_acc = accuracy_score(ys_te[mask], sp_cal[mask])
                _log(f'    Conf {low:.2f}-{high:.2f}: n={mask.sum()} bucket_acc={bucket_acc:.1%} mean_conf={sev_max_confs[mask].mean():.2f}')

        # Report the key number: what % of predictions clear our 0.85 threshold?
        pct_cat_above_85 = float((cat_max_confs >= 0.85).mean() * 100)
        pct_sev_above_85 = float((sev_max_confs >= 0.85).mean() * 100)
        _log('')
        _log(f'Calibrated confidence ≥ 0.85: category {pct_cat_above_85:.0f}% · severity {pct_sev_above_85:.0f}%')

        sm.save_model(os.path.join(models_dir, 'finding_severity.xgb'))
        pickle.dump(sev_enc, open(os.path.join(models_dir, 'severity_encoder.pkl'), 'wb'))
        pickle.dump(sm_calibrated, open(os.path.join(models_dir, 'severity_calibrator.pkl'), 'wb'))
        _log('  >> Saved finding_severity.xgb + severity_calibrator.pkl')

        _update_results(job_id, {'Finding Classifier': {
            'category': f'{ca_cal:.1%}', 'severity': f'{sa_cal:.1%}',
            'category_confident_pct': f'{pct_cat_above_85:.0f}%',
            'severity_confident_pct': f'{pct_sev_above_85:.0f}%',
            'data_points': len(data), 'augmented': aug_count,
            'calibrated': True,
            'status': 'READY' if ca_cal >= 0.75 and sa_cal >= 0.75 else 'MARGINAL',
        }})

        # v5.87.0: Release all remaining classifier state before subprocess
        # exits. Cost predictor runs in a separate subprocess but the subprocess
        # orchestrator re-imports sklearn/xgboost/sentence-transformers; keeping
        # these references alive longer than needed risks OOM during the handoff.
        sm = None
        sm_calibrated = None
        sp = None
        sp_cal = None
        X_tr = None
        X_te = None
        ys_tr = None
        ys_te = None
        cat_max_confs = None
        sev_max_confs = None
        cat_probas_cal = None
        sev_probas_cal = None
        gc.collect()
    except Exception as e:
        _log(f'Finding Classifier FAILED: {e}\n{traceback.format_exc()[:500]}', 'error')
        _update_results(job_id, {'Finding Classifier': {'status': 'FAILED', 'error': str(e)[:200]}})
        raise


# ── Step 2: Contradiction Detector ────────────────────────────────────────────

def train_contradiction_detector(job_id):
    """Train contradiction detector. ~500MB peak memory (smaller dataset)."""
    import numpy as np
    from sklearn.preprocessing import LabelEncoder
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score
    import xgboost as xgb
    from models import MLContradictionPair

    t_start = time.time()
    _log = _make_logger(job_id, t_start)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    models_dir = os.path.join(base_dir, 'models')

    _log('═══ MODEL 3: CONTRADICTION DETECTOR ═══', 'header')
    try:
        rows = MLContradictionPair.query.all()
        _log(f'Loaded {len(rows)} raw contradiction pairs from database')
        data = []
        boilerplate = ["DISCLAIMER", "NOT hold us responsible", "MOLD DISCLAIMER",
            "not a qualified", "MAINTENANCE: Items marked", "intended to reduce",
            "you agree NOT", "non-discovery of any patent", "limitations of the inspection"]
        bp_removed = 0
        for r in rows:
            finding = (r.inspector_finding or '').strip()
            label = (r.label or '').strip()
            seller = (r.seller_claim or '').strip()
            if not finding or not label or len(finding) < 15:
                continue
            if any(bp.upper() in finding.upper() for bp in boilerplate):
                bp_removed += 1
                continue
            combined = (seller or '(not disclosed)') + ' [SEP] ' + finding
            data.append({'text': combined, 'label': label})

        # Dedup
        seen = set()
        deduped = []
        for d in data:
            if d['text'] not in seen:
                seen.add(d['text'])
                deduped.append(d)
        data = deduped
        if bp_removed:
            _log(f'Removed {bp_removed} boilerplate rows')
        _log(f'After cleaning: {len(data)} unique pairs')
        label_counts = Counter(d['label'] for d in data)
        _log('')
        _emit_distribution(_log, 'Label distribution', label_counts)

        unique_labels = list(set(d['label'] for d in data))
        if len(data) < 20 or len(unique_labels) < 2:
            _update_results(job_id, {'Contradiction Detector': {'status': 'NOT ENOUGH DATA', 'data_points': len(data)}})
            return

        embedder = _get_embedder(_log)
        import gc
        _log(f'Encoding {len(data)} pairs in chunks of 500...')
        texts = [d['text'] for d in data]
        labels = [d['label'] for d in data]
        emb_chunks = []
        for start in range(0, len(texts), 500):
            end = min(start + 500, len(texts))
            chunk = embedder.encode(texts[start:end], batch_size=32, show_progress_bar=False)
            emb_chunks.append(chunk)
            gc.collect()
        import numpy as _np
        emb = _np.vstack(emb_chunks) if len(emb_chunks) > 1 else emb_chunks[0]
        del emb_chunks
        gc.collect()
        c_enc = LabelEncoder().fit(labels)
        y_c = c_enc.transform(labels)

        n_cls = len(unique_labels)
        obj = 'binary:logistic' if n_cls == 2 else 'multi:softprob'
        metric = 'logloss' if n_cls == 2 else 'mlogloss'

        try:
            cX_tr, cX_te, cy_tr, cy_te = train_test_split(emb, y_c, test_size=0.2, random_state=42, stratify=y_c)
        except ValueError:
            cX_tr, cX_te, cy_tr, cy_te = train_test_split(emb, y_c, test_size=0.2, random_state=42)
        _log(f'Split: {len(cX_tr)} train, {len(cX_te)} test')

        _log('')
        _log('--- Contradiction Classifier ---')
        c_model = xgb.XGBClassifier(n_estimators=300, max_depth=6, learning_rate=0.08,
            min_child_weight=2, subsample=0.8, colsample_bytree=0.8,
            objective=obj, eval_metric=metric, n_jobs=1, random_state=42)
        c_model.fit(cX_tr, cy_tr, eval_set=[(cX_te, cy_te)], verbose=False)
        c_pred = c_model.predict(cX_te)
        c_acc = accuracy_score(cy_te, c_pred)
        _emit_classification_report(_log, cy_te, c_pred, list(c_enc.classes_))
        _log(f'Contradiction accuracy: {c_acc:.1%}', 'success' if c_acc >= 0.90 else 'warn')
        c_model.save_model(os.path.join(models_dir, 'contradiction_detector.xgb'))
        pickle.dump(c_enc, open(os.path.join(models_dir, 'contradiction_encoder.pkl'), 'wb'))
        _log('  >> Saved contradiction_detector.xgb + contradiction_encoder.pkl')

        _update_results(job_id, {'Contradiction Detector': {
            'accuracy': f'{c_acc:.1%}', 'data_points': len(data),
            'status': 'READY' if c_acc >= 0.75 else 'MARGINAL',
        }})
    except Exception as e:
        _log(f'Contradiction Detector FAILED: {e}\n{traceback.format_exc()[:500]}', 'error')
        _update_results(job_id, {'Contradiction Detector': {'status': 'FAILED', 'error': str(e)[:200]}})
        raise


# ── Step 3: Cost Predictor ────────────────────────────────────────────────────

def train_cost_predictor(job_id):
    """Train repair cost regressor. ~800MB peak (embedding + feature matrix)."""
    import numpy as np
    import pandas as pd
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import mean_absolute_error, r2_score
    import xgboost as xgb
    from models import Analysis, Property

    t_start = time.time()
    _log = _make_logger(job_id, t_start)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    models_dir = os.path.join(base_dir, 'models')

    _log('═══ MODEL 2: REPAIR COST PREDICTOR ═══', 'header')
    try:
        cost_rows = []
        # Pull from analyses
        for a in Analysis.query.filter(Analysis.status == 'completed', Analysis.result_json.isnot(None)).all():
            try:
                result = _json.loads(a.result_json or '{}')
                if result.get('analysis_depth') == 'address_only':
                    continue
                prop = Property.query.get(a.property_id) if a.property_id else None
                repair_est = result.get('repair_estimate', {})
                if isinstance(repair_est, dict):
                    for item in repair_est.get('breakdown', []):
                        if not isinstance(item, dict):
                            continue
                        desc = (item.get('description') or '').strip()
                        low = float(item.get('low', 0) or 0)
                        high = float(item.get('high', 0) or 0)
                        if desc and len(desc) > 5 and (low > 0 or high > 0):
                            cost_rows.append({
                                'text': desc, 'category': (item.get('system') or '').lower(),
                                'severity': (item.get('severity') or '').lower(),
                                'cost_mid': (low + high) / 2,
                                'zip': prop.address if prop else '', 'price': prop.price if prop else 0,
                            })
            except Exception:
                pass

        # Baseline costs
        try:
            from repair_cost_estimator import BASELINE_COSTS
            for cat, severities in BASELINE_COSTS.items():
                for sev, (low, high) in severities.items():
                    cost_rows.append({
                        'text': f'{cat} repair - {sev} severity (national average)',
                        'category': cat, 'severity': sev,
                        'cost_mid': (low + high) / 2, 'zip': '', 'price': 0,
                    })
        except Exception:
            pass

        # External crawled data
        COST_BOUNDS = {
            'roof_exterior':        (800, 50000),
            'foundation_structure': (1000, 80000),
            'electrical':           (200, 25000),
            'plumbing':             (150, 20000),
            'hvac_systems':         (300, 30000),
            'environmental':        (500, 40000),
            'general':              (500, 30000),
        }
        try:
            from models import MLCostData
            # Filter EXCLUDED:* rows — these were marked by the audit endpoint
            # for known-bad data (e.g., 'electrical panel' priced like a full rewire).
            crawled = MLCostData.query.filter(
                ~MLCostData.source.like('EXCLUDED:%')
            ).all()
            added_crawled = 0
            filtered_crawled = 0
            for c in crawled:
                if not c.cost_mid or c.cost_mid <= 0:
                    continue
                cat = c.category or 'general'
                bounds = COST_BOUNDS.get(cat, (500, 50000))
                if c.cost_mid < bounds[0] or c.cost_mid > bounds[1]:
                    filtered_crawled += 1
                    continue
                cost_rows.append({
                    'text': c.finding_text[:200], 'category': cat,
                    'severity': c.severity or 'moderate', 'cost_mid': c.cost_mid,
                    'zip': c.zip_code or '', 'price': 0,
                })
                added_crawled += 1
            if crawled:
                _log(f'External data: {added_crawled} usable, {filtered_crawled} filtered as outliers (from {len(crawled)} total)')
        except Exception as crawl_err:
            _log(f'Crawled cost data skipped: {crawl_err}', 'warn')

        # Dedup
        seen = set()
        deduped = []
        for d in cost_rows:
            key = d['text'].lower()[:80]
            if key not in seen:
                seen.add(key)
                deduped.append(d)
        cost_rows = deduped
        _log(f'After dedup: {len(cost_rows)} unique cost entries')

        if len(cost_rows) < 10:
            _log(f'Only {len(cost_rows)} cost entries — need 10+', 'warn')
            _update_results(job_id, {'Repair Cost': {'status': 'NOT ENOUGH DATA', 'data_points': len(cost_rows)}})
            return

        # Hard row cap (same rationale as finding_classifier — cgroup-aware
        # memory reporting on Render is unreliable, so use a conservative fixed cap).
        MAX_COST_ROWS = 5000
        if len(cost_rows) > MAX_COST_ROWS:
            _log(f'⚠ Dataset has {len(cost_rows):,} cost entries — capping to {MAX_COST_ROWS:,} (random sample) for 2GB RAM', 'warn')
            import random
            random.seed(42)
            cost_rows = random.sample(cost_rows, MAX_COST_ROWS)

        rdf = pd.DataFrame(cost_rows)
        rdf = rdf[rdf['cost_mid'] > 0]
        _log('')
        _log(f'Cost range: ${rdf["cost_mid"].min():,.0f} — ${rdf["cost_mid"].max():,.0f}')
        _log(f'Mean: ${rdf["cost_mid"].mean():,.0f}, Median: ${rdf["cost_mid"].median():,.0f}')

        # Per-category breakdown — matches train.sh detail level
        try:
            _log('')
            _log('By category:')
            for cat, grp in sorted(rdf.groupby('category'), key=lambda x: -len(x[1])):
                n = len(grp)
                avg = grp['cost_mid'].mean()
                lo = grp['cost_mid'].min()
                hi = grp['cost_mid'].max()
                _log(f'  {cat:30s} n={n:3d}  avg=${avg:,.0f}  range=${lo:,.0f}-${hi:,.0f}')
        except Exception as e:
            _log(f'  (per-category breakdown failed: {e})', 'warn')

        # Per-severity breakdown
        try:
            _log('')
            _log('By severity:')
            for sev, grp in sorted(rdf.groupby('severity'), key=lambda x: -len(x[1])):
                n = len(grp)
                avg = grp['cost_mid'].mean()
                _log(f'  {sev:30s} n={n:3d}  avg=${avg:,.0f}')
        except Exception as e:
            _log(f'  (per-severity breakdown failed: {e})', 'warn')

        # Memory logger
        def _log_mem(tag):
            try:
                import psutil
                mb = psutil.Process().memory_info().rss / (1024 * 1024)
                _log(f'  [mem:{tag}] {mb:.0f}MB RSS')
            except Exception:
                pass
        _log_mem('pre-encode')

        import gc
        embedder = _get_embedder(_log)
        # Smaller chunks to reduce transient torch activation memory during encoding.
        # Same rationale as finding_classifier — see v5.86.55 notes above.
        chunk_size = 500
        encode_batch = 32
        cost_texts = rdf['text'].tolist()
        n_cost = len(cost_texts)
        _log(f'Encoding {n_cost} cost entries in chunks of {chunk_size} (batch={encode_batch})...')
        cost_emb_chunks = []
        for start in range(0, n_cost, chunk_size):
            end = min(start + chunk_size, n_cost)
            chunk = embedder.encode(cost_texts[start:end], batch_size=encode_batch, show_progress_bar=False)
            cost_emb_chunks.append(chunk)
            if n_cost > chunk_size and (end % 2000 == 0 or end == n_cost):
                _log(f'  Encoded {end}/{n_cost} ({end*100//n_cost}%)')
                _log_mem('encoding')
            gc.collect()
        emb = np.vstack(cost_emb_chunks)
        del cost_emb_chunks
        gc.collect()
        _log_mem('post-encode')

        cat_dummies = pd.get_dummies(rdf['category'], prefix='cat')
        sev_dummies = pd.get_dummies(rdf['severity'], prefix='sev')
        # Preserve column names BEFORE the delete — used in feature_meta below
        cat_cols = list(cat_dummies.columns)
        sev_cols = list(sev_dummies.columns)
        import re
        rdf['zip_num'] = rdf['zip'].apply(
            lambda z: float(re.search(r'\b(\d{5})\b', str(z)).group(1)) / 100000 if re.search(r'\b(\d{5})\b', str(z)) else 0)
        rdf['price_norm'] = rdf['price'].fillna(0) / 1_000_000

        structured = np.hstack([cat_dummies.values, sev_dummies.values, rdf[['zip_num', 'price_norm']].values]).astype(np.float32)
        X_all = np.hstack([emb.astype(np.float32, copy=False), structured])
        # v5.86.96: Prefer reassignment-to-None over `del` — `del` makes the
        # variable genuinely unbound, so any downstream reference raises
        # UnboundLocalError with the confusing message "cannot access local
        # variable 'X' where it is not associated with a value". That error
        # was observed in production (07:09 AM 2026-04-23) with cat_dummies.
        # Setting to None achieves the same memory release (refcount → 0,
        # immediate GC for large numpy/pandas objects) without the footgun.
        emb = None
        structured = None
        cat_dummies = None
        sev_dummies = None
        import gc
        gc.collect()
        emb_dim = 384
        struct_dim = X_all.shape[1] - emb_dim
        _log(f'Feature matrix: {X_all.shape} ({emb_dim} embedding + {struct_dim} structured)')
        y_cost = np.log1p(rdf['cost_mid'].values).astype(np.float32)

        X_tr, X_te, y_tr, y_te = train_test_split(X_all, y_cost, test_size=0.2, random_state=42)
        del X_all, y_cost
        gc.collect()
        _log(f'Split: train={len(X_tr)}, test={len(X_te)}')

        _log('')
        _log('--- Cost Predictor (quantile regression, log-scale) ---')
        # v5.87.0: Train THREE quantile regressors (p10, p50, p90) instead of
        # a single point estimate. This gives us:
        #   - p50 as the primary prediction (same role as the old single model)
        #   - p10 and p90 as the low/high bounds of a real 80% prediction interval
        #   - Confidence = narrowness of band relative to median. Narrow band
        #     (p10 close to p90) = model is confident; wide band = uncertain.
        #
        # Motivation: enables the "ML used only when confidence ≥ 0.85"
        # architectural guarantee across all models. Previously cost had no
        # confidence signal and always fired. Now cost gates on a real
        # distributional uncertainty measure.
        #
        # Memory discipline: train three models sequentially with explicit
        # reference-drop between them so peak RSS stays under 1.2GB on Render.
        n_cost_trees = 500 if len(rdf) > 5000 else 300

        def _train_quantile_model(alpha: float, label: str):
            """Train one quantile regressor at alpha ∈ (0, 1)."""
            m = xgb.XGBRegressor(
                n_estimators=n_cost_trees, max_depth=7, learning_rate=0.05,
                min_child_weight=3, subsample=0.8, colsample_bytree=0.8,
                objective='reg:quantileerror', quantile_alpha=alpha,
                n_jobs=1, random_state=42,
            )
            m.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=False)
            _log(f'  ✓ Trained {label} (α={alpha:.2f})')
            _log_mem(f'post-fit {label}')
            return m

        _log(f'Training 3 quantile models (n_estimators={n_cost_trees}, depth=7)...')
        cost_model_p10 = _train_quantile_model(0.10, 'p10 (low)')
        gc.collect()
        cost_model_p50 = _train_quantile_model(0.50, 'p50 (median)')
        gc.collect()
        cost_model_p90 = _train_quantile_model(0.90, 'p90 (high)')
        gc.collect()

        # Evaluate on test set — use p50 as the primary point estimate
        y_pred_p50 = np.expm1(cost_model_p50.predict(X_te))
        y_pred_p10 = np.expm1(cost_model_p10.predict(X_te))
        y_pred_p90 = np.expm1(cost_model_p90.predict(X_te))
        y_actual = np.expm1(y_te)

        # Capture training set length for the sample-predictions log below,
        # then release the training arrays since they're no longer needed.
        n_train = len(X_tr)
        X_tr = None
        X_te = None
        y_tr = None
        y_te = None
        gc.collect()

        mae = mean_absolute_error(y_actual, y_pred_p50)
        r2 = r2_score(y_actual, y_pred_p50)
        pct_err = float(np.median(np.abs(y_actual - y_pred_p50) / np.maximum(y_actual, 1) * 100))

        # Interval calibration: what % of actuals fall within [p10, p90]?
        # If the model is well-calibrated, this should be ~80%.
        in_interval = float(np.mean((y_actual >= y_pred_p10) & (y_actual <= y_pred_p90)) * 100)

        # Confidence distribution: how tight are our bands?
        # relative_width = (p90 - p10) / p50. Smaller = more confident.
        # We convert to a 0-1 confidence score via exp decay.
        rel_width = (y_pred_p90 - y_pred_p10) / np.maximum(y_pred_p50, 1)
        # Confidence mapping: narrow band (rel_width ~0.3) → conf ≈ 0.93
        # Reasonable band (rel_width ~0.7) → conf ≈ 0.85 (our threshold)
        # Wide band (rel_width > 2.0) → conf < 0.60
        # Using exp(-rel_width/4.0) as the decay curve.
        conf_scores = np.exp(-rel_width / 4.0).clip(0.0, 1.0)
        pct_above_85 = float(np.mean(conf_scores >= 0.85) * 100)
        mean_conf = float(np.mean(conf_scores))

        _log('')
        _log(f'  Point estimate (p50):')
        _log(f'    Mean Absolute Error:    ${mae:,.0f}')
        _log(f'    Median % Error:         {pct_err:.0f}%')
        _log(f'    R-squared:              {r2:.3f}', 'success' if r2 >= 0.5 else 'warn')
        _log(f'  Interval calibration:')
        _log(f'    Actuals in [p10, p90]:  {in_interval:.1f}%  (target ~80%)')
        _log(f'    Mean confidence:        {mean_conf:.2f}')
        _log(f'    % at confidence ≥ 0.85: {pct_above_85:.1f}%',
             'success' if pct_above_85 >= 25 else 'warn')

        _log('')
        _log('  Sample predictions (actual · p10 | p50 | p90 · conf):')
        for i in range(min(8, len(y_actual))):
            try:
                desc = rdf.iloc[n_train+i]["text"][:40]
            except Exception:
                desc = '?'
            _log(f'    ${y_actual[i]:>7,.0f} · ${y_pred_p10[i]:>7,.0f}|${y_pred_p50[i]:>7,.0f}|${y_pred_p90[i]:>7,.0f} · {conf_scores[i]:.2f}  ({desc}...)')

        # Save all three models + legacy single-file filename for backward compat
        # (inference code in ml_inference.py must load all three or gracefully
        # degrade to p50-only when p10/p90 are missing from disk)
        cost_model_p10.save_model(os.path.join(models_dir, 'repair_cost_p10.xgb'))
        cost_model_p50.save_model(os.path.join(models_dir, 'repair_cost_p50.xgb'))
        cost_model_p90.save_model(os.path.join(models_dir, 'repair_cost_p90.xgb'))
        # Legacy path for existing inference code that loads repair_cost.xgb
        cost_model_p50.save_model(os.path.join(models_dir, 'repair_cost.xgb'))

        feature_meta = {
            'category_columns': cat_cols, 'severity_columns': sev_cols,
            'embedding_dim': 384, 'uses_log_transform': True,
            # v5.87.0: indicates this training run produced quantile models
            'quantile_version': 'v1',
            'quantile_alphas': [0.10, 0.50, 0.90],
            'confidence_decay_factor': 4.0,  # used by inference to reconstruct conf
        }
        pickle.dump(feature_meta, open(os.path.join(models_dir, 'cost_feature_meta.pkl'), 'wb'))
        _log('  >> Saved repair_cost_{p10,p50,p90}.xgb + cost_feature_meta.pkl')

        _update_results(job_id, {'Repair Cost': {
            'mae': f'${mae:,.0f}', 'median_pct_err': f'{pct_err:.0f}%',
            'r2': f'{r2:.3f}', 'data_points': len(cost_rows),
            'interval_calibration_pct': f'{in_interval:.0f}%',
            'mean_confidence': f'{mean_conf:.2f}',
            'pct_confident': f'{pct_above_85:.0f}%',
            'status': 'READY' if r2 >= 0.5 and pct_err <= 40 else 'MARGINAL',
        }})
    except Exception as e:
        tb = traceback.format_exc()
        _log(f'Repair Cost FAILED: {e}\n{tb[:1000]}', 'error')
        # v5.86.96: include last traceback frame in the error field so the UI
        # can show which line failed. Previously we showed only str(e), which
        # for UnboundLocalError is cryptic without the stack location.
        tb_lines = [ln.strip() for ln in tb.strip().splitlines()]
        last_frame = next((ln for ln in reversed(tb_lines) if 'File "' in ln and 'ml_training_pipeline' in ln), '')
        error_with_location = f'{str(e)[:160]} | {last_frame[:160]}' if last_frame else str(e)[:200]
        _update_results(job_id, {'Repair Cost': {'status': 'FAILED', 'error': error_with_location}})
        raise


# ── Step 4: Post-training (hot-reload + smoke tests + dashboard) ──────────────

def run_post_training(job_id):
    """Reload models into inference, run smoke tests, write dashboard, save history."""
    t_start = time.time()
    _log = _make_logger(job_id, t_start)
    base_dir = os.path.dirname(os.path.abspath(__file__))

    from admin_routes import _load_job_state, _save_job_state
    state = _load_job_state(job_id) or {}
    results = state.get('results') or {}
    started_at = state.get('started_at', t_start)

    _log('═══ RELOADING MODELS ═══', 'header')
    try:
        from ml_inference import init_ml_inference
        init_ml_inference(app_base_dir=base_dir)
        results['_reload'] = 'success'
        _log('Models hot-reloaded into inference pipeline', 'success')
    except Exception as e:
        results['_reload'] = f'failed: {e}'
        _log(f'Model reload failed: {e}', 'error')

    # Inference smoke tests
    _log('═══ INFERENCE SMOKE TESTS ═══', 'header')
    inference_results = {'passed': 0, 'failed': 0, 'skipped': 0, 'details': [], 'total': 0}
    try:
        from ml_inference_tests import run_inference_tests
        inference_results = run_inference_tests()
        for t in inference_results.get('details', []):
            icon = '✓' if t['status'] == 'PASS' else '✗' if t['status'] == 'FAIL' else '○'
            level = 'success' if t['status'] == 'PASS' else 'error' if t['status'] == 'FAIL' else 'info'
            model_short = t.get('model', '?').replace('FindingClassifier', 'FC').replace('ContradictionDetector', 'CD').replace('CostPredictor', 'Cost')
            if t['status'] == 'FAIL':
                if t.get('got_cat'):
                    cat_mark = '✓' if t.get('cat_ok') else '✗'
                    sev_mark = '✓' if t.get('sev_ok') else '✗'
                    conf_cat = f" ({t.get('cat_conf', 0):.0%})" if t.get('cat_conf') else ''
                    conf_sev = f" ({t.get('sev_conf', 0):.0%})" if t.get('sev_conf') else ''
                    _log(f'{icon} {model_short}: {t.get("input","")[:50]}', level)
                    _log(f'    {cat_mark} Category: expected={t.get("expected_cat")}  got={t["got_cat"]}{conf_cat}', level)
                    _log(f'    {sev_mark} Severity: expected={t.get("expected_sev")}  got={t["got_sev"]}{conf_sev}', level)
                elif t.get('expected_range'):
                    _log(f'{icon} {model_short}: {t.get("input","")[:50]}', level)
                    _log(f'    Expected: {t["expected_range"]}  Got: {t.get("got","?")}', level)
                elif t.get('got'):
                    _log(f'{icon} {model_short}: {t.get("input","")[:50]}', level)
                    _log(f'    Expected: {t.get("expected","?")}  Got: {t["got"]}  Conf: {t.get("confidence",0):.0%}', level)
                else:
                    _log(f'{icon} {model_short}: {t.get("input","")[:50]} — {t.get("reason","")}', level)
            else:
                detail = ''
                if t.get('got_cat'):
                    detail = f" → {t['got_cat']}/{t['got_sev']}"
                elif t.get('got'):
                    detail = f" → {t['got']}"
                elif t.get('status') == 'SKIP':
                    detail = f" — {t.get('reason','skipped')}"
                _log(f'{icon} {model_short}: {t.get("input","")[:50]}{detail}', level)
        _log(f'Results: {inference_results["passed"]} passed, {inference_results["failed"]} failed, {inference_results["skipped"]} skipped',
             'success' if inference_results['failed'] == 0 else 'warn')
        results['_inference_tests'] = {
            'passed': inference_results['passed'], 'failed': inference_results['failed'],
            'skipped': inference_results['skipped'],
            'total': inference_results.get('total', 0),
            'details': inference_results['details'],
        }
    except Exception as e:
        results['_inference_tests'] = {'error': str(e)[:200]}

    # Elapsed total time (since the web worker wrote started_at)
    elapsed = time.time() - started_at
    results['_elapsed'] = f'{elapsed:.1f}s'

    # Accuracy dashboard
    _log('', 'info')
    _log('═══ ACCURACY DASHBOARD ═══', 'header')
    fc = results.get('Finding Classifier', {})
    cd = results.get('Contradiction Detector', {})
    rc = results.get('Repair Cost', {})

    def _dash_line(model, metric, current, target, unit='%'):
        try:
            val = float(str(current).replace('%', '').replace('$', '').replace(',', ''))
            status = '✅' if val >= target else '❌'
        except Exception:
            status = '❌'
        _log(f'  {status} {model:25s} {metric:12s} {str(current):>8s} / {target}{unit}')

    if fc.get('category'):
        _dash_line('Finding Classifier', 'Category', fc['category'], 90)
        _dash_line('Finding Classifier', 'Severity', fc.get('severity', '?'), 85)
    if cd.get('accuracy'):
        _dash_line('Contradiction Detector', 'Accuracy', cd['accuracy'], 99)
    if rc.get('r2'):
        try:
            r2_pct = f"{float(rc['r2'])*100:.1f}%"
        except Exception:
            r2_pct = rc['r2']
        _dash_line('Repair Cost Predictor', 'R-squared', r2_pct, 85)
        _dash_line('Repair Cost Predictor', 'MAE', rc.get('mae', '?'), 1000, '')
        _dash_line('Repair Cost Predictor', 'Median err', rc.get('median_pct_err', '?'), 10)

    tests_passed = inference_results.get('passed', 0)
    tests_total = tests_passed + inference_results.get('failed', 0) + inference_results.get('skipped', 0)
    _log(f'  Inference tests: {tests_passed}/{tests_total} passed')
    _log(f'  Total time: {elapsed:.1f}s')
    _log('')

    # Save training history (best-effort)
    try:
        from models import db, MLTrainingRun
        run = MLTrainingRun(
            trigger='manual',
            elapsed_seconds=elapsed,
            fc_status=fc.get('status'),
            fc_category_acc=_parse_pct(fc.get('category')),
            fc_severity_acc=_parse_pct(fc.get('severity')),
            fc_data_points=fc.get('data_points'),
            fc_augmented=fc.get('augmented', 0),
            fc_error=(fc.get('error') or None) if fc.get('status') == 'FAILED' else None,
            cd_status=cd.get('status'),
            cd_accuracy=_parse_pct(cd.get('accuracy')),
            cd_data_points=cd.get('data_points'),
            cd_error=(cd.get('error') or None) if cd.get('status') == 'FAILED' else None,
            rc_status=rc.get('status'),
            rc_r2=_parse_float(rc.get('r2')),
            rc_mae=_parse_dollars(rc.get('mae')),
            rc_median_pct=_parse_pct(rc.get('median_pct_err')),
            rc_data_points=rc.get('data_points'),
            rc_error=(rc.get('error') or None) if rc.get('status') == 'FAILED' else None,
            inference_tested=True,
            inference_passed=inference_results.get('passed', 0),
            inference_failed=inference_results.get('failed', 0),
            inference_details=_json.dumps(inference_results.get('details', [])),
        )
        db.session.add(run)
        db.session.commit()
        results['_history_id'] = run.id
    except Exception as e:
        import logging
        logging.warning(f"ML training history save failed: {e}")
        try:
            from models import db as _db
            _db.session.rollback()
        except Exception:
            pass

    _log(f'Training complete in {elapsed:.1f}s', 'success')

    # Write the final 'log' into results too (frontend expects d._log)
    results['_log'] = (state.get('log') or [])
    state['results'] = results
    state['status'] = 'complete'
    state['elapsed_total'] = elapsed
    _save_job_state(job_id, state)


def _parse_pct(s):
    """Parse a percentage string like '90.4%' or '90.4' into a display-format float.
    Returns 90.4 (not 0.904) — the frontend adds the '%' suffix on render, so we
    preserve the display-format number in the DB. Matches the convention used by
    the original admin_routes.py training code.
    """
    try:
        return float(str(s).replace('%', ''))
    except Exception:
        return None


def _parse_float(s):
    try:
        return float(s)
    except Exception:
        return None


def _parse_dollars(s):
    try:
        return float(str(s).replace('$', '').replace(',', ''))
    except Exception:
        return None


# ── Entrypoint ────────────────────────────────────────────────────────────────

STEP_DISPATCH = {
    'finding_classifier': train_finding_classifier,
    'contradiction_detector': train_contradiction_detector,
    'cost_predictor': train_cost_predictor,
    'post_training': run_post_training,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('step', choices=list(STEP_DISPATCH.keys()))
    parser.add_argument('--job-id', required=True)
    args = parser.parse_args()

    # Tell app.py to skip APScheduler startup in this subprocess
    os.environ['OFFERWISE_TRAINING_SUBPROCESS'] = '1'

    app_dir = os.path.dirname(os.path.abspath(__file__))
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

    from app import app

    try:
        with app.app_context():
            STEP_DISPATCH[args.step](args.job_id)
        sys.exit(0)
    except Exception as e:
        print(f'[pipeline:{args.step}] FAILED for {args.job_id}: {e}\n{traceback.format_exc()}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
