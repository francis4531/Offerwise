#!/usr/bin/env bash
# OfferWise — one-command interactive deploy.
#
# Runs the whole flow in a single command: find the newest build tarball in
# ~/Downloads, verify + extract it, push to STAGING, pause for you to eyeball
# the staging site, then — only if you confirm — promote the SAME commit to
# PRODUCTION. Save this once as ~/ow_ship.sh and run `~/ow_ship.sh` every deploy.
#
# Usage:
#   ~/ow_ship.sh                   # newest offerwise_render_v*.tar.gz in ~/Downloads
#   ~/ow_ship.sh 5.89.196          # a specific version
#   ~/ow_ship.sh /path/build.tar.gz
set -euo pipefail

DOWNLOADS="${OW_DOWNLOADS:-$HOME/Downloads}"
EXTRACT_TO="$DOWNLOADS/offerwise_render"

say() { printf '%s\n' "$*"; }
hr()  { printf '%s\n' "────────────────────────────────────────────────────────────"; }

# 1) Locate the tarball -------------------------------------------------------
arg="${1:-}"
if   [ -n "$arg" ] && [ -f "$arg" ]; then TARBALL="$arg"
elif [ -n "$arg" ]; then                  TARBALL="$DOWNLOADS/offerwise_render_v${arg#v}.tar.gz"
else TARBALL="$(ls -1 "$DOWNLOADS"/offerwise_render_v*.tar.gz 2>/dev/null | sort -t_ -k3 -V | tail -1)"
fi
[ -n "${TARBALL:-}" ] && [ -f "$TARBALL" ] || {
  say "✗ No build found in $DOWNLOADS (offerwise_render_v*.tar.gz)."
  say "  Pass a version or path, e.g.  ~/ow_ship.sh 5.89.196"
  exit 1; }

say "→ Build: $(basename "$TARBALL")"
if command -v md5 >/dev/null 2>&1; then say "  md5:   $(md5 -q "$TARBALL")"
else say "  md5:   $(md5sum "$TARBALL" | awk '{print $1}')"; fi

# 2) Verify + extract ---------------------------------------------------------
gzip -t "$TARBALL" || { say "✗ Tarball is corrupt (incomplete download). Re-download and retry."; exit 1; }
rm -rf "$EXTRACT_TO"
tar xzf "$TARBALL" -C "$DOWNLOADS"
[ -f "$EXTRACT_TO/VERSION" ] && [ -f "$EXTRACT_TO/scripts/ow_deploy.sh" ] || {
  say "✗ Extract looks wrong (no VERSION / deploy script). Re-download and retry."; exit 1; }
grep -q 'standalone@7.29.7' "$EXTRACT_TO/static/app.html" || {
  say "✗ Safety check: Babel not pinned in app.html — not deploying."; exit 1; }
VER="$(cat "$EXTRACT_TO/VERSION")"
say "✓ Extracted v$VER (archive + Babel pin verified)"

# Heads-up (non-destructive) if this build ships a newer copy of this launcher.
if [ -f "$EXTRACT_TO/scripts/ow_ship.sh" ] && [ -f "$0" ] && ! cmp -s "$EXTRACT_TO/scripts/ow_ship.sh" "$0"; then
  say "ℹ A newer ow_ship.sh ships in this build. To update your launcher:"
  say "    cp \"$EXTRACT_TO/scripts/ow_ship.sh\" \"$0\""
fi

# 3) Deploy to staging --------------------------------------------------------
say ""; hr; say "  Deploying v$VER to STAGING"; hr
if ! bash "$EXTRACT_TO/scripts/ow_deploy.sh"; then
  say "✗ Staging deploy failed (see above). Production was NOT touched."
  exit 1
fi

# 4) Pause for human verification ---------------------------------------------
say ""
say "════════════════════════════════════════════════════════════════"
say "  STAGING updated — verify it (give Render a minute to build):"
say "      https://offerwise-staging.onrender.com"
say "════════════════════════════════════════════════════════════════"
printf "Promote v%s to PRODUCTION? [y/N] " "$VER"
reply=""; read -r reply </dev/tty || true
case "$reply" in
  y|Y|yes|YES)
    say ""; hr; say "  Promoting v$VER to PRODUCTION"; hr
    bash "$HOME/offerwise-deploy/scripts/ow_promote.sh"
    ;;
  *)
    say ""
    say "• Stopped at staging — production unchanged."
    say "  Re-run ~/ow_ship.sh and answer y when staging looks good,"
    say "  or promote later:  bash ~/offerwise-deploy/scripts/ow_promote.sh"
    ;;
esac
