-- GTM (Go-To-Market) Tables Migration
-- Version: 5.62.44
-- Date: 2025-02-26
-- 
-- Run against the Render PostgreSQL database:
--   psql $DATABASE_URL < migrations/add_gtm_tables.sql
--
-- Or these tables auto-create via SQLAlchemy db.create_all() on deploy.

-- Reddit Scout: scanned threads
CREATE TABLE IF NOT EXISTS gtm_scanned_threads (
    id SERIAL PRIMARY KEY,
    reddit_id VARCHAR(20) UNIQUE NOT NULL,
    subreddit VARCHAR(100) NOT NULL,
    title TEXT NOT NULL,
    selftext TEXT,
    author VARCHAR(100),
    reddit_score INTEGER DEFAULT 0,
    num_comments INTEGER DEFAULT 0,
    url VARCHAR(500),
    created_utc TIMESTAMP,
    keyword_score INTEGER DEFAULT 0,
    ai_score INTEGER DEFAULT 0,
    ai_reasoning TEXT,
    ai_topics TEXT,
    status VARCHAR(30) DEFAULT 'scanned',
    scanned_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gtm_threads_reddit_id ON gtm_scanned_threads(reddit_id);
CREATE INDEX IF NOT EXISTS idx_gtm_threads_status ON gtm_scanned_threads(status);
CREATE INDEX IF NOT EXISTS idx_gtm_threads_subreddit ON gtm_scanned_threads(subreddit);
CREATE INDEX IF NOT EXISTS idx_gtm_threads_scanned ON gtm_scanned_threads(scanned_at);

-- Reddit Scout: reply drafts
CREATE TABLE IF NOT EXISTS gtm_reddit_drafts (
    id SERIAL PRIMARY KEY,
    thread_id INTEGER NOT NULL REFERENCES gtm_scanned_threads(id) ON DELETE CASCADE,
    draft_text TEXT NOT NULL,
    strategy VARCHAR(50),
    tone VARCHAR(50),
    mention_type VARCHAR(30),
    edited_text TEXT,
    status VARCHAR(20) DEFAULT 'pending',
    skip_reason VARCHAR(200),
    reviewed_at TIMESTAMP,
    posted_at TIMESTAMP,
    reddit_comment_id VARCHAR(20),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gtm_drafts_status ON gtm_reddit_drafts(status);
CREATE INDEX IF NOT EXISTS idx_gtm_drafts_thread ON gtm_reddit_drafts(thread_id);
CREATE INDEX IF NOT EXISTS idx_gtm_drafts_created ON gtm_reddit_drafts(created_at);

-- Reddit Scout: scan runs log
CREATE TABLE IF NOT EXISTS gtm_scan_runs (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status VARCHAR(20) DEFAULT 'running',
    posts_scanned INTEGER DEFAULT 0,
    posts_filtered INTEGER DEFAULT 0,
    posts_scored INTEGER DEFAULT 0,
    drafts_created INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    error_detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_gtm_runs_started ON gtm_scan_runs(started_at);

-- Conversion Intel: funnel events
CREATE TABLE IF NOT EXISTS gtm_funnel_events (
    id SERIAL PRIMARY KEY,
    stage VARCHAR(30) NOT NULL,
    source VARCHAR(100) DEFAULT 'direct',
    medium VARCHAR(100) DEFAULT 'none',
    user_id INTEGER REFERENCES users(id),
    session_id VARCHAR(100),
    metadata_json TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gtm_funnel_stage ON gtm_funnel_events(stage);
CREATE INDEX IF NOT EXISTS idx_gtm_funnel_source ON gtm_funnel_events(source);
CREATE INDEX IF NOT EXISTS idx_gtm_funnel_created ON gtm_funnel_events(created_at);

-- Conversion Intel: daily ad performance
CREATE TABLE IF NOT EXISTS gtm_ad_performance (
    id SERIAL PRIMARY KEY,
    channel VARCHAR(50) NOT NULL,
    date DATE NOT NULL,
    impressions INTEGER DEFAULT 0,
    clicks INTEGER DEFAULT 0,
    spend NUMERIC(10, 2) DEFAULT 0,
    conversions INTEGER DEFAULT 0,
    revenue NUMERIC(10, 2) DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP,
    UNIQUE(channel, date)
);

CREATE INDEX IF NOT EXISTS idx_gtm_adperf_channel ON gtm_ad_performance(channel);
CREATE INDEX IF NOT EXISTS idx_gtm_adperf_date ON gtm_ad_performance(date);

-- GTM: target subreddits (admin-managed)
CREATE TABLE IF NOT EXISTS gtm_target_subreddits (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,
    priority INTEGER DEFAULT 5,
    notes VARCHAR(300),
    added_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gtm_subs_enabled ON gtm_target_subreddits(enabled);

-- Seed default subreddits
INSERT INTO gtm_target_subreddits (name, priority, notes) VALUES
    ('FirstTimeHomeBuyer', 1, 'Highest intent — our core audience'),
    ('HomeInspections', 1, 'Direct match — inspection discussion'),
    ('RealEstateAdvice', 2, 'Advice seekers, high intent'),
    ('homebuying', 2, 'Active buying process discussions'),
    ('RealEstate', 3, 'High volume, general real estate'),
    ('RealEstateAgent', 4, 'Agent perspective, indirect value'),
    ('bayarea', 5, 'Local — Bay Area housing discussions'),
    ('SanJose', 5, 'Local — San Jose housing threads')
ON CONFLICT (name) DO NOTHING;
