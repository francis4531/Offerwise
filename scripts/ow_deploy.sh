#!/usr/bin/env bash
# OfferWise — deploy a build to STAGING (history-preserving, no force-push).
#
# Replaces the old `git init && git push --force` flow, which wiped history on
# every deploy (so there was no rollback) and pushed straight to prod. This
# keeps one persistent clone with real history, commits normally, and pushes to
# the `staging` branch → Render's staging service deploys it. You verify on the
# staging URL, then run scripts/ow_promote.sh to ship the SAME commit to prod.
#
# Usage:
#   scripts/ow_deploy.sh [path-to-extracted-build]   # defaults to this build
# Env:
#   OW_REPO   persistent local working clone (default: ~/offerwise-deploy)
#   OW_GIT    git remote URL (default: the GitHub repo Render watches)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DEFAULT="$(dirname "$SCRIPT_DIR")"
_build_arg="${1:-$BUILD_DEFAULT}"
# Resolve to an ABSOLUTE path before we cd into the clone — otherwise a relative
# arg (e.g. "offerwise_render") breaks once the working dir changes.
BUILD="$(cd "$_build_arg" 2>/dev/null && pwd || true)"
[ -n "$BUILD" ] || { echo "✗ build dir '$_build_arg' not found"; exit 1; }
OW_REPO="${OW_REPO:-$HOME/offerwise-deploy}"
OW_GIT="${OW_GIT:-https://github.com/francis4531/Offerwise.git}"

[ -f "$BUILD/VERSION" ] || { echo "✗ '$BUILD' has no VERSION file — not a build dir."; exit 1; }
VER="$(cat "$BUILD/VERSION")"

if [ ! -d "$OW_REPO/.git" ]; then
  echo "→ First run: cloning $OW_GIT into $OW_REPO (keeps history → enables rollback)"
  git clone "$OW_GIT" "$OW_REPO"
fi

cd "$OW_REPO"
git fetch origin --prune

# Get onto the staging branch (create from main the first time).
if git show-ref --verify --quiet refs/heads/staging; then
  git checkout staging
elif git ls-remote --exit-code --heads origin staging >/dev/null 2>&1; then
  git checkout -B staging origin/staging
else
  echo "→ No staging branch yet — creating it from main."
  git checkout -B staging origin/main
fi
git pull --ff-only origin staging 2>/dev/null || true

# Sync the new build over the working tree: --delete propagates removed files,
# --exclude keeps the real git history intact.
rsync -a --delete --exclude='.git/' "$BUILD"/ "$OW_REPO"/

git add -A
if git diff --cached --quiet; then
  echo "• No changes vs current staging — nothing to deploy."
  exit 0
fi
git commit -m "v$VER (staging)"
git push origin staging
echo "✓ Pushed v$VER to staging → Render staging service will deploy."
echo "  Verify on the staging URL, then:  scripts/ow_promote.sh"
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  DEPLOYMENT  v$VER"
echo "║  ENVIRONMENT  ▶▶  STAGING   (offerwise-staging)"
echo "║  URL          https://offerwise-staging.onrender.com"
echo "║  NOT in production — promote with scripts/ow_promote.sh"
echo "╚══════════════════════════════════════════════════════════════╝"
