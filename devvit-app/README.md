# OfferWise Bot — Devvit App

Auto-posts daily educational content to r/offerwiseAi from the OfferWise content queue.

## How It Works

```
OfferWise (Render)                    Devvit (Reddit)
┌──────────────────┐                  ┌──────────────────┐
│ Content Engine    │                  │ Scheduler        │
│ generates posts   │                  │ runs daily 9AM   │
│       ↓          │   GET /next-post │       ↓          │
│ Admin reviews &  │ ◄────────────────│ Fetch next post  │
│ approves         │ ────────────────►│       ↓          │
│       ↓          │   JSON response  │ Submit to Reddit │
│ Queue: approved  │                  │       ↓          │
│       ↓          │  POST /confirm   │ Confirm posted   │
│ Mark as posted   │ ◄────────────────│                  │
└──────────────────┘                  └──────────────────┘
```

## Setup (one-time)

### 1. Generate an API key on Render

Add this env var on Render:
```
REDDIT_POST_API_KEY=<generate a random string, e.g. openssl rand -hex 32>
```

### 2. Install Devvit CLI

```bash
npm install -g devvit
devvit login        # Opens browser to authorize with Reddit
```

### 3. Upload the app

```bash
cd offerwise-devvit
npm install
devvit upload       # Uploads to Reddit's app directory (private)
```

### 4. Install on r/offerwiseAi

```bash
devvit install offerwiseAi
```

### 5. Configure settings

Go to r/offerwiseAi → Mod Tools → Installed Apps → offerwise-bot → Settings:
- **OfferWise API Base URL**: `https://getofferwise.ai`
- **Post API Key**: Same value as REDDIT_POST_API_KEY from Render

### 6. Done!

The bot will:
- Automatically post at **9:00 AM PST** daily
- Fetch the next "approved" post from your GTM Command Center
- Submit it to r/offerwiseAi
- Mark it as "posted" in your dashboard

### Manual posting

As a moderator, you can also click the three-dot menu (⋯) on the subreddit
and select **"OfferWise: Post Next Approved"** to publish immediately.

## Workflow (day-to-day)

1. Go to **GTM Command Center → Subreddit Content** tab
2. Generate posts (or they auto-generate on schedule)
3. Review and click **Approve** on posts you want published
4. The Devvit bot picks them up at 9 AM and posts them
5. Check the dashboard — posted items show the Reddit URL

## Files

- `src/main.ts` — The entire Devvit app (scheduler, fetch, submit, confirm)
- `devvit.yaml` — App config (name, version)
- `package.json` — Dependencies
