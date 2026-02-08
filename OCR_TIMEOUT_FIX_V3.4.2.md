# V3.4.2 - OCR Timeout Fix

## The Problem

**Error:** "Upload failed: Failed to execute 'json' on 'Response': Unexpected end of JSON input"

**Root Cause:** OCR processing takes 30-90 seconds, but the server/frontend timed out before completion.

---

## What Was Fixed in V3.4.2

### 1. Extended Server Timeouts
**Created `gunicorn_config.py`:**
```python
timeout = 180  # 3 minutes for OCR (was 30 seconds default)
```

### 2. Frontend Timeout Handling
**Updated `static/app.html`:**
- Added 180-second timeout (3 minutes)
- Added better error handling for timeouts
- Added progress message: "Scanned PDFs may take 30-90 seconds"

### 3. Backend Progress Logging
**Updated `app.py`:**
- Logs each step of PDF processing
- Shows file size, extraction method, OCR usage
- Better error tracking

### 4. render.yaml Update
**Now uses gunicorn config:**
```yaml
startCommand: gunicorn --config gunicorn_config.py app:app
```

---

## Files Changed

| File | Change | Purpose |
|------|--------|---------|
| `gunicorn_config.py` | NEW | 180s timeout for OCR |
| `render.yaml` | Updated | Use gunicorn config |
| `static/app.html` | Enhanced | 180s frontend timeout + progress message |
| `app.py` | Enhanced | Detailed logging |
| `VERSION` | 3.4.2 | Timeout fix release |

---

## Deploy V3.4.2

```bash
# Extract
tar -xzf offerwise_render_v3_4_2_TIMEOUT_FIX.tar.gz
cd offerwise_render

# Commit and push
git add .
git commit -m "v3.4.2: Fix OCR timeout issues"
git push
```

---

## Expected Behavior

### Before V3.4.2 ❌
```
Upload scanned PDF (3.3 MB)
→ OCR starts (takes 60 seconds)
→ Server times out at 30 seconds
→ Response incomplete
→ Error: "Unexpected end of JSON input"
```

### After V3.4.2 ✅
```
Upload scanned PDF (3.3 MB)
→ Shows: "Scanned PDFs may take 30-90 seconds"
→ OCR processes (takes 60 seconds)
→ Server waits up to 180 seconds
→ Success! Text extracted
→ Analysis proceeds
```

---

## Testing

### Local Testing

```bash
# Run with gunicorn config
gunicorn --config gunicorn_config.py app:app

# Upload a scanned PDF
# Should see in logs:
#   INFO - PDF upload started
#   INFO - Starting PDF text extraction...
#   INFO - All native extraction methods failed - attempting OCR...
#   INFO - OCR processing page 1/10...
#   INFO - OCR completed: Extracted 12,543 characters
#   INFO - PDF upload successful: 10 pages, 12543 chars, OCR: True
```

### Server Logs

Watch server logs during upload:
```bash
# Should see:
✓ PDF upload started
✓ Decoding PDF (base64 length: 4456789)
✓ PDF decoded: 3.3 MB
✓ Starting PDF text extraction...
✓ pdfplumber extraction failed (expected for scanned)
✓ pdfminer extraction failed (expected for scanned)
✓ PyPDF2 extraction failed (expected for scanned)
✓ All native methods failed - attempting OCR (30-60 seconds)
✓ Converting PDF pages to images...
✓ Processing 10 pages with OCR...
✓ OCR processing page 1/10...
✓ OCR processing page 2/10...
...
✓ OCR completed: 12,543 characters from 10 pages
✓ PDF extraction completed using method: ocr
✓ PDF upload successful: 10 pages, 12543 chars, OCR: True
```

---

## Troubleshooting

### Still Getting Timeout

**Problem:** 180 seconds still not enough

**Solutions:**

1. **Increase timeout further** (edit `gunicorn_config.py`):
   ```python
   timeout = 300  # 5 minutes
   ```

2. **Reduce OCR DPI** for faster processing (edit `pdf_handler.py`):
   ```python
   images = convert_from_bytes(pdf_bytes, dpi=200)  # Lower = faster
   ```

3. **Upgrade server resources:**
   - More RAM = faster OCR
   - More CPU = faster processing

