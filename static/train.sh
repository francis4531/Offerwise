#!/bin/bash
# OfferWise ML Training Pipeline — All Models

echo ""
echo "════════════════════════════════════════════════════"
echo "  OfferWise ML Training Pipeline"
echo "════════════════════════════════════════════════════"
echo ""

ML_DIR="$HOME/offerwise-ml"
DATA_DIR="$ML_DIR/data"
MODELS_DIR="$ML_DIR/models"
DOWNLOADS="$HOME/Downloads"
PYTHON="$ML_DIR/venv/bin/python"

mkdir -p "$DATA_DIR" "$MODELS_DIR"

echo "📥 Step 1: Finding training data..."

AUG_FILE=$(ls -t "$DOWNLOADS"/offerwise_finding_labels_augmented*.csv 2>/dev/null | head -1)
RAW_FILE=$(ls -t "$DOWNLOADS"/offerwise_finding_labels*.csv 2>/dev/null | grep -v augmented | head -1)
if [ -n "$AUG_FILE" ]; then
  cp "$AUG_FILE" "$DATA_DIR/finding_labels.csv"
  echo "   ✅ Findings: $(basename "$AUG_FILE")"
elif [ -n "$RAW_FILE" ]; then
  cp "$RAW_FILE" "$DATA_DIR/finding_labels.csv"
  echo "   ✅ Findings: $(basename "$RAW_FILE")"
fi

CONTRA_FILE=$(ls -t "$DOWNLOADS"/offerwise_contradiction_pairs*.csv 2>/dev/null | head -1)
if [ -n "$CONTRA_FILE" ]; then
  cp "$CONTRA_FILE" "$DATA_DIR/contradiction_pairs.csv"
  echo "   ✅ Contradictions: $(basename "$CONTRA_FILE")"
fi

COST_FILE=$(ls -t "$DOWNLOADS"/offerwise_repair_costs*.csv 2>/dev/null | head -1)
if [ -n "$COST_FILE" ]; then
  cp "$COST_FILE" "$DATA_DIR/repair_costs.csv"
  echo "   ✅ Repair costs: $(basename "$COST_FILE")"
fi

if [ ! -f "$DATA_DIR/finding_labels.csv" ]; then
  echo "   ❌ No finding_labels.csv found."
  echo "Press Enter to exit..."; read; exit 1
fi

for f in finding_labels.csv contradiction_pairs.csv repair_costs.csv; do
  if [ -f "$DATA_DIR/$f" ]; then
    echo "   $f: $(wc -l < "$DATA_DIR/$f") lines"
  fi
done
echo ""

echo "🐍 Step 2: Checking Python..."
if [ ! -f "$PYTHON" ]; then
  echo "   ❌ No venv at $PYTHON"; echo "Press Enter to exit..."; read; exit 1
fi
echo "   ✅ $PYTHON"
echo ""

echo "════════════════════════════════════════════════════"
echo "  🧠 Step 3: Training all models..."
echo "════════════════════════════════════════════════════"
echo ""

"$PYTHON" -u << 'PYEOF'
import pandas as pd, numpy as np, pickle, os, sys, traceback, warnings
warnings.filterwarnings("ignore", message=".*use_label_encoder.*")
warnings.filterwarnings("ignore", message=".*UBJSON.*")
warnings.filterwarnings("ignore", message=".*Saving model.*")

DATA = os.path.expanduser("~/offerwise-ml/data")
MODELS = os.path.expanduser("~/offerwise-ml/models")
MODEL_CACHE = os.path.expanduser("~/offerwise-ml/model-cache/sentence-transformers/all-MiniLM-L6-v2")
os.makedirs(MODELS, exist_ok=True)
SEP = "=" * 60

from sentence_transformers import SentenceTransformer
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score, mean_absolute_error, r2_score
import xgboost as xgb

print("Loading sentence-transformers from local cache...")
embedder = SentenceTransformer(MODEL_CACHE)
print("Ready.\n")

results = {}

