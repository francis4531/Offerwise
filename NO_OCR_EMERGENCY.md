# Emergency: Deploy Without OCR

Your scanned PDFs won't work, but at least the app will function with native PDFs.

## Quick Fix: Remove OCR Dependencies

### 1. Edit requirements.txt

Remove or comment out these lines:

```python
# OCR for scanned PDFs (CURRENTLY NOT WORKING ON RENDER FREE)
# pdf2image==1.16.3
# pytesseract==0.3.10
# Pillow>=10.3.0
```

### 2. Update render.yaml

Remove OCR installation:

```yaml
services:
  - type: web
    name: offerwise
    runtime: python
    plan: free
    buildCommand: pip install -r requirements.txt  # Remove apt-get commands
    startCommand: gunicorn --config gunicorn_config.py app:app
```

### 3. Deploy

```bash
git add requirements.txt render.yaml
git commit -m "Temporarily disable OCR for Render Free deployment"
git push
```

### 4. What Works / Doesn't Work

✅ **WORKS:**
- Native text PDFs (computer-generated)
- Modern digital inspection reports
- Electronic forms
- Most computer-created documents

❌ **DOESN'T WORK:**
- Scanned image PDFs
- Handwritten then scanned documents
- Older paper documents that were scanned
- Photos of documents

---

## Add Warning to Frontend

Users need to know the limitation. Add this to your upload page:

```html
<div style="background: #fef3c7; padding: 16px; borderRadius: 8px; marginBottom: 16px;">
  ⚠️ <strong>Important:</strong> Currently only text-based PDFs are supported. 
  Scanned image PDFs will not work. To check if your PDF is text-based, 
  open it and try to select text with your cursor - if you can select text, it will work.
</div>
```

---

## This is TEMPORARY

Once you:
1. Upgrade to Render Starter ($7/mo), OR
2. Move to Heroku (free), OR  
3. Deploy with Docker

Then OCR will work and scanned PDFs will be supported.

---

## Test After Deploying

1. Visit `/api/system-info` - will show OCR unavailable
2. Try uploading a native text PDF - should work
3. Try uploading a scanned PDF - will show clear error message
4. App at least functions for native PDFs

---

This gets your app working NOW while you figure out the OCR deployment separately.
