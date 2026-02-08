# v4.5.6 DEBUGGING GUIDE - Still Crashing
## Find Out What's Using All the Memory

---

## 🚨 WHAT v4.5.6 ADDS

### **1. Removed PaddleOCR (HUGE Memory Saver!)**
**PaddleOCR was commented out but still installed - used ~150-200MB!**

Before:
```
paddleocr==2.7.3         # Installs 150MB+ ML models
paddlepaddle==2.6.2      # Another 100MB+
```

After:
```
# paddleocr - DISABLED (saves 200-300MB!)
```

**Expected savings:** 200-300MB!

---

### **2. Memory Monitoring Endpoint**
**See EXACTLY what's using memory in real-time!**

Visit while logged in:
```
https://www.getofferwise.ai/api/debug/memory
```

Returns:
```json
{
  "memory": {
    "rss_mb": 458.23,          // ← ACTUAL RAM USED
    "limit_mb": 512,            // ← YOUR LIMIT
    "percent": 89.5             // ← DANGER ZONE!
  },
  "warning": "CRASH LIKELY!"    // ← IF > 450MB
}
```

---

### **3. Startup Memory Logging**
**See memory usage when app starts**

Check Render logs for:
```
📊 Startup memory usage: 456 MB (Limit: 512 MB)
⚠️ HIGH startup memory! Crashes likely!
```

---

## 🔍 DEBUGGING STEPS

### **STEP 1: Deploy v4.5.6 First**

```bash
cd ~/offerwise_render
tar -xzf offerwise_render_v4_5_6_DEBUG.tar.gz --strip-components=1

git add .
git commit -m "v4.5.6: Remove PaddleOCR + debug tools"
git push origin main
```

**Wait 3 minutes for deploy.**

---

### **STEP 2: Check Startup Memory**

**Go to Render dashboard → Logs**

**Look for this line:**
```
📊 Startup memory usage: XXX MB (Limit: 512 MB)
```

**Interpret:**
- **< 200 MB** ✅ Good! Plenty of room
- **200-350 MB** ⚠️ Tight but OK
- **350-450 MB** ⚠️⚠️ Very tight, crashes likely under load
- **> 450 MB** ❌ Will crash immediately

**Send me this number!**

---

### **STEP 3: Check Real-Time Memory**

**While app is running, visit:**
```
https://www.getofferwise.ai/api/debug/memory
```

**Look at `rss_mb` (actual memory used):**
- **< 300 MB** ✅ Safe
- **300-400 MB** ⚠️ OK but tight
- **400-450 MB** ⚠️⚠️ Danger zone
- **> 450 MB** ❌ About to crash!

**Send me the JSON output!**

---

### **STEP 4: Test Upload**

1. **Check memory BEFORE upload:**
   ```
   Visit /api/debug/memory
   Note the rss_mb value
   ```

2. **Upload a PDF**

3. **Check memory DURING processing:**
   ```
   Visit /api/debug/memory
   Note the rss_mb value
   ```

4. **Check memory AFTER processing:**
   ```
   Visit /api/debug/memory after PDF completes
   Note the rss_mb value
   ```

**Send me all 3 numbers!**

Example:
```
Before upload: 320 MB
During upload: 410 MB  (+90 MB)
After upload:  330 MB  (cleaned up to +10 MB)
```

---

### **STEP 5: Check What Render Says**

**In Render dashboard:**

1. Go to your service
2. Click "Metrics" tab
3. Look at "Memory Usage" graph

**Take screenshot and send to me!**

---

## 🎯 WHAT TO SEND ME

**I need these 5 things:**

1. **Startup memory from logs:**
   ```
   📊 Startup memory usage: XXX MB
   ```

2. **Memory endpoint output:**
   ```json
   {whole JSON from /api/debug/memory}
   ```

3. **Memory before/during/after upload:**
   ```
   Before: XXX MB
   During: XXX MB
   After: XXX MB
   ```

4. **Render metrics screenshot** (memory graph)

5. **Latest error message from logs** (if it crashed)

**With this info, I can tell you EXACTLY what's wrong!**

---

## 🔧 LIKELY CAUSES

### **Cause 1: Base App Too Large** 

**Symptoms:**
- Startup memory > 400MB
- Crashes immediately on deploy
- Never even handles first request

**Solutions:**
- Remove more dependencies
- Use lighter alternatives
- Upgrade to paid plan

---

