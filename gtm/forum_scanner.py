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
    ['first time buyer', 'first time home buyer', 'first home', 'first-time homebuyer', 'first-time home buyer', 'ftb', 'first deal', 'first property', 'first house', 'new buyer', 'first purchase'],
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
MIN_AI_SCORE = 7  # Only draft replies for clearly relevant threads
# Max threads to AI-score per scan (API cost control)
MAX_AI_SCORES_PER_SCAN = 25
# Max posts to fetch per subreddit per sort mode
POSTS_PER_SUB = 25
# Max age of posts to consider for reply drafts (hours)
MAX_POST_AGE_HOURS = 48  # 2 days — older threads are stale, replies won't get seen


# ── Reddit Fetching ──────────────────────────────────────────────

# Reddit blocks unauthenticated JSON requests from datacenter IPs.
# If REDDIT_CLIENT_ID/SECRET are set, use OAuth; otherwise fall back to public.
# Primary: dedicated scanner credentials (script app with password flow)
REDDIT_CLIENT_ID = os.environ.get('REDDIT_CLIENT_ID', '')
REDDIT_CLIENT_SECRET = os.environ.get('REDDIT_CLIENT_SECRET', '')
REDDIT_USERNAME = os.environ.get('REDDIT_USERNAME', '')
REDDIT_PASSWORD = os.environ.get('REDDIT_PASSWORD', '')

# Fallback: reuse Reddit Ads credentials with client_credentials grant (read-only)
# This avoids needing a separate script app — ads credentials work for public subreddit reads
REDDIT_ADS_CLIENT_ID = os.environ.get('REDDIT_ADS_CLIENT_ID', '')
REDDIT_ADS_CLIENT_SECRET = os.environ.get('REDDIT_ADS_CLIENT_SECRET', '')

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
    """True if script-app password-flow credentials are configured."""
    return all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD])

def _reddit_has_client_creds():
    """True if client_credentials flow is available (ads creds or dedicated creds)."""
    client_id = REDDIT_CLIENT_ID or REDDIT_ADS_CLIENT_ID
    client_secret = REDDIT_CLIENT_SECRET or REDDIT_ADS_CLIENT_SECRET
    return bool(client_id and client_secret)


def fetch_reddit_posts(subreddit, limit=POSTS_PER_SUB):
    """Fetch recent posts from a subreddit. Tries PullPush API first (works from any IP),
    then falls back to OAuth/client_creds/public JSON."""
    # Primary: PullPush (Pushshift replacement) — no auth needed, works from datacenter IPs
    posts = _fetch_reddit_pullpush(subreddit, limit)
    if posts:
        logger.info(f"[REDDIT] r/{subreddit}: {len(posts)} posts via PullPush")
        return posts

    # Fallback: Reddit API (OAuth → client_creds → public JSON)
    logger.info(f"[REDDIT] PullPush returned 0 for r/{subreddit}, trying Reddit API fallback")
    all_posts = {}
    for sort in ('new', 'hot'):
        if _reddit_has_oauth():
            fetched = _fetch_reddit_oauth(subreddit, limit, sort=sort)
        elif _reddit_has_client_creds():
            fetched = _fetch_reddit_client_creds(subreddit, limit, sort=sort)
        else:
            fetched = _fetch_reddit_public(subreddit, limit, sort=sort)
        for p in fetched:
            if p['reddit_id'] not in all_posts:
                all_posts[p['reddit_id']] = p
        time.sleep(0.5)
    result = list(all_posts.values())
    if result:
        logger.info(f"[REDDIT] r/{subreddit}: {len(result)} posts via Reddit API fallback")
    return result


