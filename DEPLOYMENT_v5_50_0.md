# OfferWise v5.50.0 - Credibility Architecture Phase 1

## Release Date: January 26, 2026

## Summary

**CRITICAL BUG FIXES + CREDIBILITY FEATURES:**
1. ✅ Analysis deletion - Fixed 404 errors (ID mismatch)
2. ✅ Savings display - Fixed $0 showing in share modal
3. 🆕 **Credibility Phase 1** - Show confidence, evidence strength, and limitations

---

## NEW: Credibility Features

### 1. Confidence Badge on Recommended Offer
Users now see a confidence indicator (🟢 HIGH / 🟡 MODERATE / 🔴 LOW) directly on the recommended offer, showing how confident we are in the analysis.

### 2. Evidence Strength Badges on Red Flags
Each red flag now shows:
- 🟢 **VERIFIED** - Multiple pieces of evidence, detailed citations
- 🟡 **CITED** - Has evidence, single source
- 🔴 **INFERRED** - Pattern-detected, no direct quote

### 3. Credibility Summary Section
New section at bottom of offer breakdown showing:
- Confidence progress bar
- What we verified (cost benchmarks, cross-references)
- Known limitations (inspector qualifications, local pricing)
- Recommendation to get contractor quotes

---

## Bug Fixes

### Bug #1: Delete Analysis 404 Errors
- Backend now returns actual database ID instead of timestamp
- Frontend normalizes ID types for compatibility

### Bug #2: Share Modal Shows $0 Savings
- Fixed extraction of `recommended_offer` from nested `offer_strategy`
- Added robust fallback extraction in frontend

---

## Files Changed

1. `app.py` - Backend ID fix + recommended_offer extraction
2. `static/app.html` - Credibility UI components
3. `static/settings.html` - Frontend type handling + savings extraction
4. `CREDIBILITY_ARCHITECTURE.md` - Full credibility roadmap (NEW)
5. `VERSION` - Updated to 5.50.0

## What's Next (Phase 2)

See `CREDIBILITY_ARCHITECTURE.md` for full roadmap:
- Page number citations in findings
- "Click to see source" functionality
- Cost validation visualization (benchmark comparison)
- Cross-reference comparison table

---

## Deployment

Standard deployment:
```bash
git add .
git commit -m "v5.50.0: Credibility Phase 1 - confidence badges, evidence strength"
git push origin main
```

## The Goal

*"OfferWise doesn't just tell you what to offer. It shows you exactly why."*

Every number should be:
- **Traceable** → Click to see source
- **Verifiable** → User can check themselves
- **Bounded** → Show ranges, not false precision
- **Honest** → Admit what we don't know
