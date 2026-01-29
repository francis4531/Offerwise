# 502 Bad Gateway Error - Complete Fix Guide

## Your Error

```
Failed to load resource: the server responded with a status of 502 ()
Upload error: Error: Upload failed
Server error or timeout.
```

**502 = Your server is crashing or timing out.**

---

## 🔍 Step 1: Check Server Logs (CRITICAL)

**Go to Render Dashboard:**
1. Click your service name
2. Click **"Logs"** tab
3. Scroll to the bottom for most recent errors

**Look for these common errors:**

### Error A: Out of Memory
```
signal 9 (SIGKILL)
Worker timeout
MemoryError
```
**Cause:** 512 MB RAM not enough for OCR  
**Fix:** See "Memory Fix" below

### Error B: Import Error
```
ModuleNotFoundError: No module named 'pytesseract'
ImportError: cannot import name 'OCR_AVAILABLE'
```
**Cause:** Docker build failed, dependencies missing  
**Fix:** See "Rebuild Fix" below

### Error C: Tesseract Not Found
```
TesseractNotFoundError
tesseract: command not found
```
**Cause:** Docker didn't install tesseract  
**Fix:** See "Docker Fix" below

### Error D: Worker Timeout
```
[CRITICAL] WORKER TIMEOUT
Worker with pid [X] was terminated due to signal 15
```
**Cause:** Gunicorn timeout even with 180s config  
**Fix:** See "Timeout Fix" below

### Error E: Application Crash
```
Application failed to start
Error: ...
```
**Cause:** Code error or missing environment variable  
**Fix:** Check specific error message

---

## 🔧 Fixes Based on Error Type

### Fix 1: Memory Issue (Most Common)

**Problem:** Render Starter (512 MB) runs out of memory during OCR

**Solution A: Lower OCR Quality (Immediate)**

1. In Render Dashboard → Settings → Environment
2. Add environment variable:
   ```
   OCR_DPI = 150
   ```
3. Save and redeploy
4. Test upload (faster, uses less memory)

**Solution B: Upgrade to Standard Plan** (Recommended)

1. Render Dashboard → Settings → Plan
2. Change: Starter → **Standard** (2 GB RAM)
3. Cost: $25/month
4. Retry upload

**Memory Requirements by DPI:**
| DPI | Memory | Quality | Speed | Render Plan |
|-----|--------|---------|-------|-------------|
| 150 | 200-300 MB | OK | Fast | Starter ✅ |
| 200 | 300-500 MB | Good | Medium | Starter ⚠️ |
| 300 | 500-800 MB | Best | Slow | Standard ✅ |

---

### Fix 2: Rebuild from Scratch

**Problem:** Build cache causing issues

**Solution:**

1. Render Dashboard → Your Service
2. **Manual Deploy** → **"Clear build cache & deploy"**
3. Wait for rebuild (5-10 minutes)
4. Check logs for errors
5. Test upload

---

### Fix 3: Docker Not Building Properly

**Problem:** Dockerfile isn't being used or failing

**Verify render.yaml:**
```yaml
services:
  - type: web
    name: offerwise
    runtime: docker  # ← MUST be "docker"
    plan: starter
    dockerfilePath: ./Dockerfile  # ← MUST point to Dockerfile
```

**If wrong:**
1. Fix render.yaml
2. Commit: `git commit -am "Fix render.yaml"`
3. Push: `git push`

---

### Fix 4: Environment Variables Missing

**Problem:** Missing required environment variables

**Check these are set in Render:**

**Required:**
- `SECRET_KEY` - Should auto-generate
- `DATABASE_URL` - Should be linked to database

**May be required:**
- `ANTHROPIC_API_KEY` - Set manually if using Claude API
- `STRIPE_SECRET_KEY` - If using payments

**To check:**
1. Render Dashboard → Settings → Environment
2. Verify all required vars are set
3. Add missing ones
4. Redeploy

---

### Fix 5: Gunicorn Timeout Extension

**Problem:** Even 180s timeout isn't enough

**Solution - Edit gunicorn_config.py:**

Find line:
```python
timeout = 180
```

