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
#   OW_GIT_USER  GitHub account that MUST own the push (default: francis4531)
#
# MULTI-ACCOUNT SAFETY (v5.89.304)
#   If you use more than one GitHub account on this machine (e.g. a work account
#   and this one), macOS Keychain will happily hand git the WRONG cached token and
#   the push dies with:
#       remote: Permission to francis4531/Offerwise.git denied to <other-account>
#   The fix is an SSH host alias per account, so the key — not a shared keychain
#   entry — decides the identity. Run scripts/ow_git_setup.sh once to configure it.
#   This script then VERIFIES the effective identity before pushing and refuses to
#   continue if it isn't OW_GIT_USER, so a wrong-account push can't reach the repo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DEFAULT="$(dirname "$SCRIPT_DIR")"
_build_arg="${1:-$BUILD_DEFAULT}"
# Resolve to an ABSOLUTE path before we cd into the clone — otherwise a relative
# arg (e.g. "offerwise_render") breaks once the working dir changes.
BUILD="$(cd "$_build_arg" 2>/dev/null && pwd || true)"
[ -n "$BUILD" ] || { echo "✗ build dir '$_build_arg' not found"; exit 1; }
OW_REPO="${OW_REPO:-$HOME/offerwise-deploy}"
# Default to the SSH alias set up by scripts/ow_git_setup.sh. Falls back to HTTPS
# only if you override OW_GIT explicitly.
OW_GIT="${OW_GIT:-git@github-offerwise:francis4531/Offerwise.git}"
OW_GIT_USER="${OW_GIT_USER:-francis4531}"

# ── Identity guard ────────────────────────────────────────────────────────────
# Confirm the credentials git will actually use belong to OW_GIT_USER, BEFORE we
# touch the network. Fails loudly with the exact remedy rather than letting the
# push 403 after all the build work is done.
_verify_git_identity() {
  case "$OW_GIT" in
    git@*)
      local host="${OW_GIT#git@}"; host="${host%%:*}"
      local who
      # `ssh -T git@host` exits non-zero by design; the greeting is on stderr.
      who="$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -T "git@${host}" 2>&1 || true)"
      if printf '%s' "$who" | grep -q "Hi ${OW_GIT_USER}[!/]"; then
        echo "✓ GitHub identity: ${OW_GIT_USER} (via ssh alias '${host}')"
        return 0
      fi
      echo "✗ Wrong GitHub identity for this repo."
      echo "  Expected: ${OW_GIT_USER}"
      echo "  Got:      ${who:-<no response>}"
      echo ""
      echo "  Run once to fix:  scripts/ow_git_setup.sh"
      echo "  (sets up an SSH alias so this repo always uses ${OW_GIT_USER},"
      echo "   leaving your other GitHub account untouched.)"
      exit 1
      ;;
    https://*)
      echo "⚠ Remote is HTTPS — identity can't be verified before pushing, and on a"
      echo "  multi-account machine the Keychain may supply the wrong token."
      echo "  Recommended: run scripts/ow_git_setup.sh to switch this clone to SSH."
      ;;
  esac
}
_verify_git_identity

[ -f "$BUILD/VERSION" ] || { echo "✗ '$BUILD' has no VERSION file — not a build dir."; exit 1; }
VER="$(cat "$BUILD/VERSION")"

if [ ! -d "$OW_REPO/.git" ]; then
  echo "→ First run: cloning $OW_GIT into $OW_REPO (keeps history → enables rollback)"
  git clone "$OW_GIT" "$OW_REPO"
fi

cd "$OW_REPO"

# v5.89.304: if this clone predates the multi-account fix its remote is still the
# HTTPS URL, which is what lets the Keychain hand git the wrong account's token.
# Re-point it at the configured (SSH alias) URL so identity is decided by the key.
_current_remote="$(git remote get-url origin 2>/dev/null || echo '')"
if [ -n "$_current_remote" ] && [ "$_current_remote" != "$OW_GIT" ]; then
  echo "→ Re-pointing origin to the identity-safe remote:"
  echo "    was: $_current_remote"
  echo "    now: $OW_GIT"
  git remote set-url origin "$OW_GIT"
fi

# Pin the commit author for THIS clone only, so deploys are attributed to the
# OfferWise account even when the machine's global git identity is the other one.
if [ -n "${OW_GIT_EMAIL:-}" ]; then
  git config user.email "$OW_GIT_EMAIL"
fi
git config user.name "$(git config user.name 2>/dev/null || echo "$OW_GIT_USER")" >/dev/null 2>&1 || true

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
  if [ -f "$BUILD/package-lock.json" ]; then
    ( cd "$BUILD" && npm ci --silent ) \
      || { echo "  ✗ npm ci failed — cannot validate JSX. Aborting deploy."; exit 1; }
  else
    ( cd "$BUILD" && npm install --no-save --silent "@babel/core@^7" "@babel/preset-react@^7" ) \
      || { echo "  ✗ could not install Babel — cannot validate JSX. Aborting deploy."; exit 1; }
  fi
fi
node "$BUILD/scripts/check_jsx.js" "$BUILD/static/app.html"

# Duplicate/self-referential lexical declaration guard (v5.89.295): catches the bug
# class that white-screened analysis in .293/.294 — a `const x = ... || x` /
# duplicate `const x` in the same scope. Valid JS syntax (check_jsx compiles it), so
# only this static-scope check catches it before it ships.
node "$BUILD/scripts/check_dup_declarations.js" \
  || { echo "  ✗ duplicate/self-referential declaration in app.html. Aborting deploy."; exit 1; }

# Import + coverage guard (v5.89.274): catches the two failure classes py_compile
# can't — a module-level NameError that dead-boots the worker, and an API-coverage
# regression. Exit 1 = real code bug (block); exit 2 = app deps not installed here
# (can't verify — warn, don't block, since CI will still check).
set +e
python3 "$BUILD/scripts/prepackage_guard.py"
_guard_rc=$?
set -e
if [ "$_guard_rc" = "1" ]; then
  echo "✗ prepackage guard failed — a code bug that would break boot or CI. Aborting."
  exit 1
elif [ "$_guard_rc" = "2" ]; then
  echo "⚠ prepackage guard could not verify imports (app deps not installed locally) — CI will still check."
fi

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
