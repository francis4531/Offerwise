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

# ── Completeness guard (v5.89.190) ──────────────────────────────────────────
# The rsync below uses --delete: it mirrors $BUILD exactly, so ANY file missing
# from the build tree is deleted from the deploy clone (and thus the live
# service). A truncated/partial tarball extract therefore becomes mass deletion
# of live modules — this is exactly the v5.89.186 incident, where a half-finished
# extract dropped analysis_routes.py / ml_inference.py and crash-looped staging.
# Refuse to proceed if the build tree looks incomplete, BEFORE any --delete runs.
_required=(VERSION app.py analysis_routes.py ml_inference.py b2b_followup.py model_config.py static/app.html static/admin.html static/sw.js requirements.txt)
_missing=()
for _f in "${_required[@]}"; do
  [ -f "$BUILD/$_f" ] || _missing+=("$_f")
done
if [ "${#_missing[@]}" -gt 0 ]; then
  echo "✗ ABORT — build tree '$BUILD' is missing required files:"
  printf '      %s\n' "${_missing[@]}"
  echo "  Almost always a truncated/partial tarball extract. NOT deploying —"
  echo "  rsync --delete would mirror these gaps and delete the live modules."
  echo "  Re-download the tarball (verify md5), re-extract, and retry."
  exit 1
fi
_count="$(find "$BUILD" -type f -not -path '*/.git/*' -not -path '*/__pycache__/*' -not -name '*.pyc' | wc -l | tr -d ' ')"
_min_files=450
if [ "$_count" -lt "$_min_files" ]; then
  echo "✗ ABORT — build tree has only $_count files (floor is $_min_files)."
  echo "  A complete build is ~590 files; this looks like an incomplete extract."
  echo "  NOT deploying (rsync --delete unsafe). Re-extract and retry."
  exit 1
fi
echo "✓ Completeness guard passed — $_count files, all required modules present."

# Inline-JS guard: every <script> block in admin.html must parse. A dropped
# declaration or top-level await halts the whole block and white-screens the admin
# panel — and that only surfaces when the WHOLE block is parsed, not a function in
# isolation. Fails closed (set -e aborts the deploy) so a broken panel can't ship.
echo "→ Validating inline admin JS (node --check every block)…"
python3 "$BUILD/scripts/check_html_js.py" "$BUILD/static/admin.html"

# JSX guard: the buyer report's <script type="text/babel"> block must compile
# (app.html is the most edit-prone file; a JSX syntax error white-screens the
# report). Babel-compiles it exactly like the browser; fails closed.
echo "→ Validating buyer-report JSX (Babel compile)…"
# The app runs on in-browser Babel; the guard needs @babel locally (build-time
# only). Ensure it just-in-time so the guard is self-sufficient — no node_modules
# shipped in the tarball.
if ! ( cd "$BUILD" && node -e "require.resolve('@babel/preset-react')" ) >/dev/null 2>&1; then
  echo "  installing JSX-guard deps (@babel/core, @babel/preset-react)…"
  ( cd "$BUILD" && npm install --no-save --silent "@babel/core@^7" "@babel/preset-react@^7" ) \
    || { echo "  ✗ could not install Babel — cannot validate JSX. Aborting deploy."; exit 1; }
fi
node "$BUILD/scripts/check_jsx.js" "$BUILD/static/app.html"

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
