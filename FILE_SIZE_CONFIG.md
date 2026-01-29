# OfferWise File Upload Configuration Guide

## Current Settings

**Maximum Upload Size:** 100 MB (as of V3.3.4)

Location: `app.py` line 75

```python
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB
```

---

## Why 100 MB?

This limit accommodates:
- ✅ Standard seller disclosures (1-10 MB)
- ✅ Inspection reports with photos (10-40 MB)
- ✅ Comprehensive disclosure packages (40-100 MB)
- ✅ Most HOA documents (10-30 MB)

---

## Adjusting the Limit

### To Change the Limit

Edit line 75 in `app.py`:

```python
# 50 MB (conservative)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

# 100 MB (recommended)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

# 200 MB (generous)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024

# 500 MB (enterprise)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
```

### After Changing

1. **Restart the application:**
   ```bash
   # Local development
   # Stop the app (Ctrl+C) and restart
   python app.py
   
   # Render/production
   # Commit and push changes
   git add app.py
   git commit -m "Adjust file upload limit"
   git push
   ```

2. **Test with a large file** to verify the new limit works.

---

## Server Resource Requirements

### Memory Requirements

The server needs enough RAM to process uploaded files:

| File Size Limit | Recommended RAM | Minimum RAM |
|----------------|-----------------|-------------|
| 50 MB | 512 MB | 256 MB |
| 100 MB | 1 GB | 512 MB |
| 200 MB | 2 GB | 1 GB |
| 500 MB | 4 GB | 2 GB |

**Formula:** RAM needed ≈ 3-4x file size limit

### Processing Time Estimates

| File Size | Typical Processing Time |
|-----------|------------------------|
| 1-10 MB | 5-10 seconds |
| 10-50 MB | 15-30 seconds |
| 50-100 MB | 30-60 seconds |
| 100-200 MB | 1-2 minutes |
| 200+ MB | 2-5 minutes |

---

## Platform-Specific Limits

### Render.com

| Plan | RAM | Recommended Limit |
|------|-----|-------------------|
| Free | 512 MB | 50 MB |
| Starter | 512 MB | 50-100 MB |
| Standard | 2 GB | 200 MB |
| Pro | 4 GB | 500 MB |

### Vercel

⚠️ **Warning:** Vercel has strict limits:
- **Hobby Plan:** 4.5 MB body size limit
- **Pro Plan:** 100 MB body size limit
- Function timeout: 10s (Hobby), 60s (Pro)

For large files, Vercel is not recommended. Use Render or AWS instead.

### AWS/Heroku

These platforms handle large files well but may have:
- Request timeout limits (30-120 seconds)
- Load balancer limits (varies)
- Check your specific plan

---

## User Experience Considerations

### Upload Speed

User's upload time depends on internet speed:

| Connection | 50 MB Upload | 100 MB Upload | 200 MB Upload |
|------------|--------------|---------------|---------------|
| Slow (1 Mbps) | 7 minutes | 13 minutes | 27 minutes |
| Average (10 Mbps) | 40 seconds | 80 seconds | 2.7 minutes |
| Fast (100 Mbps) | 4 seconds | 8 seconds | 16 seconds |

**Recommendation:** 
- For 100+ MB limits, add progress bars
- Show estimated time remaining
- Allow background uploads

### Error Handling

The app now returns a friendly error message when files exceed the limit:

```json
{
  "success": false,
  "error": "File too large. Maximum file size is 100 MB.",
  "max_size_mb": 100,
  "suggestion": "Please upload a smaller file or contact support..."
}
```

---

## Frontend File Size Validation

To improve UX, add client-side validation before upload:

```javascript
// In your upload handler
const MAX_FILE_SIZE = 100 * 1024 * 1024; // 100 MB in bytes

function validateFile(file) {
    if (file.size > MAX_FILE_SIZE) {
        alert(`File too large! Maximum size is ${MAX_FILE_SIZE / 1024 / 1024} MB.\n` +
              `Your file is ${(file.size / 1024 / 1024).toFixed(1)} MB.`);
        return false;
    }
    return true;
}

// Usage
fileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (validateFile(file)) {
        // Proceed with upload
        uploadFile(file);
    }
});
```

This prevents users from waiting for upload only to get an error.

---

## Troubleshooting

### "File too large" Error

**Possible causes:**
1. File exceeds `MAX_CONTENT_LENGTH`
2. Reverse proxy (nginx/Apache) has smaller limit
3. Platform-specific restrictions (e.g., Vercel)

**Solutions:**
1. Increase `MAX_CONTENT_LENGTH` in `app.py`
2. Check nginx/Apache config if self-hosting
3. Use a platform that supports your file size needs

### Out of Memory Errors

**Symptoms:**
- App crashes during upload
- 500 errors for large files
- Slow performance

**Solutions:**
1. Reduce `MAX_CONTENT_LENGTH`
2. Upgrade to higher RAM plan
3. Optimize PDF parsing (stream instead of load full file)

### Timeout Errors

**Symptoms:**
- Upload succeeds but processing fails
- Gateway timeout (504)

**Solutions:**
1. Increase platform timeout settings
2. Implement async processing (upload → queue → process)
3. Break large documents into chunks

---

## Best Practices

### For Production

1. **Set realistic limits** based on your server resources
2. **Add client-side validation** to fail fast
3. **Show progress indicators** for uploads >10 MB
4. **Monitor server resources** and adjust limits accordingly
5. **Log large file uploads** to identify patterns

### For Development

1. Test with various file sizes (small, medium, large)
2. Test timeout scenarios
3. Test memory usage during processing
4. Verify error messages are user-friendly

### For Users

1. **Compress PDFs** before upload if possible
2. **Split large packages** into individual documents
3. **Use native PDFs** instead of scanned images (smaller)

---

## Monitoring

Track these metrics to optimize file size limits:

```python
# Add to your logging
logger.info(f"File uploaded: {filename}, Size: {file_size_mb:.2f} MB, Processing time: {duration:.2f}s")
```

Analyze:
- Average file size
- Max file size uploaded
- Processing time vs file size
- Failed uploads due to size

---

## Quick Reference

| Scenario | Recommended Limit |
|----------|-------------------|
| MVP/Testing | 50 MB |
| Production (Standard) | 100 MB |
| Enterprise/Commercial | 200-500 MB |
| Render Free Tier | 50 MB |
| Render Standard+ | 100-200 MB |
| Self-hosted (2GB+ RAM) | 200+ MB |

---

## Questions?

- Current limit: **100 MB** ✅
- Change in: `app.py` line 75
- Restart required: Yes
- Platform dependent: Yes (check your hosting plan)

**Remember:** More storage = More RAM needed = Higher costs
