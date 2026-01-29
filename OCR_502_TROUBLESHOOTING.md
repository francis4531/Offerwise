# OCR 502 ERROR TROUBLESHOOTING GUIDE

## 🚨 Problem: `/api/ocr-progress` Returns 502 Bad Gateway

**Symptom:** Frontend shows repeated 502 errors when trying to process scanned PDFs

**Root Cause:** Backend worker is crashing because OCR dependencies are missing

---

## 🔍 Step 1: Verify Your Render Service Runtime

### Check in Render Dashboard:

1. Go to your Render dashboard
2. Click on your OfferWise service
3. Look at the **"Runtime"** setting in the service details

**What you should see:**
```
Runtime: Docker ✅
```

**If you see this instead:**
```
Runtime: Python ❌
```

**Then that's your problem!** Python runtime cannot install system dependencies like:
- tesseract-ocr
- poppler-utils
- OpenCV libraries

---

## 🔧 Step 2: Run Diagnostic Endpoint

Add this diagnostic endpoint to check if OCR dependencies are available:

### A. Add diagnostic endpoint to `app.py`:

```python
@app.route('/api/diagnostic/ocr-dependencies')
def check_ocr_dependencies():
    """Diagnostic endpoint to check OCR dependencies"""
    import subprocess
    
    results = {
        'runtime': 'unknown',
        'dependencies': {},
        'python_packages': {}
    }
    
    # Check system commands
    for cmd, name in [
        ('tesseract', 'tesseract-ocr'),
        ('pdfinfo', 'poppler-utils')
    ]:
        try:
            result = subprocess.run([cmd, '--version'], capture_output=True, timeout=2)
            results['dependencies'][name] = {
                'installed': result.returncode == 0,
                'version': result.stdout.decode('utf-8').strip().split('\n')[0]
            }
        except (FileNotFoundError, subprocess.TimeoutExpired):
            results['dependencies'][name] = {
                'installed': False,
                'error': 'Command not found'
            }
    
    # Check Python packages
    for package in ['paddleocr', 'pytesseract', 'pdf2image', 'numpy']:
        try:
            module = __import__(package)
            results['python_packages'][package] = {
                'installed': True,
                'version': getattr(module, '__version__', 'unknown')
            }
        except ImportError as e:
            results['python_packages'][package] = {
                'installed': False,
                'error': str(e)
            }
    
    # Determine runtime
    if results['dependencies']['tesseract-ocr']['installed']:
        results['runtime'] = 'Docker ✅'
    else:
        results['runtime'] = 'Python ❌ (Missing system dependencies)'
    
    return jsonify(results)
```

### B. Deploy this change and visit:
```
https://your-app.onrender.com/api/diagnostic/ocr-dependencies
```

---

## ✅ Solution 1: Create New Docker Service (RECOMMENDED)

**Why:** Render doesn't allow changing runtime on existing service

**Time:** 15-20 minutes

### Steps:

1. **Push your code to GitHub** (if not already):
   ```bash
   cd ~/Offerwise
   git add .
   git commit -m "v3.8.4: Ready for Docker deployment"
   git push origin main
   ```

2. **In Render Dashboard:**
   - Click "New +" → "Web Service"
   - Connect your Offerwise repository
   - Render will auto-detect `Dockerfile` ✅
   - **Name:** `offerwise-docker`
   - **Runtime:** Docker (auto-detected) ✅
   - **Plan:** Starter

3. **Environment Variables:**
   The new service will automatically use `render.yaml` settings, but verify:
   - DATABASE_URL
   - GOOGLE_CLIENT_ID
   - GOOGLE_CLIENT_SECRET
   - FACEBOOK_CLIENT_ID (if using)
   - FACEBOOK_CLIENT_SECRET (if using)
   - STRIPE_PUBLISHABLE_KEY
   - STRIPE_SECRET_KEY

4. **Click "Create Web Service"**

5. **Wait for build** (~5-10 minutes first time):
   ```
   Building Dockerfile...
   Setting up tesseract-ocr ✅
   Installing Python packages ✅
   Successfully built
   ```

