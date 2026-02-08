# V3.4.1 - Render Deployment Fix

## The Problem

Render deployment failed with:
```
KeyError: '__version__'
× Getting requirements to build wheel did not run successfully.
```

**Root Cause:** Pillow 10.1.0 incompatible with Python 3.13

---

## The Fix

### Changed in V3.4.1:

1. **Updated Pillow version** (requirements.txt)
   ```
   # Before
   Pillow==10.1.0  # Fails on Python 3.13
   
   # After  
   Pillow>=10.3.0  # Works with Python 3.11 and 3.13
   ```

2. **Updated render.yaml**
   - Added OCR system dependencies
   - Fixed Python version specification
   ```yaml
   buildCommand: |
     apt-get update
     apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-eng
     pip install -r requirements.txt
   envVars:
     - key: PYTHON_VERSION
       value: "3.11"
   ```

3. **Updated runtime.txt** (already existed, no change needed)
   ```
   python-3.11.7
   ```

---

## Deployment Instructions

### Step 1: Extract V3.4.1
```bash
tar -xzf offerwise_render_v3_4_1_DEPLOY_FIX.tar.gz
cd offerwise_render
```

### Step 2: Commit and Push
```bash
git add .
git commit -m "v3.4.1: Fix Render deployment + Add OCR support"
git push
```

### Step 3: Render Will Auto-Deploy

The build should now succeed with:
```
✓ Installing system dependencies (poppler, tesseract)
✓ Installing Python packages (Pillow >=10.3.0)
✓ Build successful
✓ Deployment complete
```

---

## If Build Still Fails

### Issue: Render Free Tier Limitations

**Problem:** Render Free tier may not allow `apt-get` commands

**Solution 1: Use Render Starter Plan** ($7/month)
- Has full system access
- Can install OCR dependencies
- Recommended for production

**Solution 2: Disable OCR** (NOT recommended)
- Remove OCR dependencies from requirements.txt
- Scanned PDFs won't work
- Only works with native text PDFs

**Solution 3: Use Different Host**
- Heroku (with buildpack)
- AWS/GCP (full control)
- DigitalOcean App Platform

---

## Testing Build Locally

Before deploying, test locally:

```bash
# 1. Install OCR dependencies
sudo apt-get install poppler-utils tesseract-ocr  # Ubuntu
brew install poppler tesseract  # macOS

# 2. Install Python packages
pip install -r requirements.txt

# 3. Test
python diagnose_pdf.py scanned_document.pdf

# 4. Run app
python app.py
```

---

## Render Free Tier vs Starter

| Feature | Free | Starter ($7/mo) |
|---------|------|-----------------|
| Python packages | ✅ | ✅ |
| System packages (apt-get) | ⚠️ Limited | ✅ Full |
| RAM | 512 MB | 512 MB |
| OCR support | ⚠️ May fail | ✅ Works |
| **Recommendation** | Testing only | **Production** |

---

## Alternative: Render with Dockerfile

If `apt-get` doesn't work in render.yaml, use a Dockerfile:

Create `Dockerfile`:
```dockerfile
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:$PORT"]
```

Update render.yaml:
```yaml
services:
  - type: web
    name: offerwise
    runtime: docker
    plan: starter
    dockerfilePath: ./Dockerfile
```

---

## What Changed From V3.4.0 to V3.4.1

| File | Change | Why |
|------|--------|-----|
| requirements.txt | `Pillow>=10.3.0` | Fix Python 3.13 compatibility |
| render.yaml | Added OCR dependencies | Enable scanned PDF support |
| VERSION | 3.4.0 → 3.4.1 | Bug fix release |

---

## Summary

**V3.4.0 Issue:**
- Pillow 10.1.0 failed on Python 3.13
- render.yaml didn't install OCR dependencies

**V3.4.1 Fix:**
- ✅ Pillow updated to >=10.3.0 (compatible)
- ✅ render.yaml includes OCR dependencies
- ✅ Python version properly specified

**Deploy:** Just commit and push - should work now!

---

## Still Failing?

1. **Check Python version** in Render dashboard
2. **Verify plan** (Free vs Starter)
3. **Check build logs** for specific error
4. **Consider Dockerfile** approach (see above)
5. **Or use different platform** (Heroku, AWS, etc.)

For production with OCR, **Render Starter plan ($7/mo) is recommended**.