def _fetch_reddit_pullpush(subreddit, limit):
    """Fetch posts via Arctic Shift API (live Reddit data, no auth needed).
    Falls back to PullPush if Arctic Shift is down."""
    cutoff = datetime.utcnow() - timedelta(hours=MAX_POST_AGE_HOURS)
    cutoff_ts = int(cutoff.timestamp())

    # Primary: Arctic Shift (has live data, unlike PullPush which lags months)
    try:
        url = 'https://arctic-shift.photon-reddit.com/api/posts/search'
        params = {
            'subreddit': subreddit,
            'limit': min(limit, 100),
            'sort': 'desc',
            'after': cutoff_ts,
        }
        resp = requests.get(url, params=params, timeout=20,
                           headers={'User-Agent': USER_AGENT})
        logger.info(f"[ARCTIC-SHIFT] r/{subreddit}: {resp.status_code} ({len(resp.content)} bytes)")
        if resp.status_code == 200:
            data = resp.json()
            posts = _parse_shift_data(data.get('data', []), subreddit, cutoff)
            if posts:
                logger.info(f"[ARCTIC-SHIFT] r/{subreddit}: {len(posts)} fresh posts")
                return posts
        else:
            logger.warning(f"[ARCTIC-SHIFT] r/{subreddit}: HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"[ARCTIC-SHIFT] r/{subreddit} error: {e}")

    # Fallback: PullPush (may have stale data but better than nothing)
    try:
        url = f'https://api.pullpush.io/reddit/search/submission/'
        params = {
            'subreddit': subreddit,
            'size': min(limit, 100),
            'sort': 'desc',
            'sort_type': 'created_utc',
            'after': cutoff_ts,
        }
        resp = requests.get(url, params=params, timeout=20,
                           headers={'User-Agent': USER_AGENT})
        logger.info(f"[PULLPUSH] r/{subreddit}: {resp.status_code} ({len(resp.content)} bytes)")
        if resp.status_code == 200:
            data = resp.json()
            posts = _parse_shift_data(data.get('data', []), subreddit, cutoff)
            if posts:
                logger.info(f"[PULLPUSH] r/{subreddit}: {len(posts)} fresh posts")
                return posts
    except Exception as e:
        logger.error(f"[PULLPUSH] r/{subreddit} error: {e}")

    return []


def _parse_shift_data(items, subreddit, cutoff):
    """Parse posts from Arctic Shift or PullPush API response."""
    posts = []
    for p in items:
        if p.get('is_self') is False and not p.get('selftext'):
            continue
        if p.get('stickied'):
            continue
        if p.get('removed_by_category'):
            continue
        created = datetime.utcfromtimestamp(p.get('created_utc', 0))
        if created < cutoff:
            continue
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
            'created_utc': created,
        })
    return posts