# ═══════════════════════════════════════════════════════════
# MODEL 1: FINDING CLASSIFIER
# ═══════════════════════════════════════════════════════════
try:
    print(SEP)
    print("  MODEL 1: FINDING CLASSIFIER")
    print(SEP)

    df = pd.read_csv(os.path.join(DATA, "finding_labels.csv"))
    print(f"\nLoaded {len(df)} rows")

    n_syn = len(df[df["source"] == "synthetic"]) if "source" in df.columns else 0
    if n_syn > 0:
        print(f"  Real: {len(df) - n_syn}, Synthetic: {n_syn}")

    df = df[df["category"].notna() & df["severity"].notna() & df["finding_text"].notna()]
    df["category"] = df["category"].str.lower().str.strip()
    df["severity"] = df["severity"].str.lower().str.strip()
    df = df[df["severity"].isin(["critical","major","moderate","minor"])]
    df = df[df["finding_text"].str.len() > 10]
    # Normalize category variants
    cat_map = {"foundation": "foundation_structure", "exterior": "roof_exterior",
               "foundation & structure": "foundation_structure", "roof & exterior": "roof_exterior",
               "hvac & systems": "hvac_systems", "hvac": "hvac_systems",
               "roof": "roof_exterior", "legal & title": "general",
               "water_damage": "environmental", "pest": "environmental",
               "safety": "electrical", "permits": "general"}
    df["category"] = df["category"].map(lambda c: cat_map.get(c, c))
    df = df.drop_duplicates(subset="finding_text", keep="first")
    print(f"After cleaning: {len(df)} unique findings")

    print("\nCategory distribution:")
    for cat, cnt in df["category"].value_counts().items():
        bar = "#" * max(1, int(cnt / len(df) * 40))
        print(f"   {cat:30s} {cnt:4d}  {bar}")

    print("\nSeverity distribution:")
    for sev, cnt in df["severity"].value_counts().items():
        bar = "#" * max(1, int(cnt / len(df) * 40))
        print(f"   {sev:30s} {cnt:4d}  {bar}")

    print(f"\nEncoding {len(df)} findings...")
    emb = embedder.encode(df["finding_text"].tolist(), show_progress_bar=True, batch_size=64)

    cat_enc = LabelEncoder().fit(df["category"])
    sev_enc = LabelEncoder().fit(df["severity"])
    y_cat = cat_enc.transform(df["category"])
    y_sev = sev_enc.transform(df["severity"])

    try:
        X_tr, X_te, yc_tr, yc_te, ys_tr, ys_te = train_test_split(
            emb, y_cat, y_sev, test_size=0.2, random_state=42, stratify=y_cat)
    except ValueError:
        X_tr, X_te, yc_tr, yc_te, ys_tr, ys_te = train_test_split(
            emb, y_cat, y_sev, test_size=0.2, random_state=42)
    print(f"Split: train={len(X_tr)}, test={len(X_te)}")

    print("\n--- Category Classifier ---")
    cm = xgb.XGBClassifier(n_estimators=300, max_depth=6, learning_rate=0.08,
        min_child_weight=2, subsample=0.8, colsample_bytree=0.8,
        objective="multi:softprob", eval_metric="mlogloss",
        n_jobs=-1, random_state=42)
    cm.fit(X_tr, yc_tr, eval_set=[(X_te, yc_te)], verbose=False)
    cp = cm.predict(X_te)
    ca = accuracy_score(yc_te, cp)
    print(classification_report(yc_te, cp, target_names=cat_enc.classes_, zero_division=0))
    print(f"Category accuracy: {ca:.1%}")

    print("\n--- Severity Classifier ---")
    sm = xgb.XGBClassifier(n_estimators=300, max_depth=6, learning_rate=0.08,
        min_child_weight=2, subsample=0.8, colsample_bytree=0.8,
        objective="multi:softprob", eval_metric="mlogloss",
        n_jobs=-1, random_state=42)
    sm.fit(X_tr, ys_tr, eval_set=[(X_te, ys_te)], verbose=False)
    sp = sm.predict(X_te)
    sa = accuracy_score(ys_te, sp)
    print(classification_report(ys_te, sp, target_names=sev_enc.classes_, zero_division=0))
    print(f"Severity accuracy: {sa:.1%}")

    cm.save_model(os.path.join(MODELS, "finding_category.xgb"))
    sm.save_model(os.path.join(MODELS, "finding_severity.xgb"))
    pickle.dump(cat_enc, open(os.path.join(MODELS, "category_encoder.pkl"), "wb"))
    pickle.dump(sev_enc, open(os.path.join(MODELS, "severity_encoder.pkl"), "wb"))

    results["Finding Classifier"] = {"category": f"{ca:.1%}", "severity": f"{sa:.1%}",
        "status": "READY" if ca >= 0.75 and sa >= 0.75 else "MARGINAL"}
    print(f"\n  >> Saved finding_category.xgb + finding_severity.xgb")