Change to:
```python
timeout = 300  # 5 minutes
```

Commit and push.

---

## 🧪 Test After Each Fix

### Test 1: Server Health
```bash
curl https://your-app.onrender.com/api/health
```
Should return 200 OK

### Test 2: OCR Status
```bash
curl https://your-app.onrender.com/api/system-info
```
Should show:
```json
{
  "ocr_fully_available": true,
  "tesseract_installed": true
}
```

### Test 3: Small Native PDF
Upload a small (< 1 MB) native text PDF
- Should work quickly (5 seconds)

### Test 4: Scanned PDF
Upload your 3.3 MB scanned PDF
- Should work in 60-90 seconds
- If 502: Memory issue

---

## 📊 Diagnostic Checklist

Before asking for help, check:

- [ ] Render logs show specific error
- [ ] Plan is Starter or Standard (not Free)
- [ ] render.yaml uses `runtime: docker`
- [ ] Dockerfile exists in repo
- [ ] `/api/system-info` shows OCR available
- [ ] `/api/health` returns 200
- [ ] All environment variables are set
- [ ] Native PDFs work (to isolate OCR issue)

---

## 🎯 Most Likely Root Causes

### 1. Out of Memory (80% of 502 errors)
- **Symptom:** Upload fails after 30-60 seconds
- **Log:** "signal 9" or "Worker timeout"
- **Fix:** Lower OCR_DPI to 150 OR upgrade to Standard

### 2. Build Failed (10%)
- **Symptom:** Server won't start at all
- **Log:** "Build failed" or "ImportError"
- **Fix:** Check build logs, fix errors, rebuild

### 3. Wrong Configuration (5%)
- **Symptom:** Docker not being used
- **Log:** No tesseract installation messages
- **Fix:** Verify render.yaml has `runtime: docker`

### 4. Environment Variable Missing (5%)
- **Symptom:** App crashes on startup
- **Log:** "KeyError" or specific variable name
- **Fix:** Add missing environment variable

---

## 🚀 Recommended Action Plan

**Right Now:**

1. **Check Render logs** (tells you exact problem)
2. **Add OCR_DPI=150** environment variable
3. **Restart service**
4. **Test upload**

**If still fails:**

1. **Check /api/system-info** (OCR available?)
2. **Try native PDF first** (isolate OCR issue)
3. **Review render.yaml** (using Docker?)
4. **Consider Standard plan** (more RAM)

**If desperate:**

1. **Clear build cache and redeploy**
2. **Check all environment variables**
3. **Review Dockerfile** (tesseract installation)
4. **Contact Render support** (show them logs)

---

## 💰 Cost vs Performance

| Plan | RAM | OCR Works? | Your PDF | Cost |
|------|-----|------------|----------|------|
| Starter | 512 MB | ⚠️ Maybe | May fail | $7/mo |
| Starter + OCR_DPI=150 | 512 MB | ✅ Yes | Works | $7/mo |
| **Standard** | 2 GB | ✅ Yes | **Works great** | **$25/mo** |

**Recommendation:** Start with Starter + OCR_DPI=150, upgrade to Standard if needed.

---

## 📝 V3.4.5 Changes

**New in this version:**

✅ Configurable OCR DPI via environment variable  
✅ Default DPI: 200 (balanced)  
✅ Memory-optimized for Render Starter  
✅ Better error messages  

**To use lower DPI:**
- Add `OCR_DPI=150` in Render environment variables
- Faster processing, less memory
- Slightly lower quality but usually fine

---

## 🆘 Still Getting 502?

**Tell me:**
1. What do Render logs say? (exact error)
2. Does /api/system-info work?
3. What's your current Render plan?
4. Did you set OCR_DPI environment variable?

**With this info, I can give you the exact fix.**

---

## Quick Commands

```bash
# Check server status
curl https://your-app.onrender.com/api/health

# Check OCR availability  
curl https://your-app.onrender.com/api/system-info

# Test with actual file
python test_server.py https://your-app.onrender.com
```

---

**Most 502 errors are memory issues. Add OCR_DPI=150 environment variable and restart. This should fix it immediately.**
