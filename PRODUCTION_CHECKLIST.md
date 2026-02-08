# OfferWise Production Deployment Checklist
## Version 5.55.0 - Launch Ready

---

## ✅ REQUIRED ENVIRONMENT VARIABLES

Set these in Render Dashboard → Environment:

### Database
```
DATABASE_URL=postgresql://...  # Auto-set by Render PostgreSQL
```

### Authentication (OAuth)
```
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
FACEBOOK_CLIENT_ID=your_facebook_app_id
FACEBOOK_CLIENT_SECRET=your_facebook_app_secret
APPLE_CLIENT_ID=your_apple_service_id
APPLE_CLIENT_SECRET=your_apple_key
```

### Payments (Stripe)
```
STRIPE_SECRET_KEY=sk_live_...        # LIVE key (not test!)
STRIPE_PUBLISHABLE_KEY=pk_live_...   # LIVE key (not test!)
STRIPE_WEBHOOK_SECRET=whsec_...      # From Stripe webhook settings
```

### AI Services
```
ANTHROPIC_API_KEY=sk-ant-...         # For AI analysis features
GOOGLE_VISION_API_KEY=...            # For OCR (optional, has fallback)
```

### Security
```
SECRET_KEY=<random-64-char-string>   # Generate with: python -c "import secrets; print(secrets.token_hex(32))"
TURK_ADMIN_KEY=<your-admin-password> # For admin dashboard access
CRON_SECRET=<random-string>          # For scheduled jobs
```

### Optional
```
FLASK_ENV=production                 # Set to 'production' for live
ALLOWED_ORIGIN=https://getofferwise.ai
```

---

## ✅ PRE-LAUNCH VERIFICATION

### 1. Stripe Configuration
- [ ] Stripe account fully verified (not restricted)
- [ ] Live API keys configured (sk_live_, pk_live_)
- [ ] Webhook endpoint configured: `https://getofferwise.ai/webhook/stripe`
- [ ] Webhook events enabled: `checkout.session.completed`, `payment_intent.succeeded`
- [ ] Test purchase completed with real card

### 2. OAuth Configuration
- [ ] Google OAuth: Authorized redirect URI set to `https://getofferwise.ai/auth/google/callback`
- [ ] Facebook OAuth: Valid OAuth redirect URI set
- [ ] Apple OAuth: Return URL configured (if enabled)

### 3. Domain & SSL
- [ ] Custom domain configured in Render
- [ ] SSL certificate active (auto-managed by Render)
- [ ] www redirect configured (if applicable)

### 4. Database
- [ ] PostgreSQL instance running
- [ ] Connection verified
- [ ] Backup strategy enabled

### 5. Monitoring
- [ ] Render logs accessible
- [ ] Error alerting configured (optional)

---

## ✅ SECURITY CHECKLIST

- [x] No hardcoded API keys in source code
- [x] CSRF protection on state-changing endpoints
- [x] Rate limiting on critical endpoints (analyze, checkout, delete)
- [x] Session cookies secure (HTTPS only, HttpOnly, SameSite)
- [x] Security headers (X-Frame-Options, X-Content-Type-Options, XSS-Protection)
- [x] Input validation on all user inputs
- [x] SQL injection prevention (using SQLAlchemy ORM)
- [x] Error messages don't expose internal details

---

## ✅ FUNCTIONAL CHECKLIST

### User Journey
- [ ] Homepage loads
- [ ] Login with Google works
- [ ] Dashboard loads after login
- [ ] Can purchase credits (Stripe checkout)
- [ ] Credits appear after purchase
- [ ] Can upload PDF documents
- [ ] Analysis completes successfully
- [ ] Results display correctly
- [ ] Can view analysis history
- [ ] Can delete analysis
- [ ] Logout works

### Features
- [ ] OfferScore™ displays correctly
- [ ] Property Risk DNA™ pentagon renders
- [ ] Seller Transparency Report™ shows data
- [ ] Negotiation Hub works (AI + templates)
- [ ] Print report generates PDF

### Legal
- [ ] Terms of Service accessible (/terms)
- [ ] Privacy Policy accessible (/privacy)
- [ ] Disclaimer accessible (/disclaimer)

---

## 🚀 LAUNCH COMMAND

```bash
# Deploy to Render
git add .
git commit -m "v5.55.0: Production launch ready"
git push origin main
```

Render will auto-deploy from main branch.

---

## 📊 POST-LAUNCH MONITORING

### First Hour
- Watch Render logs for errors
- Verify Stripe webhooks receiving
- Test one real purchase

### First Day
- Monitor error rates
- Check analysis completion rates
- Review any support emails

### First Week
- Analyze user drop-off points
- Review PMF survey responses
- Iterate based on feedback

---

## 🆘 EMERGENCY CONTACTS

- **Render Status**: https://status.render.com
- **Stripe Status**: https://status.stripe.com
- **Support Email**: support@getofferwise.ai

---

## 📝 NOTES

- Database migrations run automatically on startup
- Static files served from /static directory
- Gunicorn timeout: 300 seconds (for long analyses)
- Max file size: 100MB
