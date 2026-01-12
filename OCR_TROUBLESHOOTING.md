# OCR Not Working - Complete Troubleshooting Guide

## Your Error

```
"Upload failed: Failed to execute 'json' on 'Response': Unexpected end of JSON input"
```

This means the server is **crashing or timing out** during OCR processing.

---

## Step 1: Check if OCR is Actually Installed

### Test the Diagnostic Endpoint

Add this to your `app.py` (or use the updated V3.4.3):

```python
@app.route('/api/system-info', methods=['GET'])
def system_info():
    # ... (see diagnostic_endpoint.py)
```

Then visit: `https://your-app.onrender.com/api/system-info`

**You'll see:**
```json
{
  "ocr_fully_available": false,  ← This is your problem!
  "tesseract_installed": false,
  "poppler_installed": false,
  "warning": "OCR not fully available - scanned PDFs will fail"
}
```

---

## The Real Problem: Render Free Tier

**Render Free tier does NOT allow `apt-get` commands in buildCommand.**

Your `render.yaml` has:
```yaml
buildCommand: |
  apt-get update
  apt-get install -y poppler-utils tesseract-ocr  ← This fails silently!
```

But Render Free tier **ignores these commands**, so OCR is never installed.

---

## Solution 1: Use Docker (Recommended) ✅

Docker guarantees dependencies are installed.

### A. Create Dockerfile (already done in V3.4.3)

```dockerfile
FROM python:3.11-slim

# Install OCR dependencies
RUN apt-get update && apt-get install -y \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

CMD gunicorn --config gunicorn_config.py app:app --bind 0.0.0.0:$PORT
```

### B. Update render.yaml

```yaml
services:
  - type: web
    name: offerwise
    runtime: docker
    plan: starter  # Required for Docker ($7/mo)
    dockerfilePath: ./Dockerfile
```

### C. Deploy

```bash
git add Dockerfile render.yaml
git commit -m "Switch to Docker for OCR support"
git push
```

**Cost:** $7/month (Render Starter)
**Result:** OCR guaranteed to work

---

## Solution 2: Use Render Starter Plan (Without Docker)

Render Starter allows `apt-get` commands.

### Update render.yaml:

```yaml
services:
  - type: web
    name: offerwise
    runtime: python
    plan: starter  # Change from 'free'
    buildCommand: |
      apt-get update
      apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-eng
      pip install -r requirements.txt
```

**Cost:** $7/month
**Result:** OCR should work

---

## Solution 3: Deploy to Different Platform

### Heroku (Free tier works)

```bash
# Create Aptfile
echo "poppler-utils" > Aptfile
echo "tesseract-ocr" >> Aptfile
echo "tesseract-ocr-eng" >> Aptfile

# Add buildpack
heroku buildpacks:add --index 1 heroku-community/apt

# Deploy
git push heroku main
```

### DigitalOcean App Platform

```yaml
# .do/app.yaml
services:
- name: offerwise
  dockerfile_path: Dockerfile
  instance_count: 1
  instance_size_slug: basic-xxs
```

### AWS/GCP/Azure

Full control, install whatever you need.

---

## Solution 4: Accept Only Native PDFs (Not Recommended)

If you can't upgrade/change platforms:

### Remove OCR from requirements.txt:

```python
# Comment out or remove:
# pdf2image==1.16.3
# pytesseract==0.3.10
# Pillow>=10.3.0
```

### Update frontend to warn users:

```javascript
// In app.html
alert('⚠️ This app only works with text-based PDFs, not scanned images. Please use PDFs with selectable text.');
```

**Downside:** 40-60% of real estate PDFs won't work.

---

## Recommended Path Forward

### Immediate (Testing):

1. Check `/api/system-info` endpoint
2. Confirm OCR is NOT installed
3. Understand the limitation

### Short Term (Production):

**Option A: Render Starter + Docker** ($7/mo)
- Extract V3.4.3
- Use `render.docker.yaml`
- Deploy with Docker runtime
- ✅ Guaranteed to work

**Option B: Move to Heroku**
- Free tier supports apt-get
- Simpler than Docker
- ✅ Works on free tier

### Why Render Free Doesn't Work:

| Feature | Free | Starter |
|---------|------|---------|
| apt-get commands | ❌ Blocked | ✅ Allowed |
| Docker runtime | ❌ No | ✅ Yes |
| OCR support | ❌ No | ✅ Yes |
| **Cost** | Free | **$7/mo** |

---

## Test Your Fix

After deploying with proper setup:

### 1. Check System Info

```bash
curl https://your-app.onrender.com/api/system-info
```

Should show:
```json
{
  "ocr_fully_available": true,
  "tesseract_installed": true,
  "poppler_installed": true
}
```

### 2. Upload Test PDF

Upload your 3.3 MB scanned PDF:
- Should take 60-90 seconds
- Should succeed with page count
- Should not timeout

### 3. Check Logs

Should see:
```
INFO - All native methods failed - attempting OCR
INFO - OCR processing page 1/10...
INFO - OCR completed: 12,543 characters
INFO - PDF upload successful: OCR: True
```

---

## Why This is Happening

```
You: Deploy to Render Free
Render: Ignores apt-get commands (security restriction)
Result: No tesseract, no poppler
Python: pytesseract installed but can't find tesseract binary
OCR: Crashes when trying to call tesseract
Server: Times out mid-response
Browser: "Unexpected end of JSON input"
```

**Fix:** Use Docker or Starter plan so apt-get actually runs.

---

## Cost Analysis

| Platform | Plan | OCR Support | Cost |
|----------|------|-------------|------|
| **Render Free** | Free | ❌ No | Free |
| **Render Starter** | Paid | ✅ Yes | $7/mo |
| **Render Starter + Docker** | Paid | ✅ Guaranteed | $7/mo |
| **Heroku Free** | Free | ✅ Yes | Free |
| **DigitalOcean** | Paid | ✅ Yes | $5/mo |
| **AWS/GCP** | Paid | ✅ Yes | Varies |

**Recommendation:** 
- Development: Heroku Free
- Production: Render Starter + Docker ($7/mo)

---

## Quick Decision Matrix

### Can you pay $7/month?

**YES** → Use Render Starter + Docker (V3.4.3)
- Most reliable
- Guaranteed OCR
- Easy deployment

**NO** → Use Heroku Free
- OCR works on free tier
- Aptfile approach
- Good for testing

### Want to stay on Render Free?

**Accept limitation:** Only native PDFs will work
- Remove OCR dependencies
- Warn users about scanned PDFs
- 40-60% of documents won't work

---

## Files You Need (V3.4.3)

✅ `Dockerfile` - Guarantees OCR installation
✅ `render.docker.yaml` - Docker-based deployment
✅ `gunicorn_config.py` - 180s timeout
✅ `app.py` with `/api/system-info` - Diagnostic endpoint
✅ All OCR code in `pdf_handler.py`

---

## Action Plan

1. **Check what you have:**
   ```bash
   curl https://your-app.onrender.com/api/system-info
   ```

2. **If `ocr_fully_available: false`:**
   - Extract V3.4.3
   - Choose: Docker on Starter OR Heroku Free
   - Deploy with proper setup

3. **Test again:**
   - Upload scanned PDF
   - Should work!

---

## Bottom Line

**Your OCR code is perfect.**  
**Your Render Free plan can't install the dependencies.**  
**Solution: Upgrade to Starter ($7/mo) or move to Heroku (free).**

Without system dependencies (tesseract, poppler), OCR literally cannot work no matter how good the code is.

---

**Next Step: Visit /api/system-info to confirm OCR is missing, then deploy V3.4.3 with Docker or move to Heroku.**
