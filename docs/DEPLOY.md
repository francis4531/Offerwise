# Deploying OfferWise

## One command (recommended)

Save the launcher once:  it lives at `scripts/ow_ship.sh` in any build, or use the
bootstrap Claude provides. Then every deploy is a single interactive command:

    ~/ow_ship.sh                 # newest tarball in ~/Downloads
    ~/ow_ship.sh 5.89.196        # a specific version

It finds the newest build in ~/Downloads, verifies + extracts it, pushes to
staging, then PAUSES and asks before promoting to production — answer `y` only
after you've eyeballed https://offerwise-staging.onrender.com. Answer anything
else and it stops at staging (production untouched).

The manual two-step flow below still works and is what ow_ship.sh calls under the
hood (scripts/ow_deploy.sh then scripts/ow_promote.sh).

---

# OfferWise — Deploy & Staging Guide (v5.89.132)

This replaces the old `git init && … && git push render main --force` flow.

**Why the change.** Force-pushing a freshly `git init`-ed tree to prod did two
harmful things on every deploy: it **wiped git history** (so Render had no prior
commit to roll back to) and it pushed **straight to production**, untested. The
new flow keeps one persistent clone with real history, deploys to a **staging**
service first, and promotes the *same verified commit* to prod — and because
history is preserved, Render's dashboard **Rollback** button now works.

```
extract build  →  ow_deploy.sh  →  (verify staging URL)  →  ow_promote.sh  →  prod
                   pushes `staging`                          ff-only → `main`
```

---

## One-time setup (do this once)

### 1. Create the persistent deploy clone
The scripts operate on one long-lived clone (default `~/offerwise-deploy`) so
history survives between deploys.

```bash
git clone https://github.com/francis4531/Offerwise.git ~/offerwise-deploy
```

(If you'd rather keep it elsewhere, set `OW_REPO=/your/path` when running the
scripts. `ow_deploy.sh` will also clone it for you on first run if missing.)

### 2. Create the `staging` branch
```bash
cd ~/offerwise-deploy
git checkout -B staging origin/main
git push origin staging
```
(`ow_deploy.sh` auto-creates this from `main` on first run too, so this is
optional — but doing it now lets you wire up Render in step 3.)

### 3. Apply the Blueprint in Render (creates the staging service + DB)
`render.yaml` now defines two web services and two databases:

| Service              | Branch    | Plan     | Database              |
|----------------------|-----------|----------|-----------------------|
| `offerwise` (prod)   | `main`    | Standard | `offerwise-db`        |
| `offerwise-staging`  | `staging` | Starter  | `offerwise-staging-db`|

In the Render dashboard: open the Blueprint for this repo → **re-sync** it.
Render will create `offerwise-staging` (web), `offerwise-staging-db` (Postgres),
and the `docrepo-staging` disk. Confirm afterward that:
- **prod `offerwise` tracks `main`**, and
- **`offerwise-staging` tracks `staging`**.

### 4. Set the staging secrets (the `sync: false` keys)
On the **offerwise-staging** service → Environment, set:
- `STRIPE_PUBLISHABLE_KEY` / `STRIPE_SECRET_KEY` → **Stripe TEST keys**
  (so staging never touches live payments).
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` and
  `FACEBOOK_CLIENT_ID` / `FACEBOOK_CLIENT_SECRET` → a **staging OAuth client**,
  with the staging domain added to the authorized redirect URIs.

Staging sets `APP_ENV=staging`, which **disables the background scheduler** — no
drip emails, monitor jobs, daily-task emails, or crawlers run there. ML
*inference* still initializes, so you can run analyses to verify flows. ML
*training* does not run on staging (Starter is too small; that's fine — staging
is for verifying boot + flows, not retraining). If staging ever OOMs on boot,
flip its one line `plan: starter` → `plan: standard` in `render.yaml`.

> Note: Render **free** Postgres expires ~30 days after creation. If you want a
> steady staging DB, bump `offerwise-staging-db` to a small paid plan.

---

## Per-deploy flow (every time you ship)

```bash
# 1. Extract the build you were handed (creates ./offerwise_render)
cd ~/Downloads && rm -rf offerwise_render && tar -xzf offerwise_render_vX.tar.gz

# 2. Deploy to STAGING (history-preserving; pushes the `staging` branch)
offerwise_render/scripts/ow_deploy.sh offerwise_render

# 3. Verify on the staging URL (the offerwise-staging service in Render).
#    Smoke-check the pages/flows your change touched.

# 4. Promote the SAME commit to PRODUCTION (fast-forward main, then push)
offerwise_render/scripts/ow_promote.sh
```

`ow_deploy.sh` auto-detects the build directory as the parent of `scripts/`, so
`offerwise_render/scripts/ow_deploy.sh` with no argument works too. Override the
clone location or remote with `OW_REPO=…` / `OW_GIT=…` if needed.

If `ow_promote.sh` reports **"main has diverged from staging"**, a change landed
directly on `main` (e.g. a hotfix) that staging doesn't have. The script prints
the exact commands to fold that change back into `staging`, re-verify, then
re-promote — it will not silently merge or rewrite history.

---

## Rollback

History is preserved, so Render's built-in rollback works:

**Render dashboard → `offerwise` → Deploys → pick the last good deploy → Rollback.**

---

## Interim option (no staging service yet)

If you haven't applied the Blueprint yet, you can still get the safety win of
keeping history (and thus rollback) without a second service: deploy through the
persistent clone to `main` directly instead of force-pushing.

```bash
cd ~/offerwise-deploy && git checkout main && git pull --ff-only
rsync -a --delete --exclude='.git/' ~/Downloads/offerwise_render/ ./
git add -A && git commit -m "vX" && git push origin main
```

This skips the staging verification step but never wipes history. Once the
staging service exists, prefer the `ow_deploy.sh` → verify → `ow_promote.sh`
flow above.
