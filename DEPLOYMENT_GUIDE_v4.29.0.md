# DEPLOYMENT GUIDE - v4.29.0
## Step-by-Step Instructions

**Time Required:** 15-20 minutes  
**Difficulty:** Easy  
**Risk:** Low (can rollback if needed)

---

## ⚡ **QUICK START**

If you just want to deploy NOW:

```bash
# 1. Set SECRET_KEY in Render (see below for value)
# 2. Update files:
cp app_with_auth_FIXED_v4.29.0.py app_with_auth.py

# 3. Update requirements.txt (add these lines):
echo "Flask-Limiter==3.5.0" >> requirements.txt

# 4. Deploy:
git add .
git commit -m "v4.29.0: All bugs fixed - Production ready"
git push origin main

# 5. Wait for Render to deploy (2-3 minutes)
# 6. Test /health endpoint
# 7. Done! ✅
```

---

## 📋 **DETAILED STEP-BY-STEP**

### **STEP 1: Generate SECRET_KEY (2 minutes)**

**On your local machine, run:**

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

**This will output something like:**
```
a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2
```

**Copy this value!** You'll need it in the next step.

---

### **STEP 2: Set Environment Variable in Render (3 minutes)**

1. Go to https://dashboard.render.com
2. Click on your OfferWise service
3. Click "Environment" in the left sidebar
4. Click "Add Environment Variable" button
5. Set:
   - **Key:** `SECRET_KEY`
   - **Value:** (paste the value from Step 1)
6. Click "Save Changes"

**⚠️ IMPORTANT:** Don't click "Deploy" yet! We'll do that after updating the code.

---

### **STEP 3: Update Your Local Files (2 minutes)**

**In your offerwise_render directory:**

```bash
# Backup current version (just in case)
cp app_with_auth.py app_with_auth_v4.28.0_backup.py

# Copy fixed version
cp app_with_auth_FIXED_v4.29.0.py app_with_auth.py

# offerwise_intelligence.py is already fixed (date bug)
```

---

### **STEP 4: Update requirements.txt (1 minute)**

**Add this line to your requirements.txt:**

```
Flask-Limiter==3.5.0
```

**Full requirements.txt should look like:**
```
Flask==3.0.0
Flask-CORS==4.0.0
Flask-Compress==1.14
Flask-Login==0.6.3
Flask-SQLAlchemy==3.1.1
Flask-Limiter==3.5.0
SQLAlchemy==2.0.23
psycopg2-binary==2.9.9
python-dotenv==1.0.0
anthropic==0.18.1
requests==2.31.0
PyPDF2==3.0.1
pdfplumber==0.10.3
Werkzeug==3.0.1
email-validator==2.1.0
MarkupSafe>=2.1.0
```

---

### **STEP 5: Commit and Push (2 minutes)**

```bash
git add .
git commit -m "v4.29.0: ALL BUGS FIXED - Production Ready

CRITICAL FIXES:
- Backend consent enforcement
- Price validation
- PDF error handling  
- File size limits
- Transaction rollback
- Required SECRET_KEY

HIGH PRIORITY FIXES:
- Rate limiting
- Input sanitization
- CORS configuration
- Comprehensive logging

See ALL_BUGS_FIXED_v4.29.0.md for complete details."

git push origin main
```

---

### **STEP 6: Monitor Deployment (3-5 minutes)**

1. Go to Render Dashboard
2. Watch the deployment logs
3. Look for:
   - ✅ "Build successful"
   - ✅ "Deploy successful"
   - ❌ Any errors (if so, check Step 7 below)

**Expected output:**
```
==> Building...
==> Installing dependencies from requirements.txt
==> Successfully installed Flask-Limiter-3.5.0
==> Build succeeded
==> Deploying...
==> Deploy succeeded
==> Your service is live at https://offerwise.com
```

---

### **STEP 7: Verify Deployment (5 minutes)**

**Test 1: Health Check**
```bash
curl https://offerwise.com/health
```

