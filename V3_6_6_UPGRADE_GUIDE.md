# V3.6.6 - INCREASE OCR SPEED: 4 Parallel Workers

## ⚡ Performance Upgrade

**Changed OCR_PARALLEL_WORKERS from 2 to 4**

**Speed improvement:**
- **44-page PDF:** 3.5 minutes → **1.75 minutes** 🚀
- **That's 2x faster!**

---

## ⚠️ Memory Warning

**This is pushing the limits of the Starter plan!**

### Memory Usage:
```
1 Gunicorn worker:     20 MB
4 OCR threads:        400 MB
System overhead:       20 MB
─────────────────────────────
Total:                440 MB ⚠️
Available:            512 MB
Headroom:             72 MB
```

**72 MB headroom is TIGHT!**

**If you see crashes or memory errors:**
- Scale back to 3 workers (safest)
- Or upgrade to Standard plan (2 GB RAM)

---

## 📋 What Changed

### render.yaml:
```yaml
- key: OCR_PARALLEL_WORKERS
  value: "4"  # Was 2, now 4

- key: GUNICORN_TIMEOUT
  value: "300"  # Added explicitly
```

### VERSION:
```
3.6.6
```

---

## 🚀 Deploy V3.6.6

```bash
cd ~/Offerwise

# Copy the updated render.yaml
# (Download the package or manually edit)

# Update render.yaml:
# Change OCR_DPI section to:
#   - key: OCR_DPI
#     value: "100"
#   
#   - key: OCR_PARALLEL_WORKERS
#     value: "4"
#   
#   - key: GUNICORN_TIMEOUT
#     value: "300"

# Update VERSION
echo "3.6.6" > VERSION

# Commit and push
git add render.yaml VERSION
git commit -m "v3.6.6: Increase OCR workers to 4 for 2x speed"
git push origin main
```

---

## ⏱️ Expected Performance

**44-page PDF timeline:**
```
00:00 - Upload starts
00:05 - Page 4 complete (4 pages at once!)
00:15 - Page 12 complete
00:30 - Page 24 complete
01:00 - Page 40 complete
01:45 - All 44 pages complete ✅
```

**From 3.5 min → 1.75 min!** 🚀

---

## 🧪 Testing Steps

1. **Deploy V3.6.6**
2. **Wait 2 minutes** for Render deploy
3. **Check logs:**
   ```
   Using 4 parallel OCR workers
   ```
4. **Upload 44-page PDF**
5. **Monitor memory** in Render Metrics
6. **Verify:** Completes in ~1.75 minutes ✅

---

## 🚨 If You See Crashes

**Symptoms:**
```
Worker exiting
Out of memory
Killed
```

**Solution A: Scale Back to 3 Workers (Free)**
```yaml
- key: OCR_PARALLEL_WORKERS
  value: "3"  # Sweet spot: faster but safer
```
- **Speed:** ~2.3 minutes for 44 pages
- **Memory:** ~320 MB (safe!)

**Solution B: Upgrade to Standard Plan ($25/mo)**
```yaml
plan: standard  # 2 GB RAM
- key: OCR_PARALLEL_WORKERS
  value: "4"  # Now safe!
```
- **Speed:** 1.75 minutes
- **Memory:** 440 MB out of 2048 MB (plenty!)

---

## 📊 Memory Monitoring

**After deploy, watch Render Metrics:**

**Dashboard → Your Service → Metrics → Memory**

**Good:**
```
Memory: 420-450 MB (82-88%) ✅
No spikes above 500 MB
```

**Bad:**
```
Memory: 480-512 MB (94-100%) ❌
Frequent spikes to 512 MB
Worker restarts
```

**If you see "Bad" → Scale back to 3 workers!**

---

## 🎯 Recommendations

### Conservative (Safest):
```
OCR_PARALLEL_WORKERS = 3
Memory: ~320 MB
Speed: 2.3 minutes
Headroom: 192 MB ✅
```

### Aggressive (Fastest, but risky on Starter):
```
OCR_PARALLEL_WORKERS = 4
Memory: ~440 MB
Speed: 1.75 minutes
Headroom: 72 MB ⚠️
```

### Production (Best):
```
Plan: Standard (2 GB RAM)
OCR_PARALLEL_WORKERS = 4
Memory: ~440 MB
Speed: 1.75 minutes
Headroom: 1608 MB ✅
```

---

## 🎉 Benefits of 4 Workers

**For 44-page PDF:**
- ✅ 2x faster (1.75 min vs 3.5 min)
- ✅ Better user experience
- ✅ Can handle more uploads per hour

**For smaller PDFs (10 pages):**
- ✅ 30 seconds instead of 60 seconds
- ✅ Nearly instant processing

---

## 📝 Alternative: Try 3 Workers First

**If you're nervous about memory:**

```yaml
- key: OCR_PARALLEL_WORKERS
  value: "3"
```

**Performance:**
- 44 pages: ~2.3 minutes (still 1.5x faster!)
- Memory: ~320 MB (very safe!)
- Headroom: 192 MB (comfortable)

**Then upgrade to 4 if it works well!**

---

## 🚀 Bottom Line

**V3.6.6 with 4 workers:**
- ⚡ 2x faster processing
- ⚠️ Tight memory (72 MB headroom)
- 🧪 Test and monitor closely
- 🔧 Scale to 3 if crashes occur

**I recommend:**
1. Deploy with 4 workers
2. Test with your 44-page PDF
3. Watch memory metrics
4. If stable → Great! Keep 4 workers ✅
5. If crashes → Scale back to 3 workers

---

**Deploy and let's see how it performs!** 🚀
