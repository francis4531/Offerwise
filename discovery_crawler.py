"""discovery_crawler.py — Nightly discovery + research+draft job (v5.87.59).

Architecture:
  1. At 3:30am, run_nightly_crawl() reads pending DiscoveryQueueItem rows
  2. Top up to N items from _WEDGE_TOP_PLAYERS if queue has < N pending
  3. For each item (up to N), run discover_prospects() and create
     OutreachContact rows for any prospects returned
  4. After discovery, auto-run research+draft on the new contacts
     (Level 2 automation per founder spec)
  5. Update queue item status: completed | failed | deferred

Cost control:
  - Default N=5/night (env: DISCOVERY_CRAWL_NIGHTLY_CAP)
  - Each item costs ~1 Snov credit (50/mo free) + ~$0.03 Anthropic if
    prospects are found and research+draft fires
  - When provider credit floor reached, item is deferred (not failed)
    so it retries the next night without burning the retry budget
  - Max 3 attempts before terminal failure
  - Hard ceiling on prospects-per-item (default 10) to bound how many
    research+draft calls fire from a single rich-domain hit

Per the v5.87.49 env-var rule, runtime-tunable thresholds use env vars;
API constants and prompt engineering stay inline.
"""
from __future__ import annotations
import logging
import os
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Tunables (env-var-driven per v5.87.49 rule)
DISCOVERY_CRAWL_NIGHTLY_CAP = int(os.environ.get('DISCOVERY_CRAWL_NIGHTLY_CAP', '5'))
DISCOVERY_CRAWL_MAX_PROSPECTS_PER_ITEM = int(os.environ.get('DISCOVERY_CRAWL_MAX_PROSPECTS_PER_ITEM', '10'))
DISCOVERY_CRAWL_MAX_ATTEMPTS = int(os.environ.get('DISCOVERY_CRAWL_MAX_ATTEMPTS', '3'))
DISCOVERY_CRAWL_AUTO_RESEARCH = os.environ.get('DISCOVERY_CRAWL_AUTO_RESEARCH', '1') == '1'

# Inter-item pacing — even small delays between items help because the
# research+draft step makes Anthropic web search calls and we don't want
# to burst-fire 5 in 2 seconds.
DISCOVERY_CRAWL_ITEM_DELAY_SEC = int(os.environ.get('DISCOVERY_CRAWL_ITEM_DELAY_SEC', '3'))


def autopilot_topup(target_pending: int) -> int:
    """Ensure the queue has at least `target_pending` pending items by
    auto-queueing companies from _WEDGE_TOP_PLAYERS.

    Selection strategy: pick companies that haven't been queued in the
    last 30 days, rotating through wedges so we don't stack the queue
    with all renovation lenders one night and all insurtechs the next.

    Returns the number of items added.
    """
    from models import DiscoveryQueueItem, db
    from datetime import timedelta

    pending_count = (DiscoveryQueueItem.query
                     .filter_by(status='pending')
                     .count())
    needed = max(0, target_pending - pending_count)
    if needed == 0:
        return 0

    # Import the curated catalog
    try:
        from admin_routes import _WEDGE_TOP_PLAYERS
    except ImportError:
        logger.warning('autopilot_topup: _WEDGE_TOP_PLAYERS not available')
        return 0

    # Build a flat list of (wedge, name, domain) and exclude domains that
    # were queued in the last 30 days. Within each wedge, preserve the
    # catalog order (it's already roughly best-to-worst per Francis).
    cutoff = datetime.utcnow() - timedelta(days=30)
    recently_queued_domains = {
        d for (d,) in db.session.query(DiscoveryQueueItem.domain)
        .filter(DiscoveryQueueItem.queued_at >= cutoff).all()
    }

    # Round-robin across wedges to avoid stacking
    wedge_iters = {
        wedge: iter(companies)
        for wedge, companies in _WEDGE_TOP_PLAYERS.items()
    }
    added = 0
    safety = 200  # outer loop bound; catalog is ~50 items
    while added < needed and safety > 0:
        safety -= 1
        any_added_this_round = False
        for wedge in list(wedge_iters.keys()):
            if added >= needed:
                break
            try:
                company = next(wedge_iters[wedge])
            except StopIteration:
                continue
            domain = (company.get('domain') or '').strip().lower()
            if not domain or domain in recently_queued_domains:
                continue
            item = DiscoveryQueueItem(
                domain=domain,
                wedge=wedge,
                queued_by='autopilot',
                status='pending',
                seniority_filter='senior,executive,c_level',
            )
            db.session.add(item)
            recently_queued_domains.add(domain)
            added += 1
            any_added_this_round = True
        if not any_added_this_round:
            break  # nothing left in any wedge

    if added > 0:
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error('autopilot_topup commit failed: %s', e)
            return 0

    logger.info('autopilot_topup: added %d items (target=%d, was_pending=%d)',
                added, target_pending, pending_count)
    return added


