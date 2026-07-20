#!/usr/bin/env bash
# OfferWise -- one-time git setup for a machine with TWO GitHub accounts.
#
# THE PROBLEM THIS SOLVES
#   With HTTPS remotes, macOS Keychain stores ONE credential for github.com. If
#   you're signed in with a work account, git silently uses it for every repo and
#   the OfferWise push dies with:
#       remote: Permission to francis4531/Offerwise.git denied to <work-account>
#       fatal: ... The requested URL returned error: 403
#   Switching accounts back and forth is manual and easy to get wrong.
#
# THE FIX
#   Give each account its own SSH key and its own HOST ALIAS. The alias in the
#   remote URL selects the key, so identity is decided per-repo by the URL -- never
#   by a shared keychain entry. Both accounts keep working, with no switching.
#
#     work repos      -> git@github.com:...            (unchanged, your default key)
#     OfferWise repo  -> git@github-offerwise:francis4531/Offerwise.git
#
# Run this ONCE. It is idempotent -- safe to re-run.
#
# NOTE: this file is deliberately PURE ASCII. macOS ships bash 3.2, which mis-parses a
# multi-byte character sitting immediately after a variable ($VAR followed by an
# ellipsis) -- it swallows the bytes into the name, then `set -u` aborts with
# "GH_USER?: unbound variable". Keep this script ASCII-only.
#
# Usage:
#   scripts/ow_git_setup.sh [github-username] [email-for-commits]
set -euo pipefail

GH_USER="${1:-francis4531}"
GH_EMAIL="${2:-francis@getofferwise.ai}"
ALIAS_HOST="github-offerwise"
KEY="$HOME/.ssh/id_ed25519_offerwise"
SSH_CONFIG="$HOME/.ssh/config"
OW_REPO="${OW_REPO:-$HOME/offerwise-deploy}"

echo "------------------------------------------------------------"
echo "  OfferWise git setup -- account: $GH_USER"
echo "------------------------------------------------------------"

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

# -- 1. Dedicated key for this account ----------------------------------------
if [ -f "$KEY" ]; then
  echo "[ok] Key already exists: $KEY"
  # A partial/interrupted setup can leave the private key without its .pub.
  # Regenerate it from the private key rather than failing later on `cat`.
  if [ ! -f "${KEY}.pub" ]; then
    echo "-> Public half missing; regenerating ${KEY}.pub from the private key..."
    ssh-keygen -y -f "$KEY" > "${KEY}.pub"
    chmod 644 "${KEY}.pub"
    echo "[ok] Regenerated ${KEY}.pub"
  fi
else
  echo "-> Creating a dedicated SSH key for $GH_USER..."
  ssh-keygen -t ed25519 -f "$KEY" -N "" -C "${GH_EMAIL:-$GH_USER}@offerwise-deploy"
  echo "[ok] Created $KEY"
fi

# -- 2. Host alias so the URL picks the key -----------------------------------
touch "$SSH_CONFIG"; chmod 600 "$SSH_CONFIG"
if grep -q "^Host ${ALIAS_HOST}\$" "$SSH_CONFIG" 2>/dev/null; then
  echo "[ok] SSH alias '${ALIAS_HOST}' already configured"
else
  echo "-> Adding SSH alias '${ALIAS_HOST}' to $SSH_CONFIG..."
  cat >> "$SSH_CONFIG" <<EOF

# OfferWise deploy identity ($GH_USER) -- keeps this repo's account separate from
# any other GitHub account on this machine. Added by scripts/ow_git_setup.sh.
Host ${ALIAS_HOST}
  HostName github.com
  User git
  IdentityFile ${KEY}
  IdentitiesOnly yes
EOF
  echo "[ok] Alias added"
fi

# -- 3. Show the public key to register ---------------------------------------
echo ""
echo "------------------------------------------------------------"
echo "  ACTION REQUIRED -- add this key to the $GH_USER account"
echo "------------------------------------------------------------"
echo ""
cat "${KEY}.pub"
echo ""
echo "  1. Copy the line above (it's also on your clipboard if pbcopy exists)."
echo "  2. Sign in to GitHub as: $GH_USER"
echo "  3. Settings -> SSH and GPG keys -> New SSH key -> paste -> Add."
echo ""
command -v pbcopy >/dev/null 2>&1 && pbcopy < "${KEY}.pub" && echo "  (copied to clipboard)"
echo ""
read -r -p "Press Enter once the key is added to GitHub... " _

# -- 4. Verify the identity ---------------------------------------------------
echo "-> Verifying identity..."
WHO="$(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -T "git@${ALIAS_HOST}" 2>&1 || true)"
if printf '%s' "$WHO" | grep -q "Hi ${GH_USER}[!/]"; then
  echo "[ok] Authenticated as ${GH_USER}"
else
  echo "[X] Could not authenticate as ${GH_USER}."
  echo "  GitHub said: ${WHO:-<no response>}"
  echo "  Re-check that the key above was added to the ${GH_USER} account, then re-run."
  exit 1
fi

# -- 5. Point the deploy clone at the alias -----------------------------------
NEW_URL="git@${ALIAS_HOST}:${GH_USER}/Offerwise.git"
if [ -d "$OW_REPO/.git" ]; then
  CUR="$(git -C "$OW_REPO" remote get-url origin 2>/dev/null || echo '')"
  if [ "$CUR" != "$NEW_URL" ]; then
    git -C "$OW_REPO" remote set-url origin "$NEW_URL"
    echo "[ok] $OW_REPO origin -> $NEW_URL"
  else
    echo "[ok] $OW_REPO already using the alias"
  fi
  # Per-repo commit identity, so deploys aren't attributed to the other account.
  git -C "$OW_REPO" config user.email "$GH_EMAIL"
  git -C "$OW_REPO" config user.name "$GH_USER"
  echo "[ok] Commit identity pinned for this clone only (global git config untouched)"
else
  echo "* No clone at $OW_REPO yet -- ow_deploy.sh will create it using the alias."
fi

echo ""
echo "------------------------------------------------------------"
echo "  Done. Both GitHub accounts now coexist:"
echo "    work repos      git@github.com:...          (your default key)"
echo "    OfferWise       git@${ALIAS_HOST}:...   ($GH_USER)"
echo ""
echo "  Nothing to switch -- the remote URL picks the account."
echo "  Next: scripts/ow_deploy.sh"
echo "------------------------------------------------------------"
