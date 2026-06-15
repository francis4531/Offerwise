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
    # Repair costs (v5.89.62: + contractor quotes/bids)
    ['repair cost', 'repair estimate', 'how much to fix', 'renovation cost', 'repair', 'replace roof', 'fix foundation', 'plumbing', 'electrical', 'hvac', 'contractor estimate', 'contractor quote', 'bids for', 'getting bids'],
    # First-time buyer
    ['first time buyer', 'first time home buyer', 'first home', 'first-time homebuyer', 'first-time home buyer', 'ftb', 'first deal', 'first property', 'first house', 'new buyer', 'first purchase', '1st deal', 'buying my first'],
    # Negotiation
    ['negotiate', 'negotiation', 'contingency', 'repair credit', 'seller concession', 'closing cost', 'price reduction', 'counter offer', 'contingency period', 'inspection contingency', 'appraisal contingency'],
    # California-specific
    ['california', 'bay area', 'san jose', 'los angeles', 'san francisco', 'socal', 'norcal', 'san diego', 'sacramento', 'oakland'],
    # Property condition / due diligence
    ['old house', 'old home', 'property condition', 'due diligence', 'too old', 'fixer', 'fixer upper', 'as-is', 'needs work', 'older property', 'older home', 'older house', 'pre-war', 'prewar'],
    # Buying process (v5.89.62: + pre-approval)
    ['home buying', 'buying a house', 'house hunting', 'under contract', 'escrow', 'appraisal', 'closing', 'earnest money', 'pre-approval', 'preapproval', 'pre-approved', 'home buyer'],
    # v5.89.62: Investor due-diligence vocabulary. Captures BP-style threads
    # where the OP is evaluating a property/deal — overlaps with OfferWise's
    # value prop (condition, repair cost, contradiction detection).
    ['capex', 'cap ex', 'capital expenditure', 'capital expenses', 'arv', 'after repair value', 'after-repair', 'rehab', 'rehab cost', 'rehab budget', 'rehab estimate', 'walkthrough', 'walk through', 'walk-through', 'final walk', 'deal analysis', 'analyzing a deal', 'analyze the deal', 'evaluating property', 'evaluating the deal'],
    # v5.89.62: Property type & age — investors buying older multi-family
    # are exactly OfferWise's repeat-customer profile (need disclosure
    # analysis + repair-cost prediction on every acquisition).
    ['multi-family', 'multifamily', 'multi family', 'duplex', 'triplex', 'fourplex', '4-plex', '1920s', '1930s', '1940s', '1950s', '1960s', '1900s', 'house hack', 'house hacking', 'househack'],
    # v5.89.62: Financing & alternative deal structures. Buyers and small
    # investors using FHA / VA / DPA / owner-financed / lease-to-own all
    # face the same condition-assessment problem.
    ['fha', 'fha loan', 'conventional loan', 'va loan', 'usda loan', 'down payment grant', 'down payment assistance', 'dpa', 'lease to own', 'lease-to-own', 'rent to own', 'rent-to-own', 'owner financing', 'seller financing', 'owner-financed', 'wholesaler', 'wholesalers', 'wholesale deal', 'assignment fee'],
]

# Flatten for quick matching
ALL_KEYWORDS = set()
for group in KEYWORD_GROUPS:
    for kw in group:
        ALL_KEYWORDS.add(kw.lower())

# Minimum keyword score to pass to AI scoring
MIN_KEYWORD_SCORE = 1
# Minimum AI score (1-10) to generate a draft
MIN_AI_SCORE = 6  # v5.89.61: lowered from 7 to allow marginal-but-relevant
                  # threads through. Paired with BP body-fetching (this same
                  # release) which gives the AI substance to score on. Reddit
                  # quality should stay fine since Reddit posts already had bodies.
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


