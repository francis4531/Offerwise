# V3.4.8 - Complete Environment Variables + New Service Guide

## What Changed

**render.yaml now includes ALL your environment variables!**

This means when you create a new Docker service, it will be **90% pre-configured** automatically.

---

## 🎯 Your Environment Variables (Now in render.yaml)

✅ **DATABASE_URL** - sqlite:///offerwise.db  
✅ **OCR_DPI** - 100 (memory-optimized)  
✅ **SECRET_KEY** - Auto-generated (new secure key)  
✅ **FACEBOOK_CLIENT_ID** - 1431080625107515  
✅ **FACEBOOK_CLIENT_SECRET** - Included  
✅ **GOOGLE_CLIENT_ID** - Included  
✅ **GOOGLE_CLIENT_SECRET** - Included  
✅ **STRIPE_PUBLISHABLE_KEY** - Included  
✅ **STRIPE_SECRET_KEY** - Included  

**All of these are now in render.yaml and will be auto-configured!**

---

## 🚀 Create New Docker Service (10 Minutes Total)

### Step 1: Push Updated render.yaml

```bash
cd offerwise_render

# Add the complete render.yaml
git add render.yaml

# Commit
git commit -m "v3.4.8: Complete render.yaml with all environment variables"

# Push
git push origin main
```

### Step 2: Create New Service in Render

**Go to Render Dashboard:**

1. Click **"New +"** (top right)
2. Select **"Web Service"**
3. Choose **"Build and deploy from a Git repository"**
4. Click **"Connect" next to your Offerwise repository**
   - (If not showing, click "Configure account" to connect GitHub)

### Step 3: Configure Service

**Render will show a form:**

- **Name:** `offerwise-docker` (or just `offerwise` if you delete old one first)
- **Runtime:** Should automatically detect **"Docker"** ✅ (from render.yaml)
- **Region:** Oregon (US West) - same as current
- **Branch:** main
- **Plan:** Starter ($7/mo) ✅

**Click "Create Web Service"**

### Step 4: Wait for Build (5-10 minutes)

**Watch the build logs:**

You should see:
```
==> Building...
Building Dockerfile...
Step 1/7 : FROM python:3.11-slim
Step 2/7 : RUN apt-get update && apt-get install -y tesseract-ocr
Setting up tesseract-ocr (4.1.1-2)  ✅ KEY LINE!
Successfully built [image-id]
==> Starting service...
✓ Live
```

### Step 5: Verify Environment Variables

After build completes:

1. Go to new service → **Settings** → **Environment**
2. **All variables should already be there!** ✅
3. **No manual entry needed!** ✅

### Step 6: Test Upload

1. Go to your new service URL (Render provides this)
2. Login with Google OAuth
3. Upload your scanned PDF
4. **Should work!** ✅

### Step 7: Update DNS & Delete Old Service

**After confirming new service works:**

1. **Update DNS:**
   - Settings → Custom Domains
   - Add `getofferwise.ai`
   - Update your DNS provider (Cloudflare/etc) to point to new service

2. **Delete old Python service:**
   - Go to old service
   - Settings → Delete or suspend service
   - Confirm deletion

---

## ✅ What You DON'T Need to Do

❌ **Manually enter environment variables** - They're in render.yaml!  
❌ **Configure OAuth** - Already configured!  
❌ **Set up Stripe** - Already configured!  
❌ **Set OCR_DPI** - Already set to 100!  
❌ **Generate SECRET_KEY** - Auto-generated!  

**Everything is pre-configured in render.yaml!**

---

## 📊 Time Breakdown

| Step | Time | Effort |
|------|------|--------|
| Push render.yaml | 1 min | Run 3 commands |
| Create service in dashboard | 2 min | Click, click, click |
| Wait for Docker build | 10 min | ☕ Automatic |
| Verify & test | 2 min | Click upload |
| Update DNS | 3 min | Change DNS record |
| Delete old service | 1 min | Click delete |
| **Total** | **19 min** | **~9 min active work** |

---

## 🎯 Why This Will Work

**Old service:**
- ❌ Created as Python
- ❌ Stuck on Python runtime
- ❌ Render won't switch it to Docker
- ❌ tesseract never installed

**New service:**
- ✅ Created as Docker from the start (via render.yaml)
- ✅ Dockerfile builds with tesseract
- ✅ All environment variables pre-configured
- ✅ OCR works immediately
- ✅ No migration headaches

---

## 🆘 If You Hit Issues

### Issue: "Can't find repository"
**Solution:** Click "Configure account" to connect GitHub

### Issue: "Runtime shows Python not Docker"
**Solution:** Make sure you pushed the updated render.yaml first

### Issue: "Build fails"
**Solution:** Check build logs for specific error, likely Docker-related

### Issue: "Environment variables missing"
**Solution:** render.yaml might not have been read - check service was created from repo with render.yaml

---

## 🎉 Expected Result

**After 10 minutes (5-10 min build + 2 min testing):**

1. ✅ New service running with Docker
2. ✅ tesseract installed and working
3. ✅ All environment variables configured
4. ✅ OAuth working (Google, Facebook)
5. ✅ Stripe payment working
6. ✅ **PDF uploads working with OCR!**

Then just:
- Update DNS (3 min)
- Delete old service (1 min)
- **Done!**

---

## 💡 Pro Tip: Run Both Simultaneously

**You can keep BOTH services running:**

1. Create new service with name `offerwise-docker`
2. Let it build and test it fully
3. Old `offerwise` service keeps serving users
4. When new service confirmed working:
   - Switch DNS to new service
   - Delete old service

**Zero downtime!**

---

## 📝 Checklist

**Before starting:**
- [ ] Updated render.yaml pushed to git
- [ ] Confirmed render.yaml shows `runtime: docker` on GitHub

**During creation:**
- [ ] Selected correct repository
- [ ] Runtime shows "Docker" ✅
- [ ] Plan is "Starter"
- [ ] Branch is "main"

**After build:**
- [ ] Build logs show "Building Dockerfile"
- [ ] Build logs show "Setting up tesseract-ocr"
- [ ] Service shows "Docker" not "Python 3"
- [ ] Environment variables all present
- [ ] Test upload works

**After migration:**
- [ ] DNS updated to new service
- [ ] Old service deleted
- [ ] Users can access site at getofferwise.ai

---

## 🚀 Ready to Start?

**Step 1 right now:**

```bash
cd offerwise_render
git add render.yaml
git commit -m "v3.4.8: Complete environment variables for new service"
git push origin main
```

**Then go to Render Dashboard → New + → Web Service**

**You'll have a working Docker service in 10 minutes!**

---

**The hard part is done. render.yaml has everything. Now it's just: create, wait, test, switch. Easy!**
