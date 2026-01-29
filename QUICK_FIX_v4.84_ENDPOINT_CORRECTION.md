# 🔧 QUICK FIX v4.84: Wrong API Endpoint in Dashboard

**Date:** January 20, 2026  
**Version:** 4.84  
**Severity:** P2 - Minor (Non-blocking 404)  
**Impact:** Unnecessary 404 error in console

---

## 🐛 THE BUG

**User Screenshot Shows:**
```
❌ GET https://www.getofferwise.ai/api/buyer-profile 404 (Not Found)
```

**What's Happening:**
- Dashboard checks buyer preferences on load
- Calls `/api/buyer-profile` endpoint
- That endpoint doesn't exist!
- Should be `/api/user/preferences`

---

## 🔍 ROOT CAUSE

**dashboard.html, Line 1646:**
```javascript
// WRONG:
const prefResponse = await fetch('/api/buyer-profile', { credentials: 'include' });
```

**Why it's wrong:**
- We standardized on `/api/user/preferences` for preferences
- `/api/buyer-profile` was never implemented
- Old naming convention that wasn't updated

---

## ✅ THE FIX

**dashboard.html, Line 1646:**
```javascript
// FIXED:
const prefResponse = await fetch('/api/user/preferences', { credentials: 'include' });
```

**That's it!** One line changed.

---

## 📊 BEFORE vs AFTER

### **Before (v4.83):**
```
Dashboard loads
    ↓
Check preferences: GET /api/buyer-profile
    ↓
404 Not Found ❌
    ↓
Console error (but page still works)
```

### **After (v4.84):**
```
Dashboard loads
    ↓
Check preferences: GET /api/user/preferences
    ↓
200 OK ✅
    ↓
Clean console
```

---

## 🎯 IMPACT

**Functionality:** No change (error was non-blocking)

**Console:**
- Before: 404 error visible
- After: Clean, no errors

**User Experience:** No visible change

**Developer Experience:** Cleaner logs, easier debugging

---

## 📝 FILES MODIFIED

**static/dashboard.html**
- Line 1646: Changed `/api/buyer-profile` → `/api/user/preferences`
- **Total:** 1 line changed

---

## 🚀 DEPLOYMENT

```bash
# Quick deploy
tar -xzf offerwise_v4_84_ENDPOINT_FIX.tar.gz
cd offerwise_render
git add .
git commit -m "v4.84: Fix dashboard preferences endpoint (404)"
git push origin main
```

---

## ✅ TESTING

**Test:**
1. Hard refresh dashboard
2. Open console
3. Should see: `✅ Buyer preferences complete`
4. Should NOT see: `❌ GET /api/buyer-profile 404`

**Expected:**
✅ No 404 errors  
✅ Preferences load correctly  
✅ Clean console  

---

## 📊 CUMULATIVE FIXES

This package includes **ALL previous fixes:**

| Version | Fix | Status |
|---------|-----|--------|
| v4.80 | Missing /api/user/analyses endpoint | ✅ |
| v4.81 | Consent names in settings.html | ✅ |
| v4.82 | Preferences crash on None | ✅ |
| v4.83 | Reversed onboarding flow | ✅ |
| v4.84 | Wrong preferences endpoint | ✅ |

---

**VERSION: 4.84**  
**DATE: January 20, 2026**  
**STATUS: ✅ MINOR FIX - COMPLETE**

---

## 💬 SUMMARY

**What:** Dashboard called wrong preferences endpoint  
**Why:** Old naming convention `/api/buyer-profile` not updated  
**How:** Changed to `/api/user/preferences`  
**Result:** Clean console, no 404 errors  

**One line fix for a cleaner experience!** 🔧✅
