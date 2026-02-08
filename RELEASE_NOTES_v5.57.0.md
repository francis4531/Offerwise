# Release Notes v5.57.0 — Security Hardening

**Date:** February 6, 2026  
**Type:** Security patch — no feature changes

## Summary

Comprehensive security audit and remediation. All changes are backend-only — no UI changes, no database migrations required.

## ⚠️ REQUIRED: Environment Variables

After deploying this version, **verify these environment variables are set in Render**:

| Variable | Required? | Notes |
|----------|-----------|-------|
| `TURK_ADMIN_KEY` | **YES** | Admin endpoints will reject all requests if this is unset (previously defaulted to `offerwise-admin`) |
| `CRON_SECRET` | **YES** | Cron endpoint will reject requests if unset (previously defaulted to `offerwise-cron-secret`) |
| `SECRET_KEY` | **YES** | App will refuse to start in production if unset |
| `DEVELOPER_EMAILS` | Optional | Comma-separated list of developer emails for auto-enterprise (e.g., `francis@piotnetworks.com`). Previously hardcoded. |

## Changes

### Critical Fixes
- **C1/C2:** Removed hardcoded default admin key (`offerwise-admin`) and cron secret (`offerwise-cron-secret`). Both now require the environment variable to be explicitly set.
- **C3:** CORS now restricted to allowed origins only (`getofferwise.ai`, `offerwise.onrender.com`, `localhost`). Previously allowed all origins with credentials.
- **C4:** Developer email whitelist moved from hardcoded to `DEVELOPER_EMAILS` env var.
- **C5:** Stopped logging partial Anthropic API key characters.

### High-Priority Fixes
- **H1:** Turk testing endpoints (`/api/turk/*`) now require admin authentication.
- **H2:** `/api/system-info` now requires admin authentication.
- **H3:** `/api/admin/health-check` upgraded from login-only to admin-only.
- **H4:** `/api/cancel-ocr` now requires authentication; removed anonymous fallback.
- **H5:** Credit deduction is now atomic (prevents race condition double-deduction).
- **H6:** Raw Python exception messages no longer returned to clients; generic error messages shown instead.
- **H7:** `SECRET_KEY` is now enforced — app refuses to start in production without it.

### Medium-Priority Fixes
- **M1:** Added `.dockerignore` to exclude test/diagnostic files from Docker image.
- **M2:** Added rate limiting to survey endpoints (10/hour).
- **M4:** Added `Content-Security-Policy` header.
- **M5:** Strengthened CSRF protection — API requests without `Origin` header now require `X-Requested-With` header.
- **M7:** Excluded `checkout-config.html` from Docker builds.
- **M9:** `/api/worker/stats` upgraded from login-only to admin-only.

## Frontend Note

If you see 403 errors on API calls after deploying, your frontend AJAX/fetch calls to `/api/*` endpoints may need to include the `X-Requested-With: XMLHttpRequest` header (due to M5 CSRF strengthening). Add this to your fetch calls:

```javascript
fetch('/api/endpoint', {
    headers: {
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'application/json'
    }
})
```

Most modern frontends already send the `Origin` header, so this is only needed if you see issues.