def _fetch_reddit_oauth(subreddit, limit, sort='new'):
    """Fetch posts using OAuth (works from datacenter IPs)."""
    try:
        token = _get_reddit_oauth_token()
        resp = requests.get(
            f'https://oauth.reddit.com/r/{subreddit}/{sort}?limit={limit}',
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


_cc_token = None
_cc_expires = 0
_cc_failed = False  # If True, skip client_creds and go straight to public

def _get_reddit_client_creds_token():
    """Get a read-only token via client_credentials grant — no user account needed."""
    global _cc_token, _cc_expires, _cc_failed
    if _cc_failed:
        return None
    if _cc_token and time.time() < _cc_expires - 60:
        return _cc_token
    client_id = REDDIT_CLIENT_ID or REDDIT_ADS_CLIENT_ID
    client_secret = REDDIT_CLIENT_SECRET or REDDIT_ADS_CLIENT_SECRET
    logger.info(f"[REDDIT-AUTH] Requesting client_credentials token. client_id={client_id[:8] if client_id else 'NONE'}... grant=client_credentials")
    try:
        resp = requests.post(
            'https://www.reddit.com/api/v1/access_token',
            auth=(client_id, client_secret),
            data={'grant_type': 'client_credentials'},
            headers={'User-Agent': USER_AGENT},
            timeout=15,
        )
        logger.info(f"[REDDIT-AUTH] Token response: {resp.status_code}")
        if resp.status_code != 200:
            logger.error(f"[REDDIT-AUTH] Token request failed: {resp.status_code} {resp.text[:200]}")
            logger.warning("[REDDIT-AUTH] Marking client_creds as failed — will use public JSON fallback")
            _cc_failed = True
            return None
        data = resp.json()
        _cc_token = data.get('access_token')
        _cc_expires = time.time() + data.get('expires_in', 3600)
        logger.info(f"[REDDIT-AUTH] Token acquired, expires in {data.get('expires_in', 0)}s")
        return _cc_token
    except Exception as e:
        logger.error(f"[REDDIT-AUTH] Token acquisition FAILED: {e}")
        logger.warning("[REDDIT-AUTH] Marking client_creds as failed — will use public JSON fallback")
        _cc_failed = True
        return None


def _fetch_reddit_client_creds(subreddit, limit, sort='new'):
    """Fetch posts using client_credentials. Falls back to public JSON if token fails."""
    token = _get_reddit_client_creds_token()
    if not token:
        # Token acquisition failed — fall back to public JSON
        logger.info(f"[REDDIT-FETCH] client_creds token unavailable, falling back to public JSON for r/{subreddit}/{sort}")
        return _fetch_reddit_public(subreddit, limit, sort=sort)
    try:
        url = f'https://oauth.reddit.com/r/{subreddit}/{sort}?limit={limit}'
        logger.info(f"[REDDIT-FETCH] GET {url} (token={token[:10] if token else 'NONE'}...)")
        resp = requests.get(
            url,
            headers={'Authorization': f'bearer {token}', 'User-Agent': USER_AGENT},
            timeout=15,
        )
        logger.info(f"[REDDIT-FETCH] r/{subreddit}/{sort}: {resp.status_code} ({len(resp.content)} bytes)")
        if resp.status_code == 429:
            logger.warning(f"Reddit rate limited on r/{subreddit}")
            time.sleep(5)
            return []
        if resp.status_code == 403:
            logger.error(f"[REDDIT-FETCH] 403 on r/{subreddit}/{sort} — falling back to public JSON")
            return _fetch_reddit_public(subreddit, limit, sort=sort)
        resp.raise_for_status()
        posts = _parse_reddit_listing(resp.json(), subreddit)
        logger.info(f"[REDDIT-FETCH] r/{subreddit}/{sort}: parsed {len(posts)} posts")
        return posts
    except Exception as e:
        logger.error(f"Reddit client_creds fetch error for r/{subreddit}/{sort}: {e}")
        logger.info(f"[REDDIT-FETCH] Falling back to public JSON for r/{subreddit}/{sort}")
        return _fetch_reddit_public(subreddit, limit, sort=sort)


def _fetch_reddit_public(subreddit, limit, sort='new'):
    """Fetch posts using public JSON API. Tries old.reddit.com first (more reliable from servers)."""
    # Try old.reddit.com first — more permissive with server IPs
    urls = [
        f'https://old.reddit.com/r/{subreddit}/{sort}.json?limit={limit}&raw_json=1',
        f'https://www.reddit.com/r/{subreddit}/{sort}.json?limit={limit}&raw_json=1',
    ]
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/html',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    for url in urls:
        try:
            resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            logger.info(f"[REDDIT-PUBLIC] {url[:60]}... → {resp.status_code} ({len(resp.content)} bytes)")
            if resp.status_code == 403:
                logger.warning(f"[REDDIT-PUBLIC] 403 on {url[:60]}... trying next")
                continue
            if resp.status_code == 429:
                logger.warning(f"Reddit rate limited on r/{subreddit}, backing off")
                time.sleep(5)
                continue
            resp.raise_for_status()
            data = resp.json()
            posts = _parse_reddit_listing(data, subreddit)
            if posts:
                logger.info(f"[REDDIT-PUBLIC] r/{subreddit}/{sort}: {len(posts)} posts via public JSON")
                return posts
        except requests.exceptions.JSONDecodeError:
            logger.error(f"[REDDIT-PUBLIC] Non-JSON response for r/{subreddit}/{sort} (likely HTML block page)")
            continue
        except Exception as e:
            logger.error(f"[REDDIT-PUBLIC] Error for r/{subreddit}/{sort}: {e}")
            continue
    logger.warning(f"[REDDIT-PUBLIC] All attempts failed for r/{subreddit}/{sort}")
    return []


def _parse_reddit_listing(data, subreddit):
    """Parse a Reddit listing response into post dicts. Skips posts older than MAX_POST_AGE_HOURS."""
    cutoff = datetime.utcnow() - timedelta(hours=MAX_POST_AGE_HOURS)
    posts = []
    skipped_old = 0
    for child in data.get('data', {}).get('children', []):
        p = child.get('data', {})
        if p.get('stickied') or p.get('is_self') is False:
            continue  # Skip stickied and link-only posts
        created = datetime.utcfromtimestamp(p.get('created_utc', 0))
        if created < cutoff:
            skipped_old += 1
            continue
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
            'created_utc': created,
        })
    if skipped_old:
        logger.info(f"r/{subreddit}: skipped {skipped_old} posts older than {MAX_POST_AGE_HOURS}h")
    logger.info(f"Fetched {len(posts)} fresh posts from r/{subreddit}")
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

