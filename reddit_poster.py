"""
Reddit Auto-Poster — Publishes approved posts to r/offerwiseAi
================================================================
Supports two modes:
  1. Data API mode: Background job on Render posts directly via Reddit API
  2. Devvit mode: Devvit app fetches next post from /api/reddit/next-post,
     publishes it, then confirms via /api/reddit/post-confirm

Both modes use the same approval queue from GTMSubredditPost (status='approved').

Required env vars for Data API mode:
  REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD

For Devvit mode, only REDDIT_POST_API_KEY is needed (secures the fetch endpoint).
"""

import logging
import os
from datetime import datetime, date

import requests

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────

REDDIT_CLIENT_ID = os.environ.get('REDDIT_CLIENT_ID', '')
REDDIT_CLIENT_SECRET = os.environ.get('REDDIT_CLIENT_SECRET', '')
REDDIT_USERNAME = os.environ.get('REDDIT_USERNAME', '')
REDDIT_PASSWORD = os.environ.get('REDDIT_PASSWORD', '')
TARGET_SUBREDDIT = os.environ.get('REDDIT_TARGET_SUBREDDIT', 'offerwiseAi')
USER_AGENT = f'web:OfferWise-ContentBot:v1.0 (by /u/{REDDIT_USERNAME or "offerwiseAI"})'


def is_configured():
    """Check if Reddit Data API credentials are set."""
    return all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD])


# ── Reddit Auth ──────────────────────────────────────────────────

def _get_access_token():
    """Get an OAuth2 access token using the password grant flow."""
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
    if 'access_token' not in data:
        raise RuntimeError(f"Reddit auth failed: {data}")
    return data['access_token']


# ── Post to Reddit ───────────────────────────────────────────────

def submit_post(title, body, flair=None):
    """Submit a text post to the target subreddit.

    Returns dict with 'url' and 'id' on success, or raises on failure.
    """
    token = _get_access_token()
    headers = {
        'Authorization': f'bearer {token}',
        'User-Agent': USER_AGENT,
    }

    data = {
        'sr': TARGET_SUBREDDIT,
        'kind': 'self',
        'title': title,
        'text': body,
        'resubmit': True,
    }
    if flair:
        data['flair_text'] = flair

    resp = requests.post(
        'https://oauth.reddit.com/api/submit',
        headers=headers,
        data=data,
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()

    # Reddit returns nested JSON; extract the URL
    if result.get('success') is False:
        errors = result.get('jquery', [])
        raise RuntimeError(f"Reddit submit failed: {errors}")

    # Try to extract post URL from response
    post_url = ''
    post_id = ''
    json_data = result.get('json', {}).get('data', {})
    if json_data:
        post_url = json_data.get('url', '')
        post_id = json_data.get('id', '') or json_data.get('name', '')

    logger.info(f"Reddit post submitted: {post_url}")
    return {'url': post_url, 'id': post_id}


# ── Fetch & Post Next Approved ───────────────────────────────────

def get_next_approved_post(db_session):
    """Get the next approved post that hasn't been posted yet.

    Prioritizes today's post, then past approved posts.
    Returns the GTMSubredditPost object or None.
    """
    from models import GTMSubredditPost

    today = date.today()

    # First: today's approved post
    post = GTMSubredditPost.query.filter_by(
        status='approved', scheduled_date=today
    ).first()

    if not post:
        # Fallback: any past approved post not yet posted
        post = GTMSubredditPost.query.filter(
            GTMSubredditPost.status == 'approved',
            GTMSubredditPost.scheduled_date <= today,
        ).order_by(GTMSubredditPost.scheduled_date.desc()).first()

    return post


def post_next_approved(db_session):
    """Find the next approved post and submit it to Reddit.

    Returns dict with result info, or None if nothing to post.
    Requires Data API credentials to be configured.
    """
    if not is_configured():
        logger.debug("Reddit poster: not configured, skipping")
        return None

    post = get_next_approved_post(db_session)
    if not post:
        logger.debug("Reddit poster: no approved posts to publish")
        return None

    try:
        body = post.edited_body or post.body
        result = submit_post(post.title, body, post.flair)

        post.status = 'posted'
        post.posted_at = datetime.utcnow()
        post.posted_url = result.get('url', '')
        db_session.commit()

        logger.info(f"Reddit auto-posted: {post.title} → {post.posted_url}")
        return {
            'post_id': post.id,
            'title': post.title,
            'reddit_url': post.posted_url,
        }

    except Exception as e:
        logger.error(f"Reddit auto-post failed: {e}")
        db_session.rollback()
        return {'error': str(e)}


# ── Confirm Callback (for Devvit mode) ───────────────────────────

def confirm_posted(db_session, post_id, reddit_url):
    """Mark a post as posted after external publishing (Devvit or manual).

    Returns True on success.
    """
    from models import GTMSubredditPost

    post = GTMSubredditPost.query.get(post_id)
    if not post:
        return False

    post.status = 'posted'
    post.posted_at = datetime.utcnow()
    post.posted_url = reddit_url
    db_session.commit()
    logger.info(f"Reddit post confirmed: #{post_id} → {reddit_url}")
    return True
