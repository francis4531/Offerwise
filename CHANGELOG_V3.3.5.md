# OfferWise V3.3.5 - File Size Limit Update

**Release Date:** January 5, 2026

## Changes

### Enhanced File Upload Capacity ✅

**Issue:** File size limit was too restrictive for large real estate disclosure packages.

**Previous Limits:**
- Backend: 50 MB
- Frontend: 4.5 MB (Vercel-specific)
- Real-world need: 100+ MB for comprehensive packages

**New Limits:**
- Backend: **100 MB** ✅
- Frontend: **100 MB** ✅
- Accommodates: Most real estate documents including comprehensive disclosure packages

---

## Files Changed

### 1. `app.py` (2 changes)

**Line 75:** Increased upload limit
```python
# Before
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max

# After
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max
```

**Added:** Error handler for file size exceeded (413)
```python
@app.errorhandler(413)
def request_entity_too_large(error):
    """Handle file size limit exceeded"""
    max_size_mb = app.config['MAX_CONTENT_LENGTH'] / (1024 * 1024)
    return jsonify({
        'success': False,
        'error': f'File too large. Maximum file size is {max_size_mb:.0f} MB.',
        'max_size_mb': max_size_mb,
        'suggestion': 'Please upload a smaller file or contact support...'
    }), 413
```

### 2. `static/app.html`

**Line 268:** Updated frontend validation
```javascript
// Before
const maxSize = 4.5 * 1024 * 1024; // 4.5MB Vercel limit

// After
const maxSize = 100 * 1024 * 1024; // 100MB
```

**Line 270:** Improved error message
```javascript
// Before
throw new Error('File too large. Maximum 4.5MB...');

// After
throw new Error(`File too large. Maximum file size is 100 MB. Your file is ${(file.size / 1024 / 1024).toFixed(1)} MB.`);
```

### 3. `FILE_SIZE_CONFIG.md` (NEW)

Comprehensive configuration guide covering:
- How to adjust limits
- Server resource requirements
- Platform-specific considerations
- Processing time estimates
- Best practices
- Troubleshooting

### 4. `VERSION`

Updated from 3.3.4 → 3.3.5

---

## What Can You Upload Now?

### ✅ Now Supported (up to 100 MB)

| Document Type | Example Size | Status |
|--------------|--------------|--------|
| Simple seller disclosure | 1-5 MB | ✅ |
| Standard inspection report | 10-20 MB | ✅ |
| Comprehensive inspection (with photos) | 30-50 MB | ✅ |
| Complete disclosure package | 50-100 MB | ✅ |
| Title documents | 10-30 MB | ✅ |
| HOA CC&Rs | 20-40 MB | ✅ |
| Combined documents | up to 100 MB | ✅ |

### ⚠️ May Need Splitting (>100 MB)

| Document Type | Typical Size | Recommendation |
|--------------|--------------|----------------|
| Scanned mega-packages | 100-300 MB | Split into separate docs |
| Commercial property docs | 150+ MB | Upload individually |
| Multiple combined PDFs | 200+ MB | Upload one at a time |

---

## Server Resource Requirements

### Memory Needed

With 100 MB limit, your server should have:
- **Minimum:** 512 MB RAM
- **Recommended:** 1 GB RAM
- **Optimal:** 2 GB RAM

### Processing Time

| File Size | Expected Processing |
|-----------|---------------------|
| 1-10 MB | 5-10 seconds |
| 10-50 MB | 15-30 seconds |
| 50-100 MB | 30-60 seconds |

---

## Platform Compatibility

### ✅ Render.com
- **Free Tier** (512 MB RAM): Works, but may be slow for 100 MB files
- **Starter** (512 MB): Recommended for 50-100 MB
- **Standard** (2 GB): Ideal for 100 MB+ files

### ⚠️ Vercel
- **Hobby Plan:** 4.5 MB limit (not suitable)
- **Pro Plan:** 100 MB limit (compatible)
- **Note:** Consider Render for file-heavy workloads

### ✅ AWS/Heroku
- Compatible with proper instance sizing
- Ensure request timeout > 60 seconds

---

## User Experience Improvements

### Better Error Messages

**Before:**
```
"File too large. Maximum 4.5MB"
```

**After:**
```
"File too large. Maximum file size is 100 MB. Your file is 125.3 MB."
```

Users now see:
- ✅ The limit (100 MB)
- ✅ Their actual file size
- ✅ How much they need to reduce

### Graceful Handling

The app now returns proper HTTP 413 responses with:
- Clear error message
- Suggested actions
- Maximum allowed size

---

## Configuration

### To Adjust the Limit

See `FILE_SIZE_CONFIG.md` for complete guide.

**Quick change:**
```python
# In app.py line 75
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200MB

# Also update in static/app.html line 268
const maxSize = 200 * 1024 * 1024;
```

**Remember:** Backend AND frontend must match!

---

## Testing

### Test File Size Handling

```bash
# Create test file of specific size
dd if=/dev/zero of=test_50mb.pdf bs=1M count=50
dd if=/dev/zero of=test_100mb.pdf bs=1M count=100
dd if=/dev/zero of=test_150mb.pdf bs=1M count=150

# Test uploads
python test_file_upload.py test_50mb.pdf   # Should succeed
python test_file_upload.py test_100mb.pdf  # Should succeed
python test_file_upload.py test_150mb.pdf  # Should fail with 413
```

### Verify Error Handling

1. Try uploading a file >100 MB
2. Should see friendly error: "File too large. Maximum file size is 100 MB..."
3. Should NOT crash or hang

---

## Upgrade Instructions

### From V3.3.4 to V3.3.5

1. **Extract new version:**
   ```bash
   tar -xzf offerwise_render_v3_3_5_FILE_SIZE.tar.gz
   cd offerwise_render
   ```

2. **No new dependencies required** (uses existing packages)

3. **Restart application:**
   ```bash
   # Local
   python app.py
   
   # Production
   git commit -am "v3.3.5: Increase file size limit to 100MB"
   git push
   ```

4. **Test with large file** to verify

---

## Backwards Compatibility

✅ **100% compatible with V3.3.4**
- No database changes
- No API changes
- No breaking changes
- Only increased limits

**Note:** Files uploaded under old 50 MB limit will continue to work normally.

---

## Known Limitations

1. **Processing large files (80-100 MB) may take 45-60 seconds**
   - Consider adding progress indicator in future release

2. **Render Free Tier (512 MB RAM) may struggle with 100 MB files**
   - Recommend upgrading to Starter or higher for consistent performance

3. **No chunked upload yet**
   - Entire file must upload before processing begins
   - May timeout on very slow connections

---

## Future Enhancements (Not in This Release)

- Chunked upload for files >50 MB
- Progress indicator for large files
- Background processing queue
- Async file processing
- Client-side PDF compression

---

## Summary

### Before V3.3.5
- ❌ 50 MB limit was too small
- ❌ 4.5 MB frontend limit was confusing
- ❌ No friendly error messages
- ❌ Couldn't handle comprehensive packages

### After V3.3.5
- ✅ 100 MB limit accommodates most documents
- ✅ Frontend and backend limits match
- ✅ Clear error messages with file sizes
- ✅ Proper 413 error handling
- ✅ Comprehensive configuration guide

---

**Version:** 3.3.5  
**Previous Version:** 3.3.4  
**Release Type:** Enhancement  
**Status:** Production Ready ✅

**Questions?** See `FILE_SIZE_CONFIG.md` for detailed configuration options.
