"""
Forum Scanner — Reddit & BiggerPockets Thread Discovery + Reply Drafting
=========================================================================
Scans target communities for threads where homebuyers ask questions
relevant to OfferWise. Scores threads for relevance, then uses Claude
to draft helpful replies.

Flow:
  1. Fetch recent posts from each target community (public JSON API)
  2. Keyword pre-filter (fast, no API cost)
  3. AI scoring + reply drafting for qualifying threads (Claude API)
  4. Store in GTMScannedThread + GTMRedditDraft for admin review

Respects crawler policy: public data only, no login/auth, rate-limited.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
USER_AGENT = 'OfferWise:GTM-Scanner:v1.0 (by /u/offerwiseAI)'
REDDIT_HEADERS = {'User-Agent': USER_AGENT}

# Keyword groups — thread must match at least one
KEYWORD_GROUPS = [
    # Inspection-related
    ['inspection', 'inspector', 'inspection report', 'home inspection', 'pest inspection', 'sewer scope'],
    # Disclosure-related
    ['disclosure', 'seller disclosure', 'TDS', 'transfer disclosure', 'disclose', 'undisclosed'],
    # Offer strategy
    ['offer price', 'how much to offer', 'offer strategy', 'below asking', 'above asking', 'lowball', 'asking price'],
    # Red flags / issues
    ['red flag', 'deal breaker', 'walk away', 'foundation issue', 'roof issue', 'mold', 'termite', 'asbestos', 'water damage', 'cracks'],
    # Repair costs
    ['repair cost', 'repair estimate', 'how much to fix', 'renovation cost', 'repair', 'replace roof', 'fix foundation', 'plumbing', 'electrical', 'hvac'],
    # First-time buyer
    ['first time buyer', 'first home', 'first-time homebuyer', 'ftb', 'first deal', 'first property', 'first house', 'new buyer', 'first purchase'],
    # Negotiation
    ['negotiate', 'negotiation', 'contingency', 'repair credit', 'seller concession', 'closing cost', 'price reduction', 'counter offer'],
    # California-specific
    ['california', 'bay area', 'san jose', 'los angeles', 'san francisco', 'socal', 'norcal', 'san diego', 'sacramento', 'oakland'],
    # Property condition / due diligence (new)
    ['old house', 'old home', 'property condition', 'due diligence', 'too old', 'fixer', 'fixer upper', 'as-is', 'needs work'],
    # Buying process (new)
    ['home buying', 'buying a house', 'house hunting', 'under contract', 'escrow', 'appraisal', 'closing', 'earnest money'],
]

# Flatten for quick matching
ALL_KEYWORDS = set()
for group in KEYWORD_GROUPS:
    for kw in group:
        ALL_KEYWORDS.add(kw.lower())

# Minimum keyword score to pass to AI scoring
MIN_KEYWORD_SCORE = 1
# Minimum AI score (1-10) to generate a draft
MIN_AI_SCORE = 4
# Max threads to AI-score per scan (API cost control)
MAX_AI_SCORES_PER_SCAN = 15
# Max posts to fetch per subreddit
POSTS_PER_SUB = 25


# ── Reddit Fetching ──────────────────────────────────────────────

# Reddit blocks unauthenticated JSON requests from datacenter IPs.
# If REDDIT_CLIENT_ID/SECRET are set, use OAuth; otherwise fall back to public.
REDDIT_CLIENT_ID = os.environ.get('REDDIT_CLIENT_ID', '')
REDDIT_CLIENT_SECRET = os.environ.get('REDDIT_CLIENT_SECRET', '')
REDDIT_USERNAME = os.environ.get('REDDIT_USERNAME', '')
REDDIT_PASSWORD = os.environ.get('REDDIT_PASSWORD', '')

_oauth_token = None
_oauth_expires = 0


def _get_reddit_oauth_token():
    """Get an OAuth2 bearer token for Reddit API access."""
    global _oauth_token, _oauth_expires
    if _oauth_token and time.time() < _oauth_expires - 60:
        return _oauth_token

    resp = requests.post(
        'https://www.reddit.com/api/v1/access_token',
        auth=(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
        data={
            'grant_type': 'password',
            'username': REDDIT_USERNAME,
            'password': REDDIT_PASSWORD,
        },
        headers={'User-Agent': USER_AGENT},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _oauth_token = data.get('access_token')
    _oauth_expires = time.time() + data.get('expires_in', 3600)
    return _oauth_token


def _reddit_has_oauth():
    return all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD])


def fetch_reddit_posts(subreddit, limit=POSTS_PER_SUB):
    """Fetch recent posts from a subreddit. Uses OAuth if credentials are set."""
    if _reddit_has_oauth():
        return _fetch_reddit_oauth(subreddit, limit)
    return _fetch_reddit_public(subreddit, limit)


def _fetch_reddit_oauth(subreddit, limit):
    """Fetch posts using OAuth (works from datacenter IPs)."""
    try:
        token = _get_reddit_oauth_token()
        resp = requests.get(
            f'https://oauth.reddit.com/r/{subreddit}/new?limit={limit}',
            headers={
                'Authorization': f'bearer {token}',
                'User-Agent': USER_AGENT,
            },
            timeout=15,
        )
        if resp.status_code == 429:
            logger.warning(f"Reddit rate limited on r/{subreddit}, backing off")
            time.sleep(5)
            return []
        resp.raise_for_status()
        return _parse_reddit_listing(resp.json(), subreddit)
    except Exception as e:
        logger.error(f"Reddit OAuth fetch error for r/{subreddit}: {e}")
        return []


def _fetch_reddit_public(subreddit, limit):
    """Fetch posts using public JSON API. Uses browser-like UA for server compatibility."""
    url = f'https://www.reddit.com/r/{subreddit}/new.json?limit={limit}&raw_json=1'
    # Reddit is more permissive with browser-like user agents from server IPs
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; OfferWise/1.0; +https://www.getofferwise.ai)',
        'Accept': 'application/json',
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 403:
            logger.warning(
                f"Reddit blocked r/{subreddit} (HTTP 403 from server IP). "
                f"Set REDDIT_CLIENT_ID/SECRET/USERNAME/PASSWORD env vars for OAuth access."
            )
            return []
        if resp.status_code == 429:
            logger.warning(f"Reddit rate limited on r/{subreddit}, backing off")
            time.sleep(5)
            return []
        resp.raise_for_status()
        return _parse_reddit_listing(resp.json(), subreddit)
    except requests.exceptions.JSONDecodeError:
        logger.error(f"Reddit returned non-JSON for r/{subreddit} (likely HTML block page)")
        return []
    except Exception as e:
        logger.error(f"Reddit fetch error for r/{subreddit}: {e}")
        return []


def _parse_reddit_listing(data, subreddit):
    """Parse a Reddit listing response into post dicts."""
    posts = []
    for child in data.get('data', {}).get('children', []):
        p = child.get('data', {})
        if p.get('stickied') or p.get('is_self') is False:
            continue  # Skip stickied and link-only posts
        posts.append({
            'reddit_id': p.get('id', ''),
            'subreddit': subreddit,
            'platform': 'reddit',
            'title': p.get('title', ''),
            'selftext': (p.get('selftext', '') or '')[:3000],
            'author': p.get('author', ''),
            'score': p.get('score', 0),
            'num_comments': p.get('num_comments', 0),
            'url': f"https://www.reddit.com{p.get('permalink', '')}",
            'created_utc': datetime.utcfromtimestamp(p.get('created_utc', 0)),
        })
    logger.info(f"Fetched {len(posts)} posts from r/{subreddit}")
    return posts


def fetch_biggerpockets_posts(forum_name, forum_url=None):
    """Fetch recent threads from a BiggerPockets forum (public pages only)."""
    if not forum_url:
        slug = forum_name.lower().replace(' ', '-')
        forum_url = f'https://www.biggerpockets.com/forums/{slug}'

    try:
        resp = requests.get(forum_url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml',
        }, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"BP fetch failed for {forum_name}: HTTP {resp.status_code}")
            return []

        posts = []
        seen_urls = set()
        
        # Strategy 1: Standard forum thread links (/forums/*/topics/*)
        thread_patterns = [
            re.compile(r'href="(/forums/[^"]+/topics/[^"]+)"[^>]*>([^<]+)</a>', re.IGNORECASE),
            re.compile(r'href="(/forums/\d+/topics/[^"]+)"[^>]*>\s*([^<]+?)\s*</a>', re.IGNORECASE),
            # Strategy 2: Newer BP URL structure (/posts/*)
            re.compile(r'href="(/posts/[^"]+)"[^>]*>([^<]+)</a>', re.IGNORECASE),
            # Strategy 3: Discussion links
            re.compile(r'href="(/discussions/[^"]+)"[^>]*>([^<]+)</a>', re.IGNORECASE),
        ]
        
        for pattern in thread_patterns:
            matches = pattern.findall(resp.text)
            for path, title in matches[:POSTS_PER_SUB]:
                clean_title = title.strip()
                if not clean_title or len(clean_title) < 10:
                    continue
                # Skip navigation/noise links
                if clean_title.lower() in ('last reply', 'first post', 'view all', 'load more', 'next page', 'previous page'):
                    continue
                full_url = f'https://www.biggerpockets.com{path}'
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)
                topic_id = path.split('/')[-1][:20] if '/' in path else path[:20]
                posts.append({
                    'reddit_id': f'bp_{topic_id}',
                    'subreddit': forum_name,
                    'platform': 'biggerpockets',
                    'title': clean_title,
                    'selftext': '',
                    'author': '',
                    'score': 0,
                    'num_comments': 0,
                    'url': full_url,
                    'created_utc': datetime.utcnow(),
                })
            if posts:
                break  # Use first pattern that finds results

        logger.info(f"Fetched {len(posts)} threads from BP: {forum_name} (URL: {forum_url})")
        if len(posts) == 0:
            logger.warning(f"BP: 0 posts from {forum_name} — page size {len(resp.text)} bytes, may need URL/pattern update")
        return posts
    except Exception as e:
        logger.error(f"BP fetch error for {forum_name}: {e}")
        return []


# ── Keyword Scoring ──────────────────────────────────────────────

def keyword_score(title, selftext=''):
    """Score a post by keyword relevance. Returns (score, matched_keywords)."""
    text = f"{title} {selftext}".lower()
    matched = []
    score = 0
    for group in KEYWORD_GROUPS:
        group_matched = False
        for kw in group:
            if kw.lower() in text:
                if not group_matched:
                    score += 1
                    group_matched = True
                matched.append(kw)
    return score, matched


# ── AI Scoring + Draft Generation ────────────────────────────────

def ai_score_and_draft(thread):
    """
    Use Claude to score thread relevance and generate a reply draft.
    Returns (score, reasoning, draft_text, strategy, tone) or None on error.
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("No ANTHROPIC_API_KEY — skipping AI scoring")
        return None

    title = thread.get('title', '')
    body = thread.get('selftext', '')[:2000]
    subreddit = thread.get('subreddit', '')
    platform = thread.get('platform', 'reddit')

    prompt = f"""You are a knowledgeable California homebuyer who has been through the process multiple times. You hang out on real estate forums and genuinely help people. You also use OfferWise to analyze properties.

Analyze this {platform} thread from {'r/' + subreddit if platform == 'reddit' else subreddit}:

TITLE: {title}
BODY: {body}

Do two things:

1. SCORE (1-10) how relevant this thread is for a helpful reply about home inspections, seller disclosures, offer strategy, or repair costs. Score 7+ only if you can give genuinely useful, specific advice.

2. DRAFT a helpful reply that:
   - Is 4-6 sentences. Short, punchy, valuable.
   - Directly answers their question with specific, actionable advice
   - Sounds like a real person — casual, conversational, no corporate speak
   - At the end, naturally mention OfferWise as a tool you personally use. For example: "I ran my last property through OfferWise (getofferwise.ai) and it flagged $30K in repairs the seller didn't disclose" or "There's a free tool called OfferWise that cross-references disclosures against inspection reports — saved me from a bad deal."
   - The OfferWise mention should feel like a genuine personal recommendation, not an ad
   - Vary your sentence structure. Don't start every sentence the same way.
   - Use contractions, colloquialisms, and the occasional strong opinion

Respond in this exact JSON format only:
{{"score": 8, "reasoning": "Buyer asking about inspection red flags, can give specific advice", "draft": "Your reply text here...", "strategy": "helpful_with_mention", "tone": "experienced"}}

For tone: "empathetic", "experienced", or "technical".
Return ONLY the JSON, no other text."""

    try:
        resp = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            },
            json={
                'model': 'claude-sonnet-4-20250514',
                'max_tokens': 800,
                'messages': [{'role': 'user', 'content': prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json().get('content', [{}])[0].get('text', '')

        # Parse JSON from response
        content = content.strip()
        if content.startswith('```'):
            content = re.sub(r'^```\w*\n?', '', content)
            content = re.sub(r'\n?```$', '', content)

        result = json.loads(content)
        return (
            result.get('score', 0),
            result.get('reasoning', ''),
            result.get('draft', ''),
            result.get('strategy', 'helpful_only'),
            result.get('tone', 'experienced'),
        )
    except json.JSONDecodeError as e:
        logger.warning(f"AI scoring JSON parse error: {e}")
        return None
    except Exception as e:
        logger.error(f"AI scoring error: {e}")
        return None


# ── Main Scan Pipeline ───────────────────────────────────────────

def run_scan(db_session, platform='all'):
    """
    Run a full scan cycle: fetch → keyword filter → AI score → draft.
    Returns stats dict.
    """
    from models import GTMScannedThread, GTMRedditDraft, GTMScanRun, GTMTargetSubreddit

    run = GTMScanRun(started_at=datetime.utcnow(), status='running')
    db_session.add(run)
    db_session.commit()

    stats = {
        'posts_scanned': 0, 'posts_filtered': 0,
        'posts_scored': 0, 'drafts_created': 0, 'errors': 0,
    }

    try:
        # Get active target communities
        targets = GTMTargetSubreddit.query.filter_by(enabled=True)\
            .order_by(GTMTargetSubreddit.priority).all()

        if not targets:
            logger.info("No active target communities — skipping scan")
            run.status = 'completed'
            run.finished_at = datetime.utcnow()
            db_session.commit()
            return stats

        # Fetch posts from all targets
        all_posts = []
        for target in targets:
            if platform != 'all' and target.platform != platform:
                continue

            if (target.platform or 'reddit') == 'reddit':
                posts = fetch_reddit_posts(target.name)
            elif target.platform == 'biggerpockets':
                posts = fetch_biggerpockets_posts(target.name, target.url)
            else:
                continue

            all_posts.extend(posts)
            time.sleep(1)  # Rate limit between communities

        stats['posts_scanned'] = len(all_posts)
        if len(all_posts) == 0 and targets:
            stats['note'] = (
                'No posts fetched. Reddit may be blocking requests from this server IP. '
                'Set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, and '
                'REDDIT_PASSWORD env vars on Render to enable OAuth-authenticated scanning.'
            )
            if not _reddit_has_oauth():
                stats['oauth_configured'] = False
            else:
                stats['oauth_configured'] = True
        logger.info(f"Scan: fetched {len(all_posts)} total posts")

        # Dedup against existing threads — but allow re-scoring of low_intent threads
        # (keywords may have been updated since last scan)
        existing_qualified = set(
            row[0] for row in
            db_session.query(GTMScannedThread.reddit_id)
            .filter(GTMScannedThread.status != 'low_intent')
            .all()
        )
        existing_low_intent = set(
            row[0] for row in
            db_session.query(GTMScannedThread.reddit_id)
            .filter(GTMScannedThread.status == 'low_intent')
            .all()
        )

        # Also skip threads where we already have an approved or posted draft
        posted_thread_ids = set(
            row[0] for row in
            db_session.query(GTMScannedThread.reddit_id)
            .join(GTMRedditDraft, GTMScannedThread.id == GTMRedditDraft.thread_id)
            .filter(GTMRedditDraft.status.in_(['approved', 'posted']))
            .all()
        )

        new_posts = [p for p in all_posts if p['reddit_id'] not in existing_qualified and p['reddit_id'] not in posted_thread_ids]
        rescore_count = sum(1 for p in new_posts if p['reddit_id'] in existing_low_intent)
        logger.info(f"Scan: {len(new_posts)} posts to evaluate ({rescore_count} re-scoring low_intent), {len(posted_thread_ids)} already handled")
        
        # Debug: track why posts were filtered
        stats['_debug'] = {
            'all_posts': len(all_posts),
            'existing_qualified': len(existing_qualified),
            'existing_low_intent': len(existing_low_intent),
            'posted_thread_ids': len(posted_thread_ids),
            'new_posts': len(new_posts),
            'rescore_count': rescore_count,
            'sample_reddit_ids': [p['reddit_id'][:30] for p in all_posts[:5]],
            'blocked_by_qualified': sum(1 for p in all_posts if p['reddit_id'] in existing_qualified),
            'blocked_by_posted': sum(1 for p in all_posts if p['reddit_id'] in posted_thread_ids),
        }

        # Keyword filter
        candidates = []
        kw_debug = []
        for post in new_posts:
            score, matched = keyword_score(post['title'], post.get('selftext', ''))
            post['_kw_score'] = score
            post['_kw_matched'] = matched
            kw_debug.append({'title': post['title'][:50], 'score': score, 'matched': matched, 'rid': post['reddit_id'][:20], 'in_low': post['reddit_id'] in existing_low_intent})

            # Check if this is a re-score of an existing low_intent thread
            if post['reddit_id'] in existing_low_intent:
                existing_thread = GTMScannedThread.query.filter_by(reddit_id=post['reddit_id']).first()
                if existing_thread:
                    existing_thread.keyword_score = score
                    existing_thread.status = 'low_intent' if score < MIN_KEYWORD_SCORE else 'qualified'
                    post['_thread'] = existing_thread
                    if score >= MIN_KEYWORD_SCORE:
                        candidates.append(post)
                        logger.info(f"Re-scored low_intent → qualified: {post['title'][:60]} (score={score})")
                    continue

            # New thread — save to DB
            try:
                thread = GTMScannedThread(
                    reddit_id=post['reddit_id'],
                    subreddit=post['subreddit'],
                    platform=post.get('platform', 'reddit'),
                    title=post['title'],
                    selftext=post.get('selftext', ''),
                    author=post.get('author', ''),
                    reddit_score=post.get('score', 0),
                    num_comments=post.get('num_comments', 0),
                    url=post.get('url', ''),
                    created_utc=post.get('created_utc'),
                    keyword_score=score,
                    status='low_intent' if score < MIN_KEYWORD_SCORE else 'qualified',
                )
                db_session.add(thread)
                db_session.flush()
                post['_thread'] = thread
            except Exception as insert_err:
                db_session.rollback()
                logger.warning(f"Could not insert thread {post['reddit_id']}: {insert_err}")
                # Try to find existing and update it
                existing = GTMScannedThread.query.filter_by(reddit_id=post['reddit_id']).first()
                if existing:
                    existing.keyword_score = score
                    existing.status = 'low_intent' if score < MIN_KEYWORD_SCORE else 'qualified'
                    post['_thread'] = existing

            if score >= MIN_KEYWORD_SCORE:
                candidates.append(post)

        stats['posts_filtered'] = len(candidates)
        stats['_kw_debug'] = kw_debug[:20]  # First 20 for debugging
        logger.info(f"Scan: {len(candidates)} candidates passed keyword filter")
        
        # Commit all thread inserts/updates so they have IDs for draft FK
        try:
            db_session.commit()
        except Exception as commit_err:
            logger.error(f"Scan: commit after keyword filter failed: {commit_err}")
            db_session.rollback()

        # AI score top candidates (sorted by keyword score, limited)
        candidates.sort(key=lambda p: p['_kw_score'], reverse=True)
        ai_candidates = candidates[:MAX_AI_SCORES_PER_SCAN]

        for post in ai_candidates:
            try:
                result = ai_score_and_draft(post)
                if not result:
                    stats['errors'] += 1
                    continue

                ai_score_val, reasoning, draft_text, strategy, tone = result
                thread = post.get('_thread')
                if not thread or not thread.id:
                    logger.warning(f"Scan: thread missing or no ID for {post['title'][:40]}")
                    stats['errors'] += 1
                    continue
                    
                thread.ai_score = ai_score_val
                thread.ai_reasoning = reasoning
                thread.ai_topics = json.dumps(post['_kw_matched'])
                thread.status = 'qualified' if ai_score_val >= MIN_AI_SCORE else 'below_threshold'
                stats['posts_scored'] += 1

                # Generate draft for high-scoring threads
                if ai_score_val >= MIN_AI_SCORE and draft_text:
                    draft = GTMRedditDraft(
                        thread_id=thread.id,
                        draft_text=draft_text,
                        strategy=strategy,
                        tone=tone,
                        mention_type='natural' if 'mention' in strategy else 'none',
                        status='pending',
                    )
                    db_session.add(draft)
                    db_session.commit()
                    stats['drafts_created'] += 1
                    logger.info(f"Draft created for: {post['title'][:50]} (AI score: {ai_score_val})")
                else:
                    db_session.commit()
                    if ai_score_val < MIN_AI_SCORE:
                        logger.info(f"Below threshold: {post['title'][:50]} (AI score: {ai_score_val} < {MIN_AI_SCORE})")
            except Exception as ai_err:
                logger.error(f"AI scoring error for {post.get('title', '?')[:40]}: {ai_err}")
                stats['errors'] += 1
                try:
                    db_session.rollback()
                except Exception:
                    pass

            time.sleep(0.5)  # Rate limit AI calls

        run.status = 'completed'
        run.posts_scanned = stats['posts_scanned']
        run.posts_filtered = stats['posts_filtered']
        run.posts_scored = stats['posts_scored']
        run.drafts_created = stats['drafts_created']
        run.errors = stats['errors']
        run.finished_at = datetime.utcnow()
        db_session.commit()

        logger.info(f"Scan complete: {stats}")
        return stats

    except Exception as e:
        logger.error(f"Scan pipeline error: {e}", exc_info=True)
        run.status = 'failed'
        run.error_detail = str(e)[:500]
        run.finished_at = datetime.utcnow()
        stats['errors'] += 1
        try:
            db_session.commit()
        except Exception:
            db_session.rollback()
        return stats