except Exception as e:
    print(f"\nFinding Classifier ERROR: {type(e).__name__}: {e}")
    traceback.print_exc()
    results["Finding Classifier"] = {"status": "FAILED", "error": str(e)}

# ═══════════════════════════════════════════════════════════
# MODEL 3: CONTRADICTION DETECTOR
# ═══════════════════════════════════════════════════════════
contra_path = os.path.join(DATA, "contradiction_pairs.csv")
if os.path.exists(contra_path):
    try:
        print(f"\n\n{SEP}")
        print("  MODEL 3: CONTRADICTION DETECTOR")
        print(SEP)

        cdf = pd.read_csv(contra_path)
        print(f"\nLoaded {len(cdf)} rows")

        cdf = cdf[cdf["inspector_finding"].notna() & cdf["label"].notna()]
        cdf["seller_claim"] = cdf["seller_claim"].fillna("")

        boilerplate = ["DISCLAIMER", "NOT hold us responsible", "MOLD DISCLAIMER",
            "not a qualified", "MAINTENANCE: Items marked", "intended to reduce",
            "you agree NOT", "non-discovery of any patent", "limitations of the inspection"]
        def is_boilerplate(text):
            t = str(text).upper()
            return any(bp.upper() in t for bp in boilerplate)

        before = len(cdf)
        cdf = cdf[~cdf["inspector_finding"].apply(is_boilerplate)]
        removed = before - len(cdf)
        if removed > 0:
            print(f"Removed {removed} boilerplate rows")

        cdf = cdf[cdf["inspector_finding"].str.len() > 15]
        cdf["combined_text"] = cdf["seller_claim"].fillna("(not disclosed)") + " [SEP] " + cdf["inspector_finding"]
        cdf = cdf.drop_duplicates(subset="combined_text", keep="first")
        print(f"After cleaning: {len(cdf)} unique pairs")

        if len(cdf) < 20:
            results["Contradiction Detector"] = {"status": "NOT ENOUGH DATA"}
        else:
            print("\nLabel distribution:")
            for lab, cnt in cdf["label"].value_counts().items():
                bar = "#" * max(1, int(cnt / len(cdf) * 40))
                print(f"   {lab:30s} {cnt:4d}  {bar}")

            unique_labels = cdf["label"].unique()
            if len(unique_labels) < 2:
                results["Contradiction Detector"] = {"status": "NEED MORE LABEL DIVERSITY"}
            else:
                print(f"\nEncoding {len(cdf)} pairs...")
                c_emb = embedder.encode(cdf["combined_text"].tolist(), show_progress_bar=True, batch_size=64)
                c_enc = LabelEncoder().fit(cdf["label"])
                y_c = c_enc.transform(cdf["label"])
                n_classes = len(unique_labels)
                obj = "binary:logistic" if n_classes == 2 else "multi:softprob"
                metric = "logloss" if n_classes == 2 else "mlogloss"

                try:
                    cX_tr, cX_te, cy_tr, cy_te = train_test_split(
                        c_emb, y_c, test_size=0.2, random_state=42, stratify=y_c)
                except ValueError:
                    cX_tr, cX_te, cy_tr, cy_te = train_test_split(
                        c_emb, y_c, test_size=0.2, random_state=42)
                print(f"Split: train={len(cX_tr)}, test={len(cX_te)}")

                print("\n--- Contradiction Classifier ---")
                c_model = xgb.XGBClassifier(n_estimators=300, max_depth=6, learning_rate=0.08,
                    min_child_weight=2, subsample=0.8, colsample_bytree=0.8,
                    objective=obj, eval_metric=metric,
                    n_jobs=-1, random_state=42)
                c_model.fit(cX_tr, cy_tr, eval_set=[(cX_te, cy_te)], verbose=False)
                c_pred = c_model.predict(cX_te)
                c_acc = accuracy_score(cy_te, c_pred)
                print(classification_report(cy_te, c_pred, target_names=c_enc.classes_, zero_division=0))
                print(f"Contradiction accuracy: {c_acc:.1%}")

                c_model.save_model(os.path.join(MODELS, "contradiction_detector.xgb"))
                pickle.dump(c_enc, open(os.path.join(MODELS, "contradiction_encoder.pkl"), "wb"))
                results["Contradiction Detector"] = {"accuracy": f"{c_acc:.1%}",
                    "status": "READY" if c_acc >= 0.75 else "MARGINAL"}
                print(f"\n  >> Saved contradiction_detector.xgb + contradiction_encoder.pkl")

    except Exception as e:
        print(f"\nContradiction Detector ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        results["Contradiction Detector"] = {"status": "FAILED", "error": str(e)}
else:
    results["Contradiction Detector"] = {"status": "SKIPPED"}

# ═══════════════════════════════════════════════════════════
# MODEL 2: REPAIR COST PREDICTOR
# ═══════════════════════════════════════════════════════════
cost_path = os.path.join(DATA, "repair_costs.csv")
if os.path.exists(cost_path):
    try:
        print(f"\n\n{SEP}")
        print("  MODEL 2: REPAIR COST PREDICTOR")
        print(SEP)

        rdf = pd.read_csv(cost_path)
        print(f"\nLoaded {len(rdf)} rows")

        # Clean
        rdf = rdf[rdf["finding_text"].notna() & rdf["cost_mid"].notna()]
        rdf = rdf[rdf["finding_text"].str.len() > 10]
        rdf = rdf[rdf["cost_mid"] > 0]
        rdf = rdf.drop_duplicates(subset="finding_text", keep="first")
        print(f"After cleaning: {len(rdf)} findings with cost estimates")

        if len(rdf) < 10:
            print(f"Only {len(rdf)} — need 10+ to train")
            results["Repair Cost"] = {"status": "NOT ENOUGH DATA"}
        else:
            print(f"\nCost range: ${rdf['cost_mid'].min():,.0f} — ${rdf['cost_mid'].max():,.0f}")
            print(f"Mean: ${rdf['cost_mid'].mean():,.0f}, Median: ${rdf['cost_mid'].median():,.0f}")
            print(f"\nBy category:")
            for cat, grp in rdf.groupby("category"):
                print(f"   {str(cat):30s} n={len(grp):3d}  avg=${grp['cost_mid'].mean():,.0f}  range=${grp['cost_mid'].min():,.0f}-${grp['cost_mid'].max():,.0f}")

            print(f"\nBy severity:")
            for sev, grp in rdf.groupby("severity"):
                print(f"   {str(sev):30s} n={len(grp):3d}  avg=${grp['cost_mid'].mean():,.0f}")

            # Build features: text embedding + category + severity + zip features
            print(f"\nEncoding {len(rdf)} findings...")
            r_emb = embedder.encode(rdf["finding_text"].tolist(), show_progress_bar=True, batch_size=64)

            # Add structured features alongside embeddings
            # One-hot encode category and severity
            cat_dummies = pd.get_dummies(rdf["category"], prefix="cat")
            sev_dummies = pd.get_dummies(rdf["severity"], prefix="sev")

            # ZIP code as numeric (for regional pricing patterns)
            rdf["zip_numeric"] = pd.to_numeric(rdf["zip_code"], errors="coerce").fillna(0) / 100000.0

            # Property price as feature (normalized)
            rdf["price_norm"] = rdf["property_price"].fillna(0) / 1_000_000.0

            # Combine: embeddings + structured features
            structured = np.hstack([
                cat_dummies.values,
                sev_dummies.values,
                rdf[["zip_numeric", "price_norm"]].values,
            ])
            X_all = np.hstack([r_emb, structured])
            print(f"Feature matrix: {X_all.shape} (384 embedding + {structured.shape[1]} structured)")

            # Target: predict cost_mid (regression)
            # Use log transform for better regression on skewed cost data
            y_cost = np.log1p(rdf["cost_mid"].values)

            X_tr, X_te, y_tr, y_te = train_test_split(
                X_all, y_cost, test_size=0.2, random_state=42)
            print(f"Split: train={len(X_tr)}, test={len(X_te)}")

            # Train regressor for cost_mid
            print("\n--- Cost Predictor (log-scale regression) ---")
            cost_model = xgb.XGBRegressor(
                n_estimators=300, max_depth=6, learning_rate=0.08,
                min_child_weight=3, subsample=0.8, colsample_bytree=0.8,
                objective="reg:squarederror", eval_metric="rmse",
                n_jobs=-1, random_state=42)
            cost_model.fit(X_tr, y_tr, eval_set=[(X_te, y_te)], verbose=False)

            # Predict and inverse transform
            y_pred_log = cost_model.predict(X_te)
            y_pred = np.expm1(y_pred_log)
            y_actual = np.expm1(y_te)

            mae = mean_absolute_error(y_actual, y_pred)
            r2 = r2_score(y_actual, y_pred)

            # Calculate percentage error
            pct_errors = np.abs(y_actual - y_pred) / np.maximum(y_actual, 1) * 100
            median_pct_err = np.median(pct_errors)

            print(f"\n  Mean Absolute Error:    ${mae:,.0f}")
            print(f"  Median % Error:         {median_pct_err:.0f}%")
            print(f"  R-squared:              {r2:.3f}")

            # Show some example predictions
            print(f"\n  Sample predictions (actual vs predicted):")
            indices = np.random.RandomState(42).choice(len(y_actual), min(8, len(y_actual)), replace=False)
            for i in indices:
                finding = rdf.iloc[X_te.shape[0] - len(y_te) + i if i < len(rdf) else 0]["finding_text"][:60]
                print(f"    ${y_actual[i]:>8,.0f} vs ${y_pred[i]:>8,.0f}  ({finding}...)")

            # Save model + feature metadata
            cost_model.save_model(os.path.join(MODELS, "repair_cost.xgb"))

            # Save the feature column names so inference knows the structure
            feature_meta = {
                "category_columns": list(cat_dummies.columns),
                "severity_columns": list(sev_dummies.columns),
                "embedding_dim": 384,
                "uses_log_transform": True,
            }
            pickle.dump(feature_meta, open(os.path.join(MODELS, "cost_feature_meta.pkl"), "wb"))

            results["Repair Cost"] = {
                "mae": f"${mae:,.0f}",
                "median_pct_err": f"{median_pct_err:.0f}%",
                "r2": f"{r2:.3f}",
                "status": "READY" if r2 >= 0.5 and median_pct_err <= 40 else "MARGINAL"
            }
            print(f"\n  >> Saved repair_cost.xgb + cost_feature_meta.pkl")

    except Exception as e:
        print(f"\nRepair Cost ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
        results["Repair Cost"] = {"status": "FAILED", "error": str(e)}
else:
    print(f"\n\nSkipping Repair Cost — no repair_costs.csv")
    print(f"  Export from admin panel: Repair Costs button")
    results["Repair Cost"] = {"status": "SKIPPED"}


# ═══════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════
print(f"\n\n{SEP}")
print("  TRAINING RESULTS SUMMARY")
print(SEP)

for name, r in results.items():
    status = r.get("status", "?")
    icon = "+" if status == "READY" else "~" if status == "MARGINAL" else "x"
    details = ""
    if "category" in r:
        details = f"cat={r['category']} sev={r['severity']}"
    elif "accuracy" in r:
        details = f"acc={r['accuracy']}"
    elif "mae" in r:
        details = f"MAE={r['mae']} median_err={r['median_pct_err']} R2={r['r2']}"
    elif "error" in r:
        details = r["error"][:60]
    print(f"  [{icon}] {name:30s} {status:10s} {details}")

# ── Accuracy Dashboard ──
print(f"\n\n{SEP}")
print("  ACCURACY DASHBOARD")
print(SEP)
print()
print(f"  {'Model':<28s} {'Metric':<22s} {'Current':>10s} {'Target':>10s} {'Gap':>10s}  Status")
print(f"  {'─'*28} {'─'*22} {'─'*10} {'─'*10} {'─'*10}  {'─'*10}")

rows = []
fc = results.get("Finding Classifier", {})
if "category" in fc:
    ca_val = float(fc["category"].replace("%",""))
    sa_val = float(fc["severity"].replace("%",""))
    rows.append(("Finding Classifier", "Category accuracy", ca_val, 90.0))
    rows.append(("Finding Classifier", "Severity accuracy", sa_val, 85.0))
elif fc.get("status") == "FAILED":
    rows.append(("Finding Classifier", "Status", 0, 0))

cd = results.get("Contradiction Detector", {})
if "accuracy" in cd:
    cd_val = float(cd["accuracy"].replace("%",""))
    rows.append(("Contradiction Detector", "Accuracy", cd_val, 99.0))

rc = results.get("Repair Cost", {})
if "r2" in rc:
    r2_val = float(rc["r2"])
    mae_val = float(rc["mae"].replace("$","").replace(",",""))
    pct_val = float(rc["median_pct_err"].replace("%",""))
    rows.append(("Repair Cost Predictor", "R-squared", r2_val * 100, 85.0))
    rows.append(("Repair Cost Predictor", "Median % error", 100 - pct_val, 90.0))
    rows.append(("Repair Cost Predictor", "MAE", 100 - (mae_val / 100), 99.0))

for model, metric, current, target in rows:
    if target == 0:
        status = "FAILED"
        gap_str = "---"
        cur_str = "FAILED"
    else:
        gap = target - current
        cur_str = f"{current:.1f}%"
        gap_str = f"{gap:+.1f}%" if gap != 0 else "  0.0%"
        if current >= target:
            status = "HIT"
        elif current >= target * 0.9:
            status = "CLOSE"
        else:
            status = "NEEDS WORK"
    
    icon = "+" if status == "HIT" else "~" if status == "CLOSE" else "x"
    print(f"  {model:<28s} {metric:<22s} {cur_str:>10s} {target:>9.1f}% {gap_str:>10s}  [{icon}] {status}")

print()
print(f"  {'─'*28} {'─'*22} {'─'*10} {'─'*10} {'─'*10}  {'─'*10}")

# Overall health
total = len(rows)
hits = sum(1 for _, _, c, t in rows if t > 0 and c >= t)
close = sum(1 for _, _, c, t in rows if t > 0 and c >= t * 0.9 and c < t)
needs = total - hits - close
failed = sum(1 for _, _, c, t in rows if t == 0)

print(f"\n  Overall: {hits} targets hit, {close} close, {needs} need work", end="")
if failed:
    print(f", {failed} failed", end="")
print()

# What to do next
print(f"\n  Next steps to improve:")
if any(m == "Finding Classifier" and t > 0 and c < t for m, _, c, t in rows):
    print(f"    - Finding Classifier: generate fresh augmented data (admin panel) and retrain")
    print(f"      More real analyses is the #1 driver — each adds ~30 labeled findings")
if any(m == "Repair Cost Predictor" and "R-squared" in met and c < t for m, met, c, t in rows):
    print(f"    - Repair Cost: more real analyses + contractor completions improve ground truth")
if any(m == "Contradiction Detector" and c < t for m, _, c, t in rows):
    print(f"    - Contradiction: more dual-document analyses (disclosure + inspection)")
print()

print(f"\nFiles saved:")
for f in sorted(os.listdir(MODELS)):
    if f.startswith("."): continue
    sz = os.path.getsize(os.path.join(MODELS, f))
    print(f"     {f} ({sz:,} bytes)")
    sz = os.path.getsize(os.path.join(MODELS, f))
    print(f"     {f} ({sz:,} bytes)")
PYEOF

RESULT=$?
echo ""

if [ $RESULT -eq 0 ]; then
  echo "════════════════════════════════════════════════════"
  echo "  Done! Upload model files via admin panel."
  echo "════════════════════════════════════════════════════"
  echo ""
  ls -lh "$MODELS_DIR"/*.xgb "$MODELS_DIR"/*.pkl 2>/dev/null
  echo ""
  if command -v open &> /dev/null; then
    echo "Opening models folder..."
    open "$MODELS_DIR"
  fi
else
  echo "════════════════════════════════════════════════════"
  echo "  ❌ Failed. Check errors above."
  echo "════════════════════════════════════════════════════"
fi

echo ""
echo "Press Enter to close..."
read