**Expected:**
```json
{
  "status": "healthy",
  "timestamp": "2026-01-16T...",
  "version": "4.29.0",
  "database": "healthy"
}
```

**Test 2: Can Access Site**
- Go to https://offerwise.com
- Page loads ✅

**Test 3: Can Login**
- Login with existing account
- Still logged in ✅

**Test 4: Consent Blocking Works**
- Go to Settings
- Make sure you're NOT consented
- Try to analyze a property
- Should be blocked with message ✅

**Test 5: Price Validation**
- Try to enter "abc" as price
- Should show error message (not crash) ✅

**Test 6: Session Persists**
- Log in
- Refresh page
- Still logged in ✅
- *This confirms SECRET_KEY is working!*

---

## 🐛 **TROUBLESHOOTING**

### **Error: "SECRET_KEY environment variable is required"**

**Cause:** SECRET_KEY not set in Render

**Fix:**
1. Go to Render Dashboard → Environment
2. Add SECRET_KEY (see Step 2)
3. Click "Save Changes"
4. Render will auto-redeploy

---

### **Error: "Module 'flask_limiter' not found"**

**Cause:** requirements.txt not updated

**Fix:**
1. Add `Flask-Limiter==3.5.0` to requirements.txt
2. Commit and push again
3. Wait for redeploy

---

### **Error: "Health check failed"**

**Cause:** Database connection issue

**Fix:**
1. Check DATABASE_URL is set in Render
2. Check database is running
3. Check Render logs for specific error

---

### **Build Fails**

**Cause:** Syntax error or missing dependency

**Fix:**
1. Check Render build logs for specific error
2. Fix the issue locally
3. Test locally: `python app_with_auth.py`
4. Commit and push again

---

### **Users Getting Logged Out**

**Cause:** SECRET_KEY not set or changing

**Fix:**
1. Verify SECRET_KEY is set in Render
2. Verify it's NOT being generated randomly
3. If recently added, users will be logged out ONCE (this is normal)
4. After that, they should stay logged in ✅

---

## ⏮️ **ROLLBACK PLAN**

If something goes wrong and you need to rollback:

```bash
# Restore previous version
cp app_with_auth_v4.28.0_backup.py app_with_auth.py

# Remove Flask-Limiter from requirements.txt
# (edit the file and remove the line)

# Deploy rollback
git add .
git commit -m "Rollback to v4.28.0"
git push origin main
```

**Note:** You'll need to keep SECRET_KEY set in Render even after rollback.

---

## ✅ **POST-DEPLOYMENT CHECKLIST**

After deployment is complete, verify:

```
□ /health returns {"status": "healthy"}
□ Can access homepage
□ Can register new user
□ Can login
□ Session persists across refreshes
□ Settings page loads
□ Can upload documents
□ Consent blocking works
□ Invalid price shows error (not crash)
□ Large files show error (not crash)
□ Can complete an analysis
□ Credits are charged correctly
□ Analysis date shows today
□ Can logout
□ Can login again
```

**If all ✅ → You're live!** 🚀

---

## 📊 **MONITORING**

### **What to Watch:**

1. **Error Rate**
   - Check Render logs for errors
   - Should be very low or zero

2. **Response Times**
   - Analysis should complete in <60s
   - API calls should be <1s

3. **Rate Limit Hits**
   - Check logs for 429 errors
   - Adjust limits if needed

4. **User Feedback**
   - Watch for confused users
   - Check error messages are helpful

---

## 🎉 **SUCCESS!**

If all tests pass, you have successfully deployed v4.29.0 with all bugs fixed!

**Your application is now:**
- ✅ Production-ready
- ✅ Legally compliant
- ✅ Secure
- ✅ Stable
- ✅ Monitorable

**Congratulations!** 🎊

---

## 📞 **NEED HELP?**

If you encounter any issues:

1. **Check Render logs** first
2. **Try the troubleshooting section** above
3. **Ask me specific questions** about the error
4. **Can always rollback** if needed

**I'm here to help!** Just tell me what error you're seeing.
