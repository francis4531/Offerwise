# How to Make OCR Actually Work

## The Problem

**Render Free tier can't install system dependencies.**

Your error = Server doesn't have tesseract/poppler installed.

---

## Check First

Visit: `https://your-app.onrender.com/api/system-info`

If you see:
```json
{
  "ocr_fully_available": false,
  "tesseract_installed": false
}
```

Then OCR isn't installed. Follow solution below.

---

## Solution: Use Docker on Render Starter

### Step 1: Extract V3.4.3

```bash
tar -xzf offerwise_render_v3_4_3_DOCKER.tar.gz
cd offerwise_render
```

### Step 2: Rename Docker Config

```bash
# Use the Docker-based render config
cp render.docker.yaml render.yaml
```

Or manually edit `render.yaml`:
```yaml
services:
  - type: web
    name: offerwise
    runtime: docker  # ← Change this
    plan: starter    # ← Change this
    dockerfilePath: ./Dockerfile  # ← Add this
```

### Step 3: Upgrade Render Plan

1. Go to Render Dashboard
2. Select your service
3. Click "Settings"
4. Change plan: Free → Starter ($7/mo)
5. Save

### Step 4: Deploy

```bash
git add .
git commit -m "v3.4.3: Docker deployment for OCR"
git push
```

### Step 5: Verify

After deployment completes:

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

### Step 6: Test Upload

Upload your 3.3 MB scanned PDF:
- Wait 60-90 seconds
- Should succeed!
- Should show page count

---

## Alternative: Deploy to Heroku (Free)

If you don't want to pay $7/mo:

### Step 1: Create Heroku App

```bash
heroku create your-app-name
```

### Step 2: Add Buildpack

```bash
heroku buildpacks:add --index 1 heroku-community/apt
heroku buildpacks:add --index 2 heroku/python
```

### Step 3: Create Aptfile

```bash
cat > Aptfile << EOF
poppler-utils
tesseract-ocr
tesseract-ocr-eng
EOF
```

### Step 4: Deploy

```bash
git add .
git commit -m "Deploy to Heroku with OCR"
git push heroku main
```

### Step 5: Test

```bash
curl https://your-app-name.herokuapp.com/api/system-info
```

Should show OCR available.

---

## Why Docker Works

### Without Docker (Render Free):
```
render.yaml says: apt-get install tesseract
Render Free: "Nope, security restriction"
Result: No tesseract installed
```

### With Docker (Render Starter):
```
Dockerfile says: apt-get install tesseract
Docker: "Sure, I'll build that image"
Result: Tesseract installed in image
```

---

## Cost Summary

| Option | Cost | OCR Works |
|--------|------|-----------|
| Render Free | $0 | ❌ No |
| **Render Starter + Docker** | **$7/mo** | ✅ **Yes** |
| Heroku Free | $0 | ✅ Yes |
| DigitalOcean | $5/mo | ✅ Yes |

---

## Quick Test

Before uploading PDFs, always check:

```bash
curl https://your-app.onrender.com/api/system-info
```

If `ocr_fully_available: true` → You're good to go!

If `ocr_fully_available: false` → OCR won't work, fix deployment first.

---

## What's in V3.4.3

✅ `Dockerfile` - Docker image with OCR
✅ `render.docker.yaml` - Docker deployment config  
✅ `/api/system-info` - Diagnostic endpoint
✅ `gunicorn_config.py` - 180s timeout
✅ All the OCR code (already worked, just needed deps)

---

## TL;DR

1. Check `/api/system-info` - confirms OCR missing
2. Extract V3.4.3
3. Use `render.docker.yaml` as `render.yaml`
4. Upgrade to Render Starter ($7/mo)
5. Deploy
6. Check `/api/system-info` again - should be available
7. Upload scanned PDF - should work!

**Your 3.3 MB scanned PDF will work once you deploy with Docker!**