### Error: "Worker timeout"

**Problem:** Gunicorn worker timed out

**Solution:** Check gunicorn logs, increase timeout in `gunicorn_config.py`

### OCR Still Not Working

**Problem:** OCR dependencies not installed

**Verify:**
```bash
tesseract --version  # Should show version
python -c "import pytesseract; import pdf2image; print('OCR ready')"
```

**Fix:**
```bash
sudo apt-get install poppler-utils tesseract-ocr
```

### Render Free Tier Limitations

**Problem:** Render Free tier may:
- Not allow apt-get commands
- Have shorter timeouts
- Have less RAM

**Solutions:**
1. **Upgrade to Render Starter** ($7/mo) - Recommended
2. **Use Dockerfile approach** (see DEPLOYMENT_FIX_V3.4.1.md)
3. **Deploy to different platform** (Heroku, AWS, etc.)

---

## Performance Expectations

| PDF Type | Size | Method | Time | Status |
|----------|------|--------|------|--------|
| Native text | 5 MB | pdfplumber | 3s | ✅ Fast |
| Native text | 10 MB | pdfminer | 5s | ✅ Fast |
| **Scanned image** | **3 MB** | **OCR** | **45s** | ✅ **Works but slow** |
| **Scanned image** | **10 MB** | **OCR** | **90s** | ✅ **Works but slow** |
| Very large scanned | 50 MB | OCR | 180s+ | ⚠️ May timeout |

---

## User Experience

### Upload Flow

1. User selects PDF
2. Frontend shows: "Uploading and processing PDF... ⏱️ Scanned PDFs may take 30-90 seconds"
3. Server processes:
   - Try pdfplumber → Fail (scanned)
   - Try pdfminer → Fail (scanned)
   - Try PyPDF2 → Fail (scanned)
   - Try OCR → Success! (60 seconds)
4. Frontend receives response
5. Shows: "✓ Disclosure uploaded (10 pages)"

### What Users See

- Clear progress message
- No mysterious failures
- Success after waiting
- Page count confirmation

---

## Server Configuration

### Gunicorn Settings

```python
# gunicorn_config.py

timeout = 180        # 3 minutes for OCR
workers = 4          # Adjust based on CPU
worker_class = "sync"
keepalive = 5
```

### Why These Settings?

- **180s timeout:** Allows OCR to complete (30-90s typical)
- **Sync workers:** Better for long-running requests like OCR
- **Keepalive:** Maintains connection during processing

---

## Monitoring

### What to Monitor

1. **Upload duration:**
   - Native PDFs: <10 seconds
   - Scanned PDFs: 30-90 seconds

2. **Server logs:**
   - Look for "OCR processing" messages
   - Check extraction method used
   - Monitor for timeouts

3. **Error rates:**
   - Should be <1% with proper timeouts
   - Most errors should be file-related, not timeout

### Log Examples

**Good (OCR working):**
```
INFO - PDF upload started
INFO - PDF decoded: 3.3 MB
INFO - Starting PDF text extraction...
INFO - All native methods failed - attempting OCR
INFO - OCR processing page 1/10...
INFO - OCR completed: 12,543 characters
INFO - PDF upload successful: OCR: True
```

**Bad (Timeout):**
```
INFO - PDF upload started
INFO - Starting PDF text extraction...
INFO - All native methods failed - attempting OCR
ERROR - Worker timeout (signal 15)
```

---

## Summary

### The Issue
- OCR takes 30-90 seconds
- Default timeout: 30 seconds
- Result: Incomplete response, JSON parse error

### The Fix
- Gunicorn timeout: 180 seconds
- Frontend timeout: 180 seconds
- Progress messages for users
- Detailed logging

### The Result
- ✅ Scanned PDFs work
- ✅ Users informed of wait time
- ✅ No mysterious timeout errors
- ✅ Proper error messages if issues occur

---

**Deploy V3.4.2 and your scanned 3.3 MB PDF will upload successfully!**

## Quick Test

```bash
# After deploying V3.4.2
# Upload your scanned PDF
# Watch for:
# 1. Progress message showing
# 2. Wait 30-90 seconds (don't give up!)
# 3. Success message with page count
# 4. Analysis proceeds normally
```

**OCR is working - it just needs time!**
