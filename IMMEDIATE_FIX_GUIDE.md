# IMMEDIATE FIX FOR OCR 502 ERRORS
# Follow these steps in order

## Step 1: Add Diagnostic Endpoint (2 minutes)

1. Open `app.py`

2. Find the line around 860 that says:
   ```python
   @app.route('/api/upload-pdf', methods=['POST', 'OPTIONS'])
   ```

3. BEFORE that line, add this diagnostic endpoint:

```python
@app.route('/api/diagnostic/ocr-check')
def diagnostic_ocr_check():
    """Check if OCR dependencies are available"""
    import subprocess
    
    results = {'ocr_available': False, 'runtime': 'unknown'}
    
    try:
        tesseract = subprocess.run(['tesseract', '--version'], capture_output=True, timeout=1)
        poppler = subprocess.run(['pdfinfo', '--version'], capture_output=True, timeout=1)
        
        results['tesseract'] = tesseract.returncode == 0
        results['poppler'] = poppler.returncode == 0
        results['ocr_available'] = results['tesseract'] and results['poppler']
        results['runtime'] = 'Docker' if results['ocr_available'] else 'Python'
        
        if results['ocr_available']:
            results['message'] = '✅ OCR dependencies installed - Docker runtime confirmed'
        else:
            results['message'] = '❌ OCR dependencies missing - Python runtime detected'
            results['fix'] = 'Create new service with Docker runtime'
    except Exception as e:
        results['error'] = str(e)
        results['message'] = '❌ Diagnostic failed - likely Python runtime'
    
    return jsonify(results)
```

4. Save the file

5. Deploy:
   ```bash
   cd ~/offerwise_render
   git add app.py
   git commit -m "Add OCR diagnostic endpoint"
   git push origin main
   ```

6. Wait for deploy (1-2 minutes)

7. Visit: `https://getofferwise.ai/api/diagnostic/ocr-check`

---

## Step 2: Interpret Results

### If you see:
```json
{
  "ocr_available": true,
  "runtime": "Docker",
  "message": "✅ OCR dependencies installed - Docker runtime confirmed"
}
```
**→ Good news!** Dependencies are installed. The 502 error is something else.
**→ Skip to Step 4 (Different Issue)**

### If you see:
```json
{
  "ocr_available": false,
  "runtime": "Python",
  "message": "❌ OCR dependencies missing - Python runtime detected"
}
```
**→ This is the problem!** You're on Python runtime.
**→ Continue to Step 3**

---

## Step 3: Fix Runtime Issue (20 minutes)

### Option A: Create New Docker Service (RECOMMENDED)

**Why:** Render doesn't let you change runtime on existing service

1. **In Render Dashboard:**
   - Click "New +" → "Web Service"
   - Connect your GitHub repo
   - Render auto-detects Dockerfile → Runtime: Docker ✅
   - Name: `offerwise-docker-v2`
   - Plan: Starter

2. **Environment Variables:**
   Copy these from your old service:
   - DATABASE_URL
   - GOOGLE_CLIENT_ID
   - GOOGLE_CLIENT_SECRET
   - STRIPE_PUBLISHABLE_KEY
   - STRIPE_SECRET_KEY
   - (Facebook OAuth if you use it)

3. **Deploy** (takes 8-10 minutes first time)

4. **Test:** Visit `https://offerwise-docker-v2.onrender.com/api/diagnostic/ocr-check`
   Should show: `"ocr_available": true` ✅

5. **Switch DNS:**
   - Point `getofferwise.ai` to new service
   - Wait 5-10 minutes for DNS propagation
   - Delete old service

### Option B: Check Current Service (RARELY WORKS)

**Only do this if you're SURE the service was created with Docker runtime:**

1. Go to your service in Render dashboard
2. Click "Settings" tab
3. Look for "Runtime" setting
4. If it shows "Python" → You need Option A (create new service)
5. If it shows "Docker" but OCR still doesn't work → Continue to Step 4

---

## Step 4: If Runtime is Docker but OCR Still Fails

**This means Docker is configured but something else is wrong.**

### Check Build Logs:

1. Go to your Render service
2. Click "Logs" tab
3. Filter to "Build" logs (not "Deploy")
4. Search for these lines:

**Good signs:**
```
Setting up tesseract-ocr (4.1.1-2) ...
Setting up poppler-utils ...
```

**Bad signs:**
```
ERROR: Could not build wheels for paddlepaddle
ERROR: Failed building wheel for numpy
```

### Common Issues:

#### Issue: NumPy 2.x installed (PaddleOCR incompatible)
**Fix:** Your `requirements.txt` already has `numpy<2.0` so this shouldn't happen.
If it does, add to requirements.txt:
```
numpy==1.26.4  # Pin to specific 1.x version
```

#### Issue: PaddlePaddle installation fails
**Fix:** It will fall back to Tesseract automatically. Not a problem.

#### Issue: Memory issues during build
**Fix:** Already optimized with workers=1 and batch processing.

---

## Step 5: Test OCR Processing

After confirming OCR dependencies are installed:

1. Visit your app
2. Upload a scanned PDF
3. Open browser DevTools (F12) → Network tab
4. Watch for these requests:

**Should see:**
```
POST /api/upload-pdf → 200 OK ✅
GET /api/ocr-progress → 200 OK ✅ (polling)
Response: {"current": 10, "total": 44, "status": "processing"}
```

**Should NOT see:**
```
GET /api/ocr-progress → 502 Bad Gateway ❌
```

---

## Quick Reference: What You Need

### Required for OCR:
✅ Runtime: Docker (not Python)
✅ Dockerfile present (you have this)
✅ System deps: tesseract-ocr, poppler-utils
✅ Python deps: paddleocr, pytesseract, pdf2image
✅ Memory: 512 MB minimum (Starter plan)

### What Breaks OCR:
❌ Python runtime (can't install system deps)
❌ NumPy 2.x (PaddleOCR incompatible)
❌ Multiple workers on small RAM (memory issues)
❌ Missing environment variables (crashes worker)

---

## Expected Timeline

### If you need to create new service:
- Add diagnostic endpoint: 2 min
- Create new service: 3 min
- First build (Docker): 8-10 min
- Test and verify: 2 min
- Switch DNS: 5 min
- **Total: ~25 minutes**

### If service is already Docker:
- Add diagnostic endpoint: 2 min
- Verify issue: 1 min
- Debug specific problem: 5-10 min
- **Total: ~10 minutes**

---

## Success Criteria

You'll know it's working when:

1. ✅ `/api/diagnostic/ocr-check` returns `ocr_available: true`
2. ✅ Upload scanned PDF → See progress bar (10/44, 20/44...)
3. ✅ No 502 errors in Network tab
4. ✅ Server logs show: "🚀 PaddleOCR available"
5. ✅ Upload completes in 90-120 seconds

---

## Still Stuck?

If after following these steps you still have 502 errors:

1. **Share your diagnostic endpoint output**
   Visit: `https://your-app.onrender.com/api/diagnostic/ocr-check`
   Copy the JSON response

2. **Share your Render build logs**
   Go to: Logs → Build (not Deploy)
   Copy last 50 lines

3. **Share your error logs**
   Go to: Logs → Deploy
   Copy lines showing the 502 error

With these three pieces of info, we can diagnose the exact issue.
