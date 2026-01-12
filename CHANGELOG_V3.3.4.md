# OfferWise V3.3.4 - Bug Fix Release

**Release Date:** January 5, 2026

## Bug Fixes

### Issue #1: "Profile" → "Settings" Terminology ✅ FIXED

**Problem:** There were still references to "profile" instead of "settings" in user-facing messages, particularly in success pop-ups after saving settings.

**Files Changed:**
- `static/app.html` (line 521)
- `static/profile.html` (line 212)
- `static/settings.html` (line 542)

**Changes:**
- ❌ Old: "Profile saved! You can update it anytime from the Profile page."
- ✅ New: "Settings saved! You can update them anytime from the Settings page."

- ❌ Old: "Profile saved successfully! Your preferences will auto-fill on all future analyses."
- ✅ New: "Settings saved successfully! Your preferences will auto-fill on all future analyses."

**Impact:** All user-facing messages now consistently use "settings" terminology.

---

### Issue #2: PDF Parser Enhancement ✅ FIXED

**Problem:** The PDF parser failed to properly extract content from complex real estate disclosure documents (like Pendleton_Disclosures.pdf) that contain:
- Mixed printed and handwritten content
- Form fields and checkboxes
- Complex multi-column layouts
- Overlapping signatures and stamps
- Tables with intricate structures
- Small font sizes in footers

**Root Cause:** The basic PyPDF2 parser couldn't handle complex form layouts and nested structures.

**Solution Implemented:**

1. **Enhanced pdfplumber extraction** (`pdf_handler.py`)
   - Added `layout=True` parameter for better form handling
   - Improved table extraction with strict line detection
   - Better tolerance settings for complex layouts
   - Enhanced error handling with fallbacks

2. **Added pdfminer.six as additional fallback**
   - Provides better text extraction for complex PDFs
   - Handles documents that pdfplumber struggles with
   - Maintains multiple extraction strategies

3. **Updated extraction pipeline:**
   ```
   1st attempt: pdfplumber (with layout preservation)
   2nd attempt: pdfminer.six (if available)
   3rd attempt: PyPDF2 (basic fallback)
   ```

**Files Changed:**
- `pdf_handler.py` - Enhanced extraction methods
- `requirements.txt` - Added pdfminer.six==20221105

**Testing:**
- Created `test_pdf_parser.py` for validation
- Tested with 44-page Pendleton_Disclosures.pdf
- Verifies extraction of:
  - Property address
  - Seller disclosure content
  - Form fields (yes/no checkboxes)
  - Location information
  - Document type detection

**Impact:** 
- Can now handle complex real estate disclosure forms
- Improved text extraction quality
- Better support for forms with checkboxes and overlapping elements
- More robust parsing with multiple fallback strategies

---

## Technical Details

### New Dependency
```
pdfminer.six==20221105
```

Install with:
```bash
pip install pdfminer.six==20221105
```

### Testing the Fixes

**Test Issue #1 (Settings terminology):**
1. Go to Settings page
2. Update any preference
3. Click "Save Settings"
4. Verify the success message says "Settings saved successfully!" (not "Profile")

**Test Issue #2 (PDF parsing):**
```bash
python test_pdf_parser.py
```

Should show:
- ✓ Text extracted: 50,000+ characters
- ✓ Document type detected: seller_disclosure
- ✓ All validation checks PASS

---

## Upgrade Instructions

### From V3.3.3 to V3.3.4

1. **Extract the new tar file:**
   ```bash
   tar -xzf offerwise_render_v3_3_4.tar.gz
   cd offerwise_render
   ```

2. **Install new dependency:**
   ```bash
   pip install pdfminer.six==20221105
   ```

3. **Test the changes:**
   ```bash
   # Test PDF parsing
   python test_pdf_parser.py
   
   # Test the application
   python app.py
   ```

4. **Deploy (if using Render/Vercel):**
   - Commit and push changes
   - Platform will auto-detect requirements.txt update
   - Deploy will install pdfminer.six automatically

---

## Backwards Compatibility

✅ **Fully backwards compatible** with V3.3.3
- All existing functionality preserved
- No breaking changes
- Only additions and bug fixes
- Database schema unchanged
- API endpoints unchanged

---

## Known Issues

None identified in this release.

---

## Next Steps

After upgrading:
1. Verify settings page shows "Settings saved" messages
2. Upload a complex PDF (like Pendleton_Disclosures.pdf) to test parsing
3. Monitor logs for any PDF extraction errors
4. Report any issues via GitHub or support channels

---

## Contributors

- Bug Report: User (identified terminology inconsistency and PDF parsing issues)
- Fix Implementation: Claude/OfferWise Team
- Testing: Automated + Manual validation with real disclosure documents

---

**Version:** 3.3.4  
**Previous Version:** 3.3.3  
**Release Type:** Bug Fix Release  
**Status:** Production Ready ✅