1. SCORE (1-10) how relevant this thread is for a helpful reply about home inspections, seller disclosures, offer strategy, or repair costs. Be strict:
   - Score 8-10: Thread is directly about buying a home, inspections, disclosures, offer negotiations, or repair costs. You can give specific, high-value advice.
   - Score 5-7: Thread is tangentially related. Only score this high if the connection is obvious.
   - Score 1-4: Thread is NOT about homebuying. Career advice, investment theory, landlord issues, or anything where homebuying is not the main topic = score 3 or below.
   - IMPORTANT: If the thread is asking about a career, job, or business (e.g. "should I become an inspector?") score it 2 or below — do NOT reply to these.

2. DRAFT a helpful reply that:
   - Is 4-6 sentences. Short, punchy, valuable.
   - Directly answers their question with specific, actionable advice
   - Sounds like a real person — casual, conversational, no corporate speak
   - At the end, optionally mention OfferWise as a tool you personally use — but NEVER include a URL or website link. Example: "I ran my last property through OfferWise and it flagged $30K in repairs the seller didn't disclose" or "There's a free tool called OfferWise that cross-references disclosures against inspection reports — saved me from a bad deal." The name alone is enough — Reddit will ban the account for promotional links.
   - The OfferWise mention should feel like a genuine personal recommendation, not an ad. If the mention feels forced or the thread doesn't connect well, omit it entirely.
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
    Returns stats dict with comprehensive debug info.
    """
    from models import GTMScannedThread, GTMRedditDraft, GTMScanRun, GTMTargetSubreddit

    run = GTMScanRun(started_at=datetime.utcnow(), status='running')
    db_session.add(run)
    db_session.commit()

    stats = {
        'posts_scanned': 0, 'posts_filtered': 0,
        'posts_scored': 0, 'drafts_created': 0, 'errors': 0,
    }
    debug_log = []

    def _log(msg):
        logger.info(f"[GTM-SCAN] {msg}")
        debug_log.append(msg)

    try:
        # ── STEP 1: Get target communities ──────────────────────
        targets = GTMTargetSubreddit.query.filter_by(enabled=True)\
            .order_by(GTMTargetSubreddit.priority).all()

        target_names = [f"{t.platform or 'reddit'}:{t.name}" for t in targets]
        _log(f"STEP 1 — Targets: {len(targets)} active: {target_names}")

        if not targets:
            _log("ABORT — No active target communities. Add subreddits in admin UI.")
            run.status = 'completed'
            run.finished_at = datetime.utcnow()
            db_session.commit()
            stats['debug_log'] = debug_log
            return stats

        # ── STEP 2: Fetch posts ─────────────────────────────────
        all_posts = []
        fetch_details = []
        for target in targets:
            if platform != 'all' and target.platform != platform:
                _log(f"  SKIP {target.name} (platform={target.platform}, scanning={platform})")
                continue

            platform_key = (target.platform or 'reddit')
            _log(f"  FETCH {platform_key}:{target.name}...")

            try:
                if platform_key == 'reddit':
                    auth_method = 'oauth' if _reddit_has_oauth() else ('client_creds' if _reddit_has_client_creds() else 'public_json')
                    _log(f"    Reddit auth: {auth_method}")
                    _log(f"      REDDIT_CLIENT_ID={'SET('+REDDIT_CLIENT_ID[:8]+'...)' if REDDIT_CLIENT_ID else 'MISSING'}")
                    _log(f"      REDDIT_CLIENT_SECRET={'SET' if REDDIT_CLIENT_SECRET else 'MISSING'}")
                    _log(f"      REDDIT_ADS_CLIENT_ID={'SET('+REDDIT_ADS_CLIENT_ID[:8]+'...)' if REDDIT_ADS_CLIENT_ID else 'MISSING'}")
                    _log(f"      REDDIT_ADS_CLIENT_SECRET={'SET' if REDDIT_ADS_CLIENT_SECRET else 'MISSING'}")
                    _log(f"      Effective client_id={'SET('+((REDDIT_CLIENT_ID or REDDIT_ADS_CLIENT_ID) or 'NONE')[:8]+'...)' if (REDDIT_CLIENT_ID or REDDIT_ADS_CLIENT_ID) else 'NONE'}")
                    posts = fetch_reddit_posts(target.name)
                elif platform_key == 'biggerpockets':
                    posts = fetch_biggerpockets_posts(target.name, target.url)
                elif platform_key == 'facebook':
                    group = {'name': target.name, 'url': target.url or '', 'id': target.name.lower().replace(' ', '_')}
                    posts = fetch_facebook_group_posts(group)
                elif platform_key == 'nextdoor':
                    neighborhood = {'name': target.name, 'url': target.url or '', 'id': target.name.lower().replace(' ', '_')}
                    posts = fetch_nextdoor_posts(neighborhood)
                else:
                    posts = []

                fetch_details.append({'target': f"{platform_key}:{target.name}", 'count': len(posts)})
                _log(f"    → {len(posts)} posts fetched")
                if posts:
                    _log(f"    Sample: \"{posts[0]['title'][:70]}\" by {posts[0].get('author','?')} ({posts[0].get('num_comments',0)} comments)")
                all_posts.extend(posts)
            except Exception as fetch_err:
                _log(f"    ❌ FETCH ERROR: {fetch_err}")
                stats['errors'] += 1

            time.sleep(1)

        stats['posts_scanned'] = len(all_posts)
        stats['fetch_details'] = fetch_details
        _log(f"STEP 2 — Total: {len(all_posts)} posts from {len(fetch_details)} communities")

        if len(all_posts) == 0:
            _log("ABORT — Zero posts fetched.")
            _log(f"  oauth={_reddit_has_oauth()} client_creds={_reddit_has_client_creds()}")
            _log(f"  REDDIT_CLIENT_ID={'SET' if REDDIT_CLIENT_ID else 'MISSING'}")
            _log(f"  REDDIT_ADS_CLIENT_ID={'SET' if REDDIT_ADS_CLIENT_ID else 'MISSING'}")
            _log("  Reddit blocks unauthenticated requests from datacenter IPs.")
            stats['note'] = 'No posts fetched. See debug_log.'
            stats['oauth_configured'] = _reddit_has_oauth()
            run.status = 'completed'
            run.finished_at = datetime.utcnow()
            db_session.commit()
            stats['debug_log'] = debug_log
            return stats

        # ── STEP 2b: Freshness filter — discard posts older than MAX_POST_AGE_HOURS ──
        _freshness_cutoff = datetime.utcnow() - timedelta(hours=MAX_POST_AGE_HOURS)
        before_count = len(all_posts)
        fresh_posts = []
        stale_count = 0
        for p in all_posts:
            post_time = p.get('created_utc')
            if post_time and isinstance(post_time, datetime) and post_time < _freshness_cutoff:
                stale_count += 1
            else:
                fresh_posts.append(p)
        all_posts = fresh_posts
        _log(f"STEP 2b — Freshness: {before_count} → {len(all_posts)} (discarded {stale_count} older than {MAX_POST_AGE_HOURS}h)")

        if len(all_posts) == 0:
            _log("ABORT — All posts older than freshness cutoff.")
            run.status = 'completed'
            run.finished_at = datetime.utcnow()
            db_session.commit()
            stats['debug_log'] = debug_log
            return stats

        # ── STEP 3: Dedup ───────────────────────────────────────
        _three_days_ago = datetime.utcnow() - timedelta(days=3)

        recently_scanned = set(
            row[0] for row in
            db_session.query(GTMScannedThread.reddit_id)
            .filter(GTMScannedThread.scanned_at >= _three_days_ago)
            .all()
        )
        has_draft_ids = set(
            row[0] for row in
            db_session.query(GTMScannedThread.reddit_id)
            .join(GTMRedditDraft, GTMScannedThread.id == GTMRedditDraft.thread_id)
            .all()
        )

        blocked_recent = sum(1 for p in all_posts if p['reddit_id'] in recently_scanned)
        blocked_draft = sum(1 for p in all_posts if p['reddit_id'] in has_draft_ids)
        new_posts = [p for p in all_posts if p['reddit_id'] not in recently_scanned and p['reddit_id'] not in has_draft_ids]

        _log(f"STEP 3 — Dedup: {len(all_posts)} → {len(new_posts)} new")
        _log(f"  Blocked recently scanned (<3d): {blocked_recent} (DB has {len(recently_scanned)} recent)")
        _log(f"  Blocked has draft: {blocked_draft} (DB has {len(has_draft_ids)} with drafts)")

        stats['_debug'] = {
            'all_posts': len(all_posts),
            'recently_scanned': len(recently_scanned),
            'has_draft_ids': len(has_draft_ids),
            'new_posts': len(new_posts),
            'blocked_by_recent': blocked_recent,
            'blocked_by_draft': blocked_draft,
        }

        if len(new_posts) == 0:
            _log("ABORT — All posts filtered by dedup.")
            _log(f"  Sample fetched IDs: {[p['reddit_id'][:15] for p in all_posts[:5]]}")
            run.status = 'completed'
            run.finished_at = datetime.utcnow()
            db_session.commit()
            stats['debug_log'] = debug_log
            return stats

        # ── STEP 4: Keyword filter ──────────────────────────────
        candidates = []
        kw_pass = []
        kw_fail = []
        for post in new_posts:
            score, matched = keyword_score(post['title'], post.get('selftext', ''))
            post['_kw_score'] = score
            post['_kw_matched'] = matched

            existing_thread = GTMScannedThread.query.filter_by(reddit_id=post['reddit_id']).first()
            if existing_thread:
                existing_thread.keyword_score = score
                existing_thread.status = 'low_intent' if score < MIN_KEYWORD_SCORE else 'qualified'
                existing_thread.scanned_at = datetime.utcnow()
                post['_thread'] = existing_thread
            else:
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
                    _log(f"  ❌ DB insert failed {post['reddit_id']}: {insert_err}")
                    stats['errors'] += 1
                    continue

            if score >= MIN_KEYWORD_SCORE:
                candidates.append(post)
                kw_pass.append({'title': post['title'][:70], 'score': score, 'matched': matched})
            else:
                kw_fail.append({'title': post['title'][:70], 'score': score})

        stats['posts_filtered'] = len(candidates)
        _log(f"STEP 4 — Keywords: {len(new_posts)} → {len(candidates)} passed (threshold={MIN_KEYWORD_SCORE})")
        for kp in kw_pass[:10]:
            _log(f"  ✅ [{kp['score']}] \"{kp['title']}\" → {kp['matched']}")
        for kf in kw_fail[:8]:
            _log(f"  ❌ [{kf['score']}] \"{kf['title']}\"")
        if len(kw_fail) > 8:
            _log(f"  ... +{len(kw_fail)-8} more failed")

        stats['_kw_debug'] = kw_pass[:20]

        try:
            db_session.commit()
        except Exception as commit_err:
            _log(f"  ❌ Commit failed: {commit_err}")
            db_session.rollback()

        if len(candidates) == 0:
            _log("ABORT — Zero keyword matches.")
            _log(f"  Keyword groups (first word each): {[g[0] for g in KEYWORD_GROUPS]}")
            run.status = 'completed'
            run.finished_at = datetime.utcnow()
            db_session.commit()
            stats['debug_log'] = debug_log
            return stats

        # ── STEP 5: AI scoring ──────────────────────────────────
        candidates.sort(key=lambda p: p['_kw_score'], reverse=True)
        ai_candidates = candidates[:MAX_AI_SCORES_PER_SCAN]
        _log(f"STEP 5 — AI scoring {len(ai_candidates)} candidates (max={MAX_AI_SCORES_PER_SCAN})")
        _log(f"  ANTHROPIC_API_KEY: {'SET (' + ANTHROPIC_API_KEY[:8] + '...)' if ANTHROPIC_API_KEY else 'MISSING'}")

        if not ANTHROPIC_API_KEY:
            _log("ABORT — No ANTHROPIC_API_KEY. Cannot score or draft.")
            stats['note'] = 'ANTHROPIC_API_KEY not set.'
            run.status = 'completed'
            run.finished_at = datetime.utcnow()
            db_session.commit()
            stats['debug_log'] = debug_log
            return stats

        ai_results = []
        for i, post in enumerate(ai_candidates):
            _log(f"  [{i+1}/{len(ai_candidates)}] \"{post['title'][:65]}\" (kw={post['_kw_score']})...")
            try:
                result = ai_score_and_draft(post)
                if not result:
                    _log(f"    ❌ AI returned None (API error or JSON parse failure)")
                    stats['errors'] += 1
                    continue

                ai_score_val, reasoning, draft_text, strategy, tone = result
                thread = post.get('_thread')
                if not thread or not thread.id:
                    _log(f"    ❌ Thread object missing or has no ID")
                    stats['errors'] += 1
                    continue

                thread.ai_score = ai_score_val
                thread.ai_reasoning = reasoning
                thread.ai_topics = json.dumps(post['_kw_matched'])
                thread.status = 'qualified' if ai_score_val >= MIN_AI_SCORE else 'below_threshold'
                stats['posts_scored'] += 1

                ai_results.append({
                    'title': post['title'][:60],
                    'ai_score': ai_score_val,
                    'reasoning': reasoning[:100],
                    'drafted': ai_score_val >= MIN_AI_SCORE and bool(draft_text),
                })

                if ai_score_val >= MIN_AI_SCORE and draft_text:
                    draft = GTMRedditDraft(
                        thread_id=thread.id,
                        draft_text=draft_text,
                        strategy=strategy,
                        tone=tone,
                        mention_type='natural' if 'mention' in strategy else 'none',
                        status='approved',  # v5.85.22: All drafts auto-approved — single-step workflow
                    )
                    db_session.add(draft)
                    db_session.commit()
                    stats['drafts_created'] += 1
                    _log(f"    ✅ AI={ai_score_val} → DRAFT {draft_status.upper()}: \"{draft_text[:90]}...\"")
                else:
                    db_session.commit()
                    _log(f"    ⬇️  AI={ai_score_val} (need {MIN_AI_SCORE}) → no draft. {reasoning[:80]}")
            except Exception as ai_err:
                _log(f"    ❌ EXCEPTION: {ai_err}")
                stats['errors'] += 1
                try:
                    db_session.rollback()
                except Exception:
                    pass

            time.sleep(0.5)

        stats['ai_results'] = ai_results
        _log(f"STEP 5 done — scored={stats['posts_scored']} drafts={stats['drafts_created']}")

        # ── STEP 6: Finalize ────────────────────────────────────
        run.status = 'completed'
        run.posts_scanned = stats['posts_scanned']
        run.posts_filtered = stats['posts_filtered']
        run.posts_scored = stats['posts_scored']
        run.drafts_created = stats['drafts_created']
        run.errors = stats['errors']
        run.finished_at = datetime.utcnow()
        db_session.commit()

        _log(f"DONE — scanned={stats['posts_scanned']} filtered={stats['posts_filtered']} scored={stats['posts_scored']} drafts={stats['drafts_created']} errors={stats['errors']}")
        stats['debug_log'] = debug_log
        return stats

    except Exception as e:
        _log(f"PIPELINE CRASH: {e}")
        logger.error(f"Scan pipeline error: {e}", exc_info=True)
        run.status = 'failed'
        run.error_detail = str(e)[:500]
        run.finished_at = datetime.utcnow()
        stats['errors'] += 1
        try:
            db_session.commit()
        except Exception:
            db_session.rollback()
        stats['debug_log'] = debug_log
        return stats


# ── Facebook Group Scanner ────────────────────────────────────────────────

FACEBOOK_GROUPS = [
    # Public CA real estate / homebuyer groups (public = scrapeable)
    {
        'name': 'First Time Home Buyers - California',
        'url':  'https://www.facebook.com/groups/firsttimehomebuyerscalifornia',
        'id':   'firsttimehomebuyerscalifornia',
    },
    {
        'name': 'Bay Area Real Estate - Buyers & Sellers',
        'url':  'https://www.facebook.com/groups/bayarearealestate',
        'id':   'bayarearealestate',
    },
    {
        'name': 'San Jose Real Estate',
        'url':  'https://www.facebook.com/groups/sanjoserealestate',
        'id':   'sanjoserealestate',
    },
    {
        'name': 'California Home Buyers Network',
        'url':  'https://www.facebook.com/groups/cahomebuyersnetwork',
        'id':   'cahomebuyersnetwork',
    },
    {
        'name': 'Real Estate Buyers - Bay Area',
        'url':  'https://www.facebook.com/groups/realestatebuyers.bayarea',
        'id':   'realestatebuyers.bayarea',
    },
]

def fetch_facebook_group_posts(group: dict, limit: int = 20) -> list:
    """
    Fetch recent public posts from a Facebook group.
    Facebook public groups render a subset of posts without login.
    Falls back gracefully — returns [] if blocked.
    """
    url = group.get('url', '')
    group_name = group.get('name', url)

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code in (302, 301):
            logger.info(f"Facebook group {group_name}: redirected (likely requires login)")
            return []
        if resp.status_code != 200:
            logger.warning(f"Facebook group {group_name}: HTTP {resp.status_code}")
            return []

        posts = []
        text = resp.text

        # Strategy 1: JSON-LD structured data
        import json as _json
        json_ld = re.findall(r'<script type="application/ld\+json">(.*?)</script>', text, re.DOTALL)
        for blob in json_ld:
            try:
                data = _json.loads(blob)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get('@type') in ('SocialMediaPosting', 'Article', 'Post'):
                        title = item.get('headline', item.get('name', ''))[:200]
                        body  = item.get('articleBody', item.get('description', ''))[:2000]
                        post_url = item.get('url', url)
                        if title or body:
                            posts.append({
                                'reddit_id':    f"fb_{group['id']}_{len(posts)}",
                                'subreddit':    group_name,
                                'platform':     'facebook',
                                'title':        title or body[:80],
                                'selftext':     body,
                                'author':       '',
                                'score':        0,
                                'num_comments': 0,
                                'url':          post_url,
                                'created_utc':  datetime.utcnow(),
                            })
            except Exception:
                pass

        # Strategy 2: OG / meta content patterns
        if not posts:
            title_matches = re.findall(r'<span[^>]*dir="auto"[^>]*>([^<]{20,300})</span>', text)
            seen = set()
            for t in title_matches[:limit]:
                t = t.strip()
                if t and t not in seen and len(t) > 20:
                    seen.add(t)
                    posts.append({
                        'reddit_id':    f"fb_{group['id']}_{len(posts)}",
                        'subreddit':    group_name,
                        'platform':     'facebook',
                        'title':        t[:200],
                        'selftext':     '',
                        'author':       '',
                        'score':        0,
                        'num_comments': 0,
                        'url':          url,
                        'created_utc':  datetime.utcnow(),
                    })

        logger.info(f"Facebook {group_name}: {len(posts)} posts fetched")
        if not posts:
            logger.info(f"Facebook {group_name}: 0 posts — group may require login. Add FB_GROUP_COOKIE env var for authenticated access.")
        return posts[:limit]

    except Exception as e:
        logger.error(f"Facebook fetch error for {group_name}: {e}")
        return []


# ── Nextdoor Scanner ──────────────────────────────────────────────────────

NEXTDOOR_NEIGHBORHOODS = [
    {
        'name': 'Nextdoor San Jose',
        'url':  'https://nextdoor.com/city/san-jose--ca/',
        'id':   'nextdoor_sj',
    },
    {
        'name': 'Nextdoor Bay Area Real Estate',
        'url':  'https://nextdoor.com/topics/real-estate/',
        'id':   'nextdoor_bayarea_re',
    },
    {
        'name': 'Nextdoor Los Angeles',
        'url':  'https://nextdoor.com/city/los-angeles--ca/',
        'id':   'nextdoor_la',
    },
]

def fetch_nextdoor_posts(neighborhood: dict, limit: int = 20) -> list:
    """
    Fetch public Nextdoor posts from a neighborhood or topic page.
    Nextdoor requires login for full content — public pages return minimal data.
    Returns what's available; logs clearly when login is required.
    """
    url  = neighborhood.get('url', '')
    name = neighborhood.get('name', url)

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml',
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"Nextdoor {name}: HTTP {resp.status_code} — likely requires login")
            return []

        text = resp.text
        posts = []

        # Extract post titles / content from public-facing markup
        import json as _json

        # Strategy: JSON-LD
        json_ld = re.findall(r'<script type="application/ld\+json">(.*?)</script>', text, re.DOTALL)
        for blob in json_ld:
            try:
                data = _json.loads(blob)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    title = item.get('headline', item.get('name', ''))[:200]
                    body  = item.get('description', item.get('articleBody', ''))[:2000]
                    if title:
                        posts.append({
                            'reddit_id':    f"nd_{neighborhood['id']}_{len(posts)}",
                            'subreddit':    name,
                            'platform':     'nextdoor',
                            'title':        title,
                            'selftext':     body,
                            'author':       '',
                            'score':        0,
                            'num_comments': 0,
                            'url':          url,
                            'created_utc':  datetime.utcnow(),
                        })
            except Exception:
                pass

        # Strategy 2: h2/h3 post titles
        if not posts:
            titles = re.findall(r'<(?:h2|h3)[^>]*>([^<]{15,200})</(?:h2|h3)>', text)
            for t in titles[:limit]:
                t = t.strip()
                if t:
                    posts.append({
                        'reddit_id':    f"nd_{neighborhood['id']}_{len(posts)}",
                        'subreddit':    name,
                        'platform':     'nextdoor',
                        'title':        t[:200],
                        'selftext':     '',
                        'author':       '',
                        'score':        0,
                        'num_comments': 0,
                        'url':          url,
                        'created_utc':  datetime.utcnow(),
                    })

        logger.info(f"Nextdoor {name}: {len(posts)} posts fetched")
        if not posts:
            logger.info(f"Nextdoor {name}: 0 posts — requires authenticated session. "
                        f"Set NEXTDOOR_SESSION_COOKIE env var to enable full access.")
        return posts[:limit]

    except Exception as e:
        logger.error(f"Nextdoor fetch error for {name}: {e}")
        return []
