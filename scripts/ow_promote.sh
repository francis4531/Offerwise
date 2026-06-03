#!/usr/bin/env bash
# OfferWise — promote the VERIFIED staging commit to PRODUCTION.
#
# Run this AFTER scripts/ow_deploy.sh has pushed to `staging` and you've
# eyeballed the staging URL. It fast-forwards `main` to the exact commit that
# is live on staging and pushes it — so prod ships the identical artifact you
# just verified, not a fresh re-deploy that could differ.
#
# Fast-forward ONLY: if `main` has diverged from `staging` (e.g. a hotfix was
# committed straight to main), this stops and tells you how to reconcile rather
# than silently creating a merge or clobbering history.
#
# Usage:
#   scripts/ow_promote.sh
# Env:
#   OW_REPO   persistent local working clone (default: ~/offerwise-deploy)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OW_REPO="${OW_REPO:-$HOME/offerwise-deploy}"

[ -d "$OW_REPO/.git" ] || {
  echo "✗ No clone at $OW_REPO — run scripts/ow_deploy.sh first (it sets up the clone)."
  exit 1
}

cd "$OW_REPO"
git fetch origin --prune

git ls-remote --exit-code --heads origin staging >/dev/null 2>&1 || {
  echo "✗ No 'staging' branch on origin yet — deploy to staging first (scripts/ow_deploy.sh)."
  exit 1
}

STAGING_SHA="$(git rev-parse origin/staging)"
echo "→ Promoting staging commit ${STAGING_SHA:0:12} to production (main)…"

# Get onto a local main that tracks origin/main.
if git show-ref --verify --quiet refs/heads/main; then
  git checkout main
else
  git checkout -B main origin/main
fi
git pull --ff-only origin main 2>/dev/null || true

# Fast-forward main → staging. --ff-only fails loudly if main has commits that
# staging doesn't (divergence), instead of merging or rewriting.
if ! git merge --ff-only origin/staging; then
  echo ""
  echo "✗ main has diverged from staging — cannot fast-forward."
  echo "  Something landed on main that isn't on staging (likely a direct hotfix)."
  echo "  Reconcile by getting that change onto staging first, e.g.:"
  echo "      cd \"$OW_REPO\""
  echo "      git checkout staging && git merge main   # bring the hotfix into staging"
  echo "      git push origin staging                  # re-verify on staging, then re-run this"
  exit 1
fi

git push origin main
echo "✓ Promoted ${STAGING_SHA:0:12} to production — Render prod (offerwise) will deploy."
echo ""
echo "  Rollback if needed: Render dashboard → offerwise → Deploys → pick the prior"
echo "  successful deploy → Rollback. (History is preserved, so rollback is available.)"
echo ""
VER="$(cat VERSION 2>/dev/null || echo '?')"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  DEPLOYMENT  v$VER"
echo "║  ENVIRONMENT  ▶▶  PRODUCTION   (offerwise)"
echo "║  This is LIVE — real users are now being served this build."
echo "╚══════════════════════════════════════════════════════════════╝"