6. **Test the new service:**
   - Visit: `https://offerwise-docker.onrender.com/api/diagnostic/ocr-dependencies`
   - Should show all dependencies installed ✅

7. **Switch DNS** (when ready):
   - Point `getofferwise.ai` to new service
   - Delete old Python-runtime service

---

## ⚠️ Solution 2: Temporary OCR Bypass (NOT RECOMMENDED)

**Only use this if you need the site working NOW while you set up Docker service**

### Disable OCR temporarily:

Add this to the top of `pdf_handler.py`:

```python
# EMERGENCY: Force OCR to be unavailable
OCR_AVAILABLE = False
PADDLEOCR_AVAILABLE = False
```

**Result:**
- ✅ Text-based PDFs will still work
- ❌ Scanned PDFs will fail with clear error message
- ⚠️  This is NOT a solution, just a bandaid

---

## 📊 How to Tell if Docker Runtime is Working

### Good signs (Docker runtime):
```
Logs show:
✅ "Setting up tesseract-ocr (4.1.1-2)"
✅ "Setting up poppler-utils"
✅ "🚀 PaddleOCR available - using fast OCR engine"
✅ Build takes 5-10 minutes (installing system dependencies)

Diagnostic endpoint shows:
✅ tesseract-ocr: installed
✅ poppler-utils: installed
✅ PaddleOCR: initialized successfully
```

### Bad signs (Python runtime):
```
Logs show:
❌ No mention of tesseract or poppler
❌ Build takes < 2 minutes (too fast)
❌ "⚠️ PaddleOCR not available - falling back to Tesseract"
❌ "⚠️ OCR not available - cannot process scanned documents"

Diagnostic endpoint shows:
❌ tesseract-ocr: NOT FOUND
❌ poppler-utils: NOT FOUND
❌ Runtime: Python ❌

Error logs show:
❌ 502 Bad Gateway on /api/ocr-progress
❌ Worker crashes when processing scanned PDFs
```

---

## 🎯 The Real Fix

**You need Docker runtime.** There's no way around this for OCR processing.

**The fastest path forward:**

1. ✅ Create new service with Docker runtime (15 min)
2. ✅ Verify OCR works on new service (5 min)
3. ✅ Switch DNS to new service (5 min)
4. ✅ Delete old Python service

**Total time:** ~25 minutes of active work

---

## 🧪 Quick Test After Fix

Visit your service and run this in browser console:

```javascript
// Test OCR dependencies endpoint
fetch('/api/diagnostic/ocr-dependencies')
  .then(r => r.json())
  .then(data => {
    console.log('Runtime:', data.runtime);
    console.log('Dependencies:', data.dependencies);
    console.log('Python packages:', data.python_packages);
    
    if (data.dependencies['tesseract-ocr']?.installed) {
      console.log('✅ Docker runtime confirmed!');
    } else {
      console.log('❌ Still on Python runtime');
    }
  });
```

---

## 💡 Why This Happens

**Render's service creation:**
- If you create service manually → Asks you to choose runtime
- If you already had a Python service → Can't change runtime
- Solution → Must create NEW service

**render.yaml specifies `runtime: docker`** but only applies when:
- Creating a new service
- Render reads the yaml file during creation

Your current service was probably created before render.yaml existed or was created with Python runtime selected.

---

## 🎉 Expected Result After Fix

**Before (Python runtime):**
```
Upload scanned PDF → 502 errors → Nothing works ❌
```

**After (Docker runtime):**
```
Upload scanned PDF → "Processing 44 pages..." → 90 seconds → SUCCESS ✅
```

**Logs will show:**
```
🚀 PaddleOCR available - using fast OCR engine
Processing 44 pages with OCR (batch mode: 2 pages at a time)
OCR progress: 10/44 pages completed
OCR progress: 20/44 pages completed
...
📊 OCR methods used: PaddleOCR: 44, Tesseract: 0
OCR completed: Extracted 45,230 characters
Upload SUCCESS! 🎉
```