def fetch_biggerpockets_body(thread_url, max_chars=2000):
    """v5.89.61: fetch a single BP topic page and extract the OP post body.

    Called only for keyword-matched candidates (not every fetched thread)
    to keep total scan time manageable. Without bodies the AI scorer was
    reasoning about titles alone and consistently rejected threads, so
    drafts_created was 0.

    Returns the OP body text (up to max_chars) or empty string on any
    failure — caller treats empty as "no body available" which is the
    existing behavior, so failures are non-blocking.
    """
    if not thread_url:
        return ''
    try:
        resp = requests.get(thread_url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml',
        }, timeout=10)
        if resp.status_code != 200:
            return ''
        html = resp.text

        # Strategy 1: Open Graph description meta tag — BP populates this with
        # the OP's first paragraph or two. Most reliable across HTML changes.
        og_match = re.search(
            r'<meta\s+(?:property|name)="og:description"\s+content="([^"]+)"',
            html, re.IGNORECASE)
        if og_match:
            body = og_match.group(1).strip()
            # HTML-entity-decode common entities (BP uses &#39; for apostrophes etc.)
            body = (body.replace('&#39;', "'").replace('&quot;', '"')
                        .replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>'))
            if len(body) >= 40:  # Sanity check — too short = probably a generic page description
                return body[:max_chars]

        # Strategy 2: Look for the first <p> inside what looks like an OP post block.
        # BP uses various markup; cover common patterns.
        op_patterns = [
            re.compile(r'<div[^>]*class="[^"]*(?:original-post|opening-post|first-post|topic-content)[^"]*"[^>]*>(.*?)</div>', re.DOTALL | re.IGNORECASE),
            re.compile(r'<article[^>]*>(.*?)</article>', re.DOTALL | re.IGNORECASE),
        ]
        for pat in op_patterns:
            m = pat.search(html)
            if m:
                block = m.group(1)
                # Strip tags, collapse whitespace
                text = re.sub(r'<[^>]+>', ' ', block)
                text = (text.replace('&#39;', "'").replace('&quot;', '"')
                            .replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
                            .replace('&nbsp;', ' '))
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) >= 40:
                    return text[:max_chars]

        # Strategy 3: Last-ditch — grab description from any meta tag
        desc_match = re.search(
            r'<meta\s+name="description"\s+content="([^"]+)"',
            html, re.IGNORECASE)
        if desc_match:
            body = desc_match.group(1).strip()
            body = (body.replace('&#39;', "'").replace('&quot;', '"')
                        .replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>'))
            if len(body) >= 40:
                return body[:max_chars]

        return ''
    except Exception as e:
        logger.warning(f"BP body fetch failed for {thread_url}: {e}")
        return ''


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

BANNED_PHRASES_IN_DRAFT = [
    # AI-confidence words
    'absolutely', 'definitely', 'literally',
    # AI-hedge/opener words
    'honestly,', 'honestly ', 'look,', 'listen,',
    # AI manual-tone phrases
    'your best bet', "i'd recommend", 'i would recommend',
    "i'd suggest", 'i would suggest',
    'key thing', 'main thing', 'the thing is',
    # AI buzzwords
    'ensure ', 'navigate ', 'leverage ', 'stakeholder',
    # AI consequence-closing patterns
    "torpedo your", "you'll regret", "don't want to deal",
    # AI well-wishing closes
    'hope this helps', 'good luck', 'best of luck',
    'hope it works out', 'hope that helps',
    # Manual-tone framing
    "here's what",
]

BANNED_DRAFT_CHARS = ['—', '–']  # em dash, en dash

# v5.89.77: regex patterns for fabricated personal experience.
# These are substantive lies (not stylistic tells) — the bot has no personal
# life, so any claim of one is invented. Compiled regexes for speed.
import re as _re_validator
FABRICATED_EXPERIENCE_PATTERNS = [
    # First-person personal experience
    _re_validator.compile(r'\bwhen i (sold|bought|had|did|went|got)\b', _re_validator.IGNORECASE),
    _re_validator.compile(r'\bi (sold|bought) (mine|my|our|a|the)\b', _re_validator.IGNORECASE),
    _re_validator.compile(r'\bhappened to (me|us|my)\b', _re_validator.IGNORECASE),
    _re_validator.compile(r'\bwe (had|did|went through|dealt with)\b', _re_validator.IGNORECASE),
    _re_validator.compile(r'\bi (had|went through|dealt with) (this|that|something|a similar)\b', _re_validator.IGNORECASE),
    _re_validator.compile(r'\bin my (own )?experience(,| with)', _re_validator.IGNORECASE),
    _re_validator.compile(r'\bmy (realtor|agent|inspector|lawyer|contractor) (said|told|did)\b', _re_validator.IGNORECASE),
    # Friend/family-of-bot anecdotes
    _re_validator.compile(r'\b(happened to|a) (friend|buddy|coworker|colleague|neighbor)( of mine)?\b', _re_validator.IGNORECASE),
    _re_validator.compile(r'\bmy (brother|sister|cousin|dad|mom|uncle|aunt|wife|husband|partner)\b', _re_validator.IGNORECASE),
    _re_validator.compile(r'\bwhen (my|our) (friend|family|brother|sister)\b', _re_validator.IGNORECASE),
]


def validate_draft_voice(draft_text):
    """v5.89.65: post-LLM safety net for AI-voice tells.
    v5.89.77: added fabricated-experience detection.

    The prompt asks the LLM to avoid these patterns, but LLMs are
    inconsistent. This second-layer check rejects drafts that slip
    through with banned phrases, em/en dashes, fake personal anecdotes,
    or excessive length.

    Returns (is_valid, reasons_list).
    """
    if not draft_text or not draft_text.strip():
        return True, []  # empty draft is fine, just no posting

    reasons = []
    lower = draft_text.lower()

    # Banned-phrase check
    for phrase in BANNED_PHRASES_IN_DRAFT:
        if phrase in lower:
            reasons.append(f"contains banned phrase: '{phrase.strip()}'")

    # v5.89.77: Fabricated personal-experience check
    for pattern in FABRICATED_EXPERIENCE_PATTERNS:
        m = pattern.search(draft_text)
        if m:
            reasons.append(f"fabricated personal experience: '{m.group(0)}'")
            break  # one match is enough to flag

    # Banned-char check (em/en dash)
    for ch in BANNED_DRAFT_CHARS:
        if ch in draft_text:
            reasons.append(f"contains banned char: '{ch}' (em/en dash)")
            break  # one mention is enough

    # Length check — count sentence terminators
    sentence_count = sum(draft_text.count(t) for t in ('.', '!', '?'))
    if sentence_count > 4:
        reasons.append(f"too long: {sentence_count} sentence terminators (max 3)")

    # Word count cap as a backstop
    word_count = len(draft_text.split())
    if word_count > 75:
        reasons.append(f"too long: {word_count} words (max ~60-70)")

    return len(reasons) == 0, reasons


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

    prompt = f"""You are drafting a Reddit comment that needs to read as written by an actual human, not an AI. Reddit users are extremely good at detecting AI-written comments and respond with hostility, sarcasm, and downvotes when they suspect one.

PLATFORM: {platform}, community: {'r/' + subreddit if platform == 'reddit' else subreddit}

THREAD TITLE: {title}
THREAD BODY: {body}

Do two things:

1. SCORE (1-10) how relevant this thread is for a useful reply about home inspections, seller disclosures, offer strategy, repair costs, or buyer/investor due diligence. Be strict:
   - 8-10: Thread is directly about one of these topics AND you have something specific to say from experience (not generic advice).
   - 5-7: Thread is tangentially related OR you only have generic advice. Lean toward NOT drafting.
   - 1-4: Thread is off-topic, career advice, investment theory, landlord issues, or anything where you cannot add specific value. DO NOT draft.
   - If the OP is asking for legal advice, financial advice, or anything where being wrong has real consequences for them: score 4 or below. Generic confident-sounding advice on these topics is what gets called out as AI.

2. DRAFT a reply (only if score >= 6) following these RULES STRICTLY. Violations will be rejected.

ABSOLUTE BANS:
- NEVER use the words: "absolutely", "honestly", "literally", "definitely", "ensure", "navigate", "leverage", "your best bet", "I'd recommend", "I'd suggest", "key thing", "main thing", "the thing is"
- NEVER use em dashes (—) or en dashes (–). Use commas, periods, or hyphens (-) only.
- NEVER use the construction "X — that Y" or "X, that Y" as a dramatic clarification.
- NEVER end with consequences ("or you'll torpedo your sale", "or you'll regret it", "you don't want to deal with that").
- NEVER end with well-wishes ("good luck", "hope this helps", "best of luck").
- NEVER mention OfferWise, our product, any tool, or any link. The comment is purely conversational.
- NEVER give confident advice on legal procedures (filing complaints, what regulators will do, what contracts mean legally). If you don't know, hedge or skip.

LENGTH:
- 1-3 sentences MAXIMUM. Most successful Reddit comments are short.
- If you can't say it in 1-3 sentences, the score should be lower and the draft should be empty.

VOICE:
- Write like someone replying on their phone during a coffee break, not like someone writing an essay.
- Start mid-thought. Lowercase first letter is fine. Sentence fragments are fine.
- Say ONE thing, not three things.
- Pick the single most useful fact or observation and just say that.
- Hedging is good ("might want to", "could", "usually", "not sure but"). Confident manual-style instructions are bad.

PERSONAL EXPERIENCE — DO NOT FABRICATE:
- NEVER claim personal experience. No "when I sold mine", "we had this", "happened to me", "I went through this", "my realtor said".
- NEVER claim experience of friends or family. No "happened to a friend", "my brother is a contractor", "we did this when we bought our place".
- These claims are fabricated by definition (the bot has no personal life) and Redditors smell them immediately.
- Instead, state the thing directly as a general pattern: "usually X happens", "in most cases Y", "the standard thing is Z", or "agents/inspectors typically say W".
- If you can't say something useful without a fabricated anecdote, the draft should be empty and the score should be lower.

EXAMPLES OF BAD AI-VOICE DRAFTS (do NOT write like this):
- "Most inspectors will absolutely walk through your attic - it's a standard part of any thorough inspection and buyers expect it. Honestly, trying to prevent it will raise way more red flags..."
- "You can absolutely relist and sell your house now that you both signed the termination agreement - that contract is dead. The earnest money distribution is a separate issue..."
- "Your best bet is to clearly mark the joists with spray paint. If you're really worried, offer to have your contractor guide them through..."

EXAMPLES OF GOOD HUMAN-VOICE DRAFTS for the same threads:
- "yeah they're gonna want to walk it. just mark the joists with spray paint or tape. trying to block them is worse than the leak you already disclosed."
- "contract's dead once you both signed termination. relist whenever. the earnest money fight is a separate thing and can take a while."
- "usually people just relist and let the earnest money sort itself out separately. takes a while but doesn't block the new sale."

Notice in the good examples: short, lowercase OK, no "absolutely" or "honestly", no closing well-wishes, no comprehensive advice, no manual-tone framing, no fabricated personal anecdotes — just one observation said plainly.

Respond in this exact JSON format only:
{{"score": 8, "reasoning": "brief reason", "draft": "Your reply text here OR empty string if score<6", "strategy": "comment_only", "tone": "experienced"}}

For tone: "empathetic", "experienced", or "skeptical".
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
                'model': 'claude-sonnet-4-6',
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

        # ── STEP 3b: Noise pre-filter (v5.89.62) ────────────────
        # Audience: buyers, investors, insurance, anyone evaluating a
        # property's condition. Drop obvious sellers/wholesalers/self-promo
        # before paying for keyword scoring or AI evaluation. Noise patterns
        # are matched against the lowercase title only — selftext is usually
        # empty at this stage anyway.
        NOISE_PATTERNS = [
            'for sale', 'off market', 'off-market', 'off the market',
            'cash buyer', 'cash buyers', 'cash offer for',
            'we buy', 'we are buying', 'we\'re buying',
            'jv partner', 'jv for', 'joint venture',
            'wholesale opportunity', 'wholesale deal available',
            'looking to jv', 'looking for buyers',
            'how i built', 'how i made', 'i built an',
            'attention cash', 'attention buyers',
            'property for sale', 'home for sale', 'house for sale',
            'investor buyer op', 'investor/buyer op',
            'lender available', 'private money available',
            'cardone', 'guru', 'mentorship program',
        ]
        kept = []
        skipped_noise = []
        for post in new_posts:
            title_lower = (post.get('title') or '').lower()
            matched_noise = next((pat for pat in NOISE_PATTERNS if pat in title_lower), None)
            if matched_noise:
                post['_noise_matched'] = matched_noise
                skipped_noise.append(post)
            else:
                kept.append(post)

        _log(f"STEP 3b — Noise filter: {len(new_posts)} → {len(kept)} kept ({len(skipped_noise)} skipped)")
        for n in skipped_noise[:5]:
            _log(f"  🚫 [\"{n['_noise_matched']}\"] \"{(n.get('title') or '')[:70]}\"")
        if len(skipped_noise) > 5:
            _log(f"  ... +{len(skipped_noise)-5} more noise")

        # Persist noise rows to DB with status='noise' so they're recorded as
        # scanned (won't refetch) but bypass keyword/AI cost. Failure here
        # is non-fatal — kept threads still proceed.
        for post in skipped_noise:
            existing_thread = GTMScannedThread.query.filter_by(reddit_id=post['reddit_id']).first()
            if existing_thread:
                existing_thread.status = 'noise'
                existing_thread.scanned_at = datetime.utcnow()
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
                        keyword_score=0,
                        status='noise',
                    )
                    db_session.add(thread)
                except Exception as ne:
                    _log(f"    ⚠️ Noise-row insert failed {post['reddit_id']}: {ne}")
                    db_session.rollback()
        try:
            db_session.commit()
        except Exception as ce:
            _log(f"    ⚠️ Noise commit failed: {ce}")
            db_session.rollback()

        new_posts = kept  # everything below operates on the kept set

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

        # ── STEP 4b: BP body hydration (v5.89.61) ────────────────
        # The BP forum listing pages only expose titles. Without bodies, the
        # AI scorer consistently rejected threads as "no substance to reply
        # to." We now make one extra HTTP request per keyword-matched BP
        # candidate to grab the OP's actual post text. Reddit candidates
        # already have selftext from the JSON API, so they're skipped here.
        bp_to_hydrate = [p for p in candidates
                         if p.get('platform') == 'biggerpockets'
                         and not p.get('selftext')]
        if bp_to_hydrate:
            _log(f"STEP 4b — Hydrating {len(bp_to_hydrate)} BP candidates with body content...")
            hydrated = 0
            for post in bp_to_hydrate:
                body = fetch_biggerpockets_body(post.get('url', ''))
                if body:
                    post['selftext'] = body
                    # Persist to the DB row so future scans reuse it
                    thread_obj = post.get('_thread')
                    if thread_obj is not None:
                        thread_obj.selftext = body
                    hydrated += 1
                    _log(f"    ✓ \"{post['title'][:50]}\" → {len(body)} chars")
                else:
                    _log(f"    ✗ \"{post['title'][:50]}\" → no body extracted")
                time.sleep(0.5)  # be polite to BP
            _log(f"STEP 4b done — {hydrated}/{len(bp_to_hydrate)} bodies fetched")
            try:
                db_session.commit()
            except Exception as commit_err:
                _log(f"  ⚠️ Body persist commit failed (non-fatal): {commit_err}")
                db_session.rollback()

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
                    # v5.89.65: validate the draft against AI-voice tells
                    # before auto-approving. If validation fails, mark as
                    # 'draft' (the default model status) so it must be
                    # manually reviewed before posting. The auto-poster
                    # only picks up 'approved' drafts, so 'draft' status
                    # blocks publication until a human edits or promotes.
                    is_valid, validation_issues = validate_draft_voice(draft_text)
                    if is_valid:
                        draft_status = 'approved'
                    else:
                        draft_status = 'draft'  # held for human review
                        _log(f"    ⚠️  Held for review (AI-voice tells): {'; '.join(validation_issues[:3])}")

                    draft = GTMRedditDraft(
                        thread_id=thread.id,
                        draft_text=draft_text,
                        strategy=strategy,
                        tone=tone,
                        mention_type='natural' if 'mention' in strategy else 'none',
                        status=draft_status,
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
