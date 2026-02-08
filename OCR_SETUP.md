# OCR Setup Guide for OfferWise

## Why OCR is Required

Real estate documents (seller disclosures, inspection reports) are **often scanned images**, not native PDFs. OCR (Optical Character Recognition) is **essential** for OfferWise to work with these documents.

---

## Installation

### Step 1: Install Python Dependencies

```bash
pip install -r requirements.txt
```

This installs:
- `pytesseract` - Python wrapper for Tesseract OCR
- `pdf2image` - Converts PDF pages to images
- `Pillow` - Image processing

### Step 2: Install System Dependencies

OCR requires system-level software:

#### **Ubuntu / Debian**
```bash
sudo apt-get update
sudo apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-eng
```

#### **macOS**
```bash
brew install poppler tesseract
```

#### **Windows**
1. **Install Poppler:**
   - Download: https://github.com/oschwartz10612/poppler-windows/releases
   - Extract to `C:\Program Files\poppler`
   - Add `C:\Program Files\poppler\Library\bin` to PATH

2. **Install Tesseract:**
   - Download: https://github.com/UB-Mannheim/tesseract/wiki
   - Install to default location
   - Add to PATH if not done automatically

#### **Render.com / Cloud Deployment**

Add to your build script or `render.yaml`:

```yaml
buildCommand: |
  apt-get update
  apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-eng
  pip install -r requirements.txt
```

Or create a `build.sh`:
```bash
#!/bin/bash
apt-get update
apt-get install -y poppler-utils tesseract-ocr
pip install -r requirements.txt
```

---

## Verify Installation

```bash
# Test Tesseract
tesseract --version
# Should show: tesseract 4.x.x or higher

# Test Poppler
pdfinfo -v
# Should show: pdfinfo version 20.x.x or higher

# Test Python packages
python -c "import pytesseract; import pdf2image; print('OCR ready!')"
```

---

## How It Works

OfferWise uses a **smart fallback strategy**:

```
1. Try pdfplumber (native PDF text) → Fast, works for most PDFs
2. Try pdfminer (complex layouts) → Good for forms
3. Try PyPDF2 (basic extraction) → Simple fallback
4. Try OCR (scanned images) → SLOW but essential for scanned docs
```

**OCR is automatic** - if native extraction fails, OCR kicks in automatically.

---

## Performance

| Document Type | Method | Speed |
|--------------|---------|-------|
| Native PDF (text-based) | pdfplumber | 1-5 seconds |
| Native PDF (complex) | pdfminer | 2-10 seconds |
| **Scanned PDF (images)** | **OCR** | **30-90 seconds** |

**Note:** OCR is slower but necessary for scanned documents. A 10-page scanned PDF takes ~30-45 seconds.

---

## User Experience

### With OCR (Good) ✅

```
User uploads scanned PDF
→ Native methods fail (expected)
→ OCR starts automatically
→ "Processing with OCR (this may take 30-60 seconds)..."
→ ✓ Success: Text extracted
→ Analysis proceeds normally
```

### Without OCR (Bad) ❌

```
User uploads scanned PDF
→ Native methods fail
→ No OCR available
→ ERROR: "Could not extract text from PDF"
→ User frustrated, analysis impossible
```

---

## Troubleshooting

### "OCR not available" Error

**Problem:** System dependencies not installed

**Fix:**
```bash
# Ubuntu/Debian
sudo apt-get install poppler-utils tesseract-ocr

# macOS
brew install poppler tesseract

# Then test:
tesseract --version
```

### OCR Takes Too Long

**Problem:** Large PDFs with many pages

**Solutions:**
1. **Reduce DPI** (in pdf_handler.py):
   ```python
   images = convert_from_bytes(pdf_bytes, dpi=200)  # Lower = faster
   ```

2. **Process fewer pages** (for testing):
   ```python
   images = convert_from_bytes(pdf_bytes, dpi=300, first_page=1, last_page=5)
   ```

