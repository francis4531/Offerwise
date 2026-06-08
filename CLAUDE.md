# OfferWise — Claude Code working guide

OfferWise (getofferwise.ai) is a US homebuyer property-analysis SaaS. It analyzes
disclosures, inspection reports, and market data to produce structured buyer
guidance: offer math, risk scoring, repair-cost estimates, and ongoing property
monitoring.

**Stack:** Python / Flask, Docker, Render (2 GB tier, gunicorn `workers=1` gthread),
PostgreSQL, SQLAlchemy + Alembic, Resend (email), Stripe. Repo: `francis4531/Offerwise`.

---

## Golden rules (do not break these)

1. **Every change bumps `VERSION` and prepends a `CHANGELOG.md` entry in the *same*
   commit.** Never a separate commit, never a standalone note. The version bump and
   changelog entry are part of the change itself (`git add .` sweeps them in).
2. **Never change customer-report CSS without an explicit instruction to do so.**
   Six-plus months went into the report look and feel. Admin-page CSS is fine to
   touch; the buyer-facing report styling is not. When unsure, diff against the v83
   baseline before changing any style.
3. **Permanent solutions only.** No one-off scripts, no throwaway hacks. If a
   diagnostic is worth running once, promote it into a durable admin feature that
   fits the existing dashboard conventions.
4. **No redundant code.** Reuse the existing engines and endpoints; don't duplicate
   logic. One source of truth per concern.
5. **Be honest about limitations.** Don't call something "done" or "ready" before
   it's validated on real data. Flag real risks. Push back rather than overstate.
6. **All customer-facing text is complete sentences.** Never truncate with "…",
   never surface raw parser output, enum names, or fragments to a buyer or pro.
7. **Crawlers collect public documents only.** Respect `robots.txt`; skip
   login/paywall/auth gates and copyright-restricted content.

---

## Validate before every commit

Run these against the files you touched. All must be clean before committing.

Python — compile every touched module:
```
python3 -m py_compile path/to/changed_file.py
```

Tests — run the suites covering touched modules (read the implementation fully
before writing or changing a test; new code ships with passing tests):
```
python3 -m pytest test_card_import.py test_agentic_monitor.py -q
```

HTML/JS — check **each `<script>` block on its own**, not concatenated. Concatenating
blocks hides per-block scope/IIFE bugs and can't catch them:
```
python3 - <<'PY'
import re, subprocess
src = open('static/admin.html', encoding='utf-8').read()
for i, m in enumerate(re.finditer(r'<script\b(?![^>]*\bsrc=)[^>]*>(.*?)</script>', src, re.S|re.I)):
    open('/tmp/blk.js', 'w').write(m.group(1))
    r = subprocess.run(['node', '--check', '/tmp/blk.js'], capture_output=True, text=True)
    if r.returncode:
        print(f"block {i} FAILS:\n{r.stderr}")
PY
```

Structure checks for touched HTML:
- `<div>` opens and closes must net to zero in the main content region.
- `app.html` Babel block: braces/parens/brackets must balance. The backtick count
  baseline is **581** (odd, because of pre-existing escaped backticks) — match it,
  don't "fix" it.

---

## Deploy (history-preserving staging → prod)

Working in the repo, after committing your change to the staging branch:

Push to staging and verify the staging URL before promoting:
```
scripts/ow_deploy.sh
```

Promote to prod (fast-forward only into `main`):
```
scripts/ow_promote.sh
```

Rollback is done from the Render dashboard, not from git. Builds stack on the
staging branch, so deploy to staging and confirm there first, every time.

With Claude Code you edit in place and commit directly — there is no tar/zip handoff
and no `git init` + force-push. The old force-push flow wiped history and is
deprecated; never use it.

---

## Known gotchas (learned the hard way — don't relearn them)

- **`admin.html` inline handlers + IIFE.** The main infra/admin script is wrapped in
  an IIFE, so a top-level `function foo(){}` is *not* global. Any function called
  from an inline `onclick`/`onchange` must be published at the IIFE's end:
  `if (typeof foo === 'function') window.foo = foo;`. Miss it and you get
  `Uncaught ReferenceError: foo is not defined at HTMLButtonElement.onclick`.
- **`settings.html` reflow.** Don't use the `body.style.display='none'` reflow trick;
  it resets panel visibility. Use `requestAnimationFrame` instead.
- **Claude is the sole AI provider.** There is no OpenAI fallback anywhere
  (`ai_client.py`, `offerwise_intelligence.py`, `hybrid_ai.py`, truth-check). Keep it
  that way; `ANTHROPIC_MODEL` is the configured model.
- **Cost-page taxonomy.** Ad / marketing spend belongs **only** under Ad Performance —
  never on the API Costs or Infra Costs pages. The infra endpoints
  (`infra_invoices_list` / `infra_invoices_summary`) exclude vendor `category='ads'`
  via `_infra_category_filter`; `?category=ads` returns only those, for the Ad
  Performance "Referral & Directory Spend" card. NULL categories stay in infra.
  Google/Reddit ads are synced on Ad Performance, not seeded as infra vendors.
- **Card importer formats.** `parse_card_csv` accepts both a comma-CSV-with-header
  export and a whitespace/tab table copied from a bank site (via `_extract_rows`).
  Don't regress either path.
- **RentCast.** The comps and price monitors share one cached `/avm/value` call per
  address per day (`_rentcast_avm`). Don't reintroduce a second daily call.

---

## Working style

- Read the actual file or spec before writing code. If a format or mockup is
  provided, render/inspect it first and build to match — don't invent the format.
- For large or ambiguous changes, confirm scope before doing the work rather than
  guessing.
- Keep deploy commands as clean, copy-pasteable blocks: pure commands, no inline
  `#` comment lines (they error in zsh). Put explanation in prose outside the block.
- Reference baseline for clean diffs: the v83 snapshot of the report templates.