### **Cause 2: Memory Leak**

**Symptoms:**
- Startup memory OK (~250MB)
- Grows over time
- After upload: 320 → 380 → 450 → CRASH

**Solutions:**
- More aggressive garbage collection
- Fix the leak (I need debug info to find it)

---

### **Cause 3: PDF Processing Spikes**

**Symptoms:**
- Startup memory OK
- Crashes during uploads
- Memory during upload > 500MB

**Solutions:**
- Reduce to 1 worker (set PDF_WORKER_THREADS=1)
- Process smaller chunks
- Upgrade plan

---

### **Cause 4: Dependencies Too Heavy**

**Symptoms:**
- Startup memory high (400MB+)
- Haven't changed anything
- Just too many packages

**Solutions:**
- Remove unused packages
- Use lighter alternatives (done in v4.5.6)
- Upgrade plan

---

## 🚀 EMERGENCY FIXES

### **Fix 1: Reduce to 1 Worker**

**In Render dashboard:**
```
Environment → Add Variable
Name: PDF_WORKER_THREADS
Value: 1
```

**Saves:** ~20-30MB

---

### **Fix 2: Increase Cleanup Frequency**

**Edit app.py line 1058:**
```python
time.sleep(600)  # Clean up every 10 minutes (was 30)
```

---

### **Fix 3: Clear Jobs Immediately**

**Edit app.py line 1061:**
```python
job_manager.cleanup_old_jobs(hours=0.5)  # 30 minutes (was 2 hours)
```

---

### **Fix 4: Disable Job Manager Entirely** 

**This is drastic but will save memory:**

Comment out in app.py:
```python
# cleanup_thread = threading.Thread(target=cleanup_old_jobs_periodically, daemon=True)
# cleanup_thread.start()
```

And clean up immediately after each job in pdf_worker.py line 64:
```python
self.job_manager.complete_job(job_id, result)
gc.collect()
# Clean up THIS job immediately
self.job_manager.jobs.pop(job_id, None)  # Remove from memory NOW
```

---

## 📊 DEPENDENCY SIZE ESTIMATES

**Large (>50MB each):**
- ~~paddleocr (150MB)~~ ← REMOVED in v4.5.6!
- ~~paddlepaddle (100MB)~~ ← REMOVED in v4.5.6!
- google-cloud-vision (30MB)
- anthropic (20MB)
- scikit-learn (50MB)

**Medium (10-50MB each):**
- Flask + dependencies (30MB)
- pdfplumber (15MB)
- pdf2image (20MB)
- Pillow (10MB)

**Small (<10MB each):**
- Everything else

**Total without Paddle:** ~200MB packages + 150MB Python = ~350MB base

---

## 🔍 WHAT I NEED TO SEE

**To diagnose your specific issue, I need:**

1. ✅ Startup memory (from logs)
2. ✅ Runtime memory (from /api/debug/memory)
3. ✅ Memory graph (from Render metrics)
4. ✅ Upload test results (before/during/after)
5. ✅ Crash logs (if any)

**Send me these and I'll know exactly what to fix!**

---

## 💡 NEXT STEPS

### **After Deploying v4.5.6:**

1. **Check startup memory in logs**
   - If < 350MB: Good start!
   - If > 400MB: Need to remove more stuff

2. **Visit /api/debug/memory endpoint**
   - Send me the JSON

3. **Upload a test PDF**
   - Watch memory before/during/after
   - Send me the numbers

4. **Take Render metrics screenshot**
   - Shows memory over time
   - Helps identify patterns

**With this data, I can give you a precise fix!**

---

## 🎯 WHAT v4.5.6 SHOULD FIX

**If PaddleOCR was the problem:**
- Startup memory should drop by 200-300MB
- App should be stable now
- You'll see in logs: "📊 Startup memory usage: 250 MB" (was 450+)

**If something else is the problem:**
- Startup memory still high (400MB+)
- Still crashes
- Need more debugging (send me the info above!)

---

## 🚀 TL;DR

**Deploy v4.5.6:**
```bash
tar -xzf v4_5_6.tar.gz && git push
```

**Check logs for:**
```
📊 Startup memory usage: XXX MB
```

**Visit:**
```
https://www.getofferwise.ai/api/debug/memory
```

**Send me:**
1. Startup memory number
2. Debug endpoint JSON
3. Render metrics screenshot

**I'll tell you exactly what to do next!** 🔍