def _process_one_item(item) -> dict[str, Any]:
    """Discover prospects for a single queue item, create OutreachContact
    rows, and (Level 2) auto-run research+draft on the new contacts.

    Returns a dict with what happened: prospects_found, drafts_generated,
    source_used, error.
    """
    from models import OutreachContact, db
    from prospect_research_service import discover_prospects, research_and_draft

    out = {
        'prospects_found': 0,
        'drafts_generated': 0,
        'source_used': 'none',
        'error': None,
        'deferred': False,  # set true when ALL providers are credit-floored
    }

    # Stage 1: discover
    titles = None
    if item.title_filter:
        titles = [t.strip() for t in item.title_filter.split(',') if t.strip()]

    seniorities = None
    if item.seniority_filter:
        # The orchestrator accepts a list; map Hunter-style strings into it
        hunter_to_orch = {
            'c_level': 'c_suite', 'executive': 'c_suite',
            'senior': 'director',
        }
        seniorities = []
        for tag in item.seniority_filter.split(','):
            tag = tag.strip()
            mapped = hunter_to_orch.get(tag)
            if mapped and mapped not in seniorities:
                seniorities.append(mapped)
        seniorities = seniorities or None

    try:
        result = discover_prospects(
            company_domain=item.domain,
            company_name='',
            titles=titles,
            seniorities=seniorities,
            limit=DISCOVERY_CRAWL_MAX_PROSPECTS_PER_ITEM,
        )
    except Exception as e:
        out['error'] = f'discover_prospects raised: {e.__class__.__name__}: {str(e)[:300]}'
        return out

    out['source_used'] = result.get('source') or 'none'
    prospects = result.get('prospects') or []
    errors = result.get('errors') or []

    # Detect "all providers exhausted" → defer (don't burn retry budget)
    if out['source_used'] == 'none':
        joined = ' '.join(errors).lower()
        if 'credit' in joined and 'floor' in joined:
            out['deferred'] = True
            out['error'] = 'all providers at credit floor; will retry tomorrow'
            return out
        # Real failure (no creds for any provider configured) — surface but
        # don't defer
        out['error'] = ' | '.join(errors[:3]) if errors else 'no provider returned results'
        return out

    if not prospects:
        # Provider succeeded but database had nothing for this domain.
        # Not a failure — record completion with prospects_found=0.
        out['error'] = f'{out["source_used"]}: no emails found at {item.domain}'
        return out

    # Stage 2: persist as OutreachContact rows. Dedup on email.
    existing_emails = {
        e for (e,) in db.session.query(OutreachContact.email)
        .filter(OutreachContact.email.in_([p.get('email') for p in prospects if p.get('email')]))
        .all()
    }

    # v5.88.00: Also exclude blocklisted emails. The founder marked these
    # "never contact" — the crawler must respect that even when the
    # provider returns them again.
    from models import ProspectBlocklist
    blocklisted_emails = {
        e for (e,) in db.session.query(ProspectBlocklist.email)
        .filter(ProspectBlocklist.email.in_([(p.get('email') or '').lower() for p in prospects if p.get('email')]))
        .all()
    }

    new_contact_ids = []
    blocked_skipped = 0
    for p in prospects:
        email = (p.get('email') or '').strip().lower()
        if not email or email in existing_emails:
            continue
        if email in blocklisted_emails:
            blocked_skipped += 1
            continue
        # Wedge: prefer the queue item's wedge over auto-inferring
        wedge = item.wedge or 'other'
        contact = OutreachContact(
            email=email,
            name=p.get('name') or '',
            title=p.get('title') or '',
            company=p.get('company') or '',
            wedge=wedge,
            cohort='b2b',
            status='not_contacted',
        )
        db.session.add(contact)
        try:
            db.session.flush()
            new_contact_ids.append(contact.id)
            existing_emails.add(email)
        except Exception as flush_err:
            db.session.rollback()
            logger.warning('crawler flush failed for %s: %s', email, flush_err)
            continue

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        out['error'] = f'commit failed: {e}'
        return out

    out['prospects_found'] = len(new_contact_ids)
    out['blocked_skipped'] = blocked_skipped

    # Stage 3 (Level 2): auto-run research+draft on the new contacts
    if not DISCOVERY_CRAWL_AUTO_RESEARCH or not new_contact_ids:
        return out

    drafted = 0
    for contact_id in new_contact_ids:
        try:
            c = OutreachContact.query.get(contact_id)
            if not c:
                continue
            inferred_domain = (c.email or '').split('@')[-1] if c.email else ''
            r = research_and_draft(
                name=c.name or '',
                email=c.email or '',
                title=c.title or '',
                company=c.company or '',
                company_domain=inferred_domain,
                wedge=c.wedge or '',
                skip_research=False,
            )
            if r.get('subject') and r.get('body'):
                c.focus_areas = r.get('focus_areas') or ''
                c.draft_subject = r.get('subject') or ''
                c.draft_body = r.get('body') or ''
                c.draft_generated_at = datetime.utcnow()
                db.session.add(c)
                drafted += 1
        except Exception as e:
            logger.warning('crawler research+draft failed for contact_id=%s: %s',
                           contact_id, e)
            # Don't break the loop on one bad draft; commit what we have
            db.session.rollback()
            continue

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning('crawler research+draft commit failed: %s', e)

    out['drafts_generated'] = drafted
    return out