3. **Add progress indicator** (future enhancement)

### OCR Quality Issues

**Problem:** Extracted text has errors

**Solutions:**
- Increase DPI: `dpi=400` (slower but more accurate)
- Use better source documents (higher quality scans)
- Clean/preprocess images before OCR
- Install additional Tesseract language packs if needed

### Memory Issues

**Problem:** Server runs out of memory during OCR

**Cause:** High-resolution image processing

**Solutions:**
- Lower DPI: `dpi=200` instead of `dpi=300`
- Increase server RAM (2GB minimum recommended)
- Process pages in batches (future enhancement)

---

## Production Deployment

### Render.com

1. **Create `render.yaml`:**
```yaml
services:
  - type: web
    name: offerwise
    env: python
    buildCommand: |
      apt-get update
      apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-eng
      pip install -r requirements.txt
    startCommand: gunicorn app:app
```

2. **Or add to existing build script:**
```bash
apt-get install -y poppler-utils tesseract-ocr
```

### Heroku

Add `Aptfile` to root directory:
```
poppler-utils
tesseract-ocr
tesseract-ocr-eng
```

And use the apt buildpack:
```bash
heroku buildpacks:add --index 1 heroku-community/apt
```

### Docker

In your `Dockerfile`:
```dockerfile
FROM python:3.9-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy app
COPY . .

CMD ["gunicorn", "app:app"]
```

---

## Testing OCR

### Test with Diagnostic Tool

```bash
python diagnose_pdf.py scanned_document.pdf
```

Should show:
```
✓ Method used: ocr
✓ Page count: 10
✓ Text length: 15,234 characters
✓ SUCCESS: Text extracted successfully
```

### Create Test Scanned PDF

1. Take a photo of a document
2. Convert to PDF
3. Upload to OfferWise
4. Should process with OCR automatically

---

## Real Estate Document Types

### Native PDFs (Fast) ✅
- Modern digital inspection reports
- Computer-generated disclosures
- Electronic forms

### Scanned PDFs (Need OCR) 📸
- **Handwritten seller disclosures** ← Very common
- **Older inspection reports** ← Often scanned
- **Signed documents** ← Usually scanned after signing
- **County records** ← Often scanned archives
- **HOA documents** ← Frequently scanned

**Estimate: 40-60% of real estate PDFs need OCR**

---

## Cost Considerations

### Server Resources
- Native PDF: ~50-100 MB RAM
- OCR PDF: ~500 MB - 1 GB RAM per document

**Recommendation:** 2 GB RAM minimum for production

### Processing Time
- Native PDF: 5-10 seconds
- OCR PDF: 30-90 seconds

**Impact:** User needs to wait longer for scanned docs (but it works!)

---

## Future Enhancements

1. **Parallel OCR** - Process multiple pages simultaneously
2. **Progress Bar** - Show "Page 5/10 processing..."
3. **Smart Detection** - Detect scanned PDFs early, warn user
4. **Batch Processing** - Queue OCR jobs for background processing
5. **Language Support** - Add Spanish OCR for Latino market

---

## Summary

| Aspect | Status |
|--------|--------|
| **Python packages** | ✅ Added to requirements.txt |
| **System dependencies** | ⚠️ Must install manually |
| **Auto-detection** | ✅ OCR runs automatically when needed |
| **Performance** | ⚠️ Slower (30-90s) but necessary |
| **Production ready** | ✅ Yes, with system deps |

---

## Quick Start

```bash
# 1. Install system dependencies
sudo apt-get install poppler-utils tesseract-ocr  # Ubuntu/Debian
# OR
brew install poppler tesseract  # macOS

# 2. Install Python packages
pip install -r requirements.txt

# 3. Test
python diagnose_pdf.py scanned_document.pdf

# 4. Run OfferWise
python app.py

# 5. Upload scanned PDF - OCR works automatically!
```

---

**OCR is now a core feature of OfferWise - essential for real estate documents!**
