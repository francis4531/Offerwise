/**
 * OfferWise Bot — Devvit App
 * ==========================
 * Fetches the next approved post from OfferWise's Render API
 * and publishes it to r/offerwiseAi once per day.
 *
 * Architecture:
 *   1. Scheduler runs daily at 9:00 AM PST
 *   2. Fetches next approved post from: GET https://offerwise-docker.onrender.com/api/reddit/next-post
 *   3. Submits as a text post to the subreddit
 *   4. Confirms back: POST https://offerwise-docker.onrender.com/api/reddit/post-confirm
 *
 * Settings (configured in app settings when installed):
 *   - renderApiUrl: Base URL of OfferWise (default: https://offerwise-docker.onrender.com)
 *   - apiKey: REDDIT_POST_API_KEY from Render env vars
 */

import { Devvit } from '@devvit/public-api';

// Enable HTTP fetch
Devvit.configure({ http: true });

// ── App Settings ─────────────────────────────────────────────────

Devvit.addSettings([
  {
    name: 'renderApiUrl',
    type: 'string',
    label: 'OfferWise API Base URL',
    helpText: 'e.g. https://offerwise-docker.onrender.com',
    defaultValue: 'https://offerwise-docker.onrender.com',
  },
  {
    name: 'apiKey',
    type: 'string',
    label: 'Post API Key',
    helpText: 'REDDIT_POST_API_KEY from your Render env vars',
  },
]);

// ── Scheduled Job: Daily Post ────────────────────────────────────

Devvit.addSchedulerJob({
  name: 'daily-post',
  onRun: async (_event, context) => {
    const settings = await context.settings.getAll();
    const baseUrl = (settings.renderApiUrl as string) || 'https://offerwise-docker.onrender.com';
    const apiKey = (settings.apiKey as string) || '';

    if (!apiKey) {
      console.log('OfferWise Bot: No API key configured, skipping');
      return;
    }

    // 1. Fetch next approved post from Render
    const fetchUrl = `${baseUrl}/api/reddit/next-post?key=${encodeURIComponent(apiKey)}`;
    let postData: {
      post_id: number;
      title: string;
      body: string;
      flair?: string;
    };

    try {
      const resp = await fetch(fetchUrl);
      if (resp.status === 204) {
        console.log('OfferWise Bot: No approved posts to publish today');
        return;
      }
      if (!resp.ok) {
        console.error(`OfferWise Bot: API error ${resp.status}`);
        return;
      }
      postData = await resp.json();
    } catch (err) {
      console.error('OfferWise Bot: Failed to fetch next post:', err);
      return;
    }

    // 2. Submit to the subreddit
    const subreddit = await context.reddit.getCurrentSubreddit();
    let redditUrl = '';
    try {
      const submission = await context.reddit.submitPost({
        subredditName: subreddit.name,
        title: postData.title,
        text: postData.body,
      });
      redditUrl = submission.url || `https://www.reddit.com${submission.permalink}`;
      console.log(`OfferWise Bot: Posted "${postData.title}" → ${redditUrl}`);
    } catch (err) {
      console.error('OfferWise Bot: Failed to submit post:', err);
      return;
    }

    // 3. Confirm back to Render
    try {
      const confirmUrl = `${baseUrl}/api/reddit/post-confirm?key=${encodeURIComponent(apiKey)}`;
      await fetch(confirmUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          post_id: postData.post_id,
          reddit_url: redditUrl,
        }),
      });
      console.log(`OfferWise Bot: Confirmed post #${postData.post_id} as posted`);
    } catch (err) {
      // Non-fatal — post is already live on Reddit
      console.error('OfferWise Bot: Confirm callback failed (non-fatal):', err);
    }
  },
});

// ── Menu Item: Manual Post Now ───────────────────────────────────

Devvit.addMenuItem({
  location: 'subreddit',
  label: 'OfferWise: Post Next Approved',
  description: 'Manually publish the next approved post from OfferWise',
  onPress: async (_event, context) => {
    context.ui.showToast('Fetching next approved post...');

    // Trigger the same scheduled job immediately
    await context.scheduler.runJob({ name: 'daily-post', runAt: new Date() });

    context.ui.showToast('Post submitted! Check the subreddit.');
  },
});

// ── Install Handler: Set Up Daily Schedule ───────────────────────

Devvit.addTrigger({
  event: 'AppInstall',
  onEvent: async (_event, context) => {
    // Testing: run every hour on the hour (change to '0 17 * * *' for daily 9AM PST)
    await context.scheduler.runJob({
      name: 'daily-post',
      cron: '0 * * * *', // Every hour at :00
    });
    console.log('OfferWise Bot: Hourly test schedule active (change to daily after testing)');
  },
});

export default Devvit;