def run_nightly_crawl() -> dict[str, Any]:
    """Entry point for the 3:30am scheduler job.

    Returns a summary dict that's also logged. The job itself is fire-
    and-forget from the scheduler's perspective.
    """
    from models import DiscoveryQueueItem, db

    started_at = datetime.utcnow()
    summary = {
        'started_at': started_at.isoformat(),
        'autopilot_added': 0,
        'items_processed': 0,
        'completed': 0,
        'deferred': 0,
        'failed': 0,
        'total_prospects_found': 0,
        'total_drafts_generated': 0,
        'errors': [],
    }

    # Stage 0: top up the queue if needed
    try:
        summary['autopilot_added'] = autopilot_topup(DISCOVERY_CRAWL_NIGHTLY_CAP)
    except Exception as e:
        logger.error('autopilot_topup failed: %s', e)
        summary['errors'].append(f'topup: {e}')

    # Stage 1-3: process pending items (oldest first, capped at N)
    pending = (DiscoveryQueueItem.query
               .filter_by(status='pending')
               .order_by(DiscoveryQueueItem.queued_at.asc())
               .limit(DISCOVERY_CRAWL_NIGHTLY_CAP)
               .all())

    for item in pending:
        # Mark running
        item.status = 'running'
        item.attempts += 1
        item.last_attempt_at = datetime.utcnow()
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

        result = _process_one_item(item)
        summary['items_processed'] += 1

        # Update item state based on result
        item.prospects_found_count = result['prospects_found']
        item.drafts_generated_count = result['drafts_generated']
        item.source_used = result['source_used']
        item.error = result['error']

        if result.get('deferred'):
            item.status = 'pending'  # try again tomorrow
            summary['deferred'] += 1
        elif result['error'] and 'no emails found at' not in (result['error'] or ''):
            # Real failure (not the "Snov returned 0 prospects" case)
            if item.attempts >= DISCOVERY_CRAWL_MAX_ATTEMPTS:
                item.status = 'failed'
                summary['failed'] += 1
            else:
                item.status = 'pending'  # retry tomorrow
        else:
            item.status = 'completed'
            item.completed_at = datetime.utcnow()
            summary['completed'] += 1
            summary['total_prospects_found'] += result['prospects_found']
            summary['total_drafts_generated'] += result['drafts_generated']

        db.session.add(item)
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error('crawler item commit failed: %s', e)

        # Pace between items
        if DISCOVERY_CRAWL_ITEM_DELAY_SEC > 0:
            time.sleep(DISCOVERY_CRAWL_ITEM_DELAY_SEC)

    summary['ended_at'] = datetime.utcnow().isoformat()
    summary['elapsed_seconds'] = (datetime.utcnow() - started_at).total_seconds()
    logger.info('discovery_crawler.run_nightly_crawl complete: %s', summary)
    return summary
