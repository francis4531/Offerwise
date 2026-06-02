-- GTM Multi-Channel Posts Migration
-- Version: 5.76.76
--
-- Adds platform and target_group columns to gtm_subreddit_posts
-- to support Facebook and Nextdoor post generation alongside Reddit.
-- Safe to run multiple times (ADD COLUMN IF NOT EXISTS).

-- Add platform column (reddit | facebook | nextdoor)
ALTER TABLE gtm_subreddit_posts ADD COLUMN IF NOT EXISTS platform VARCHAR(20) DEFAULT 'reddit';
CREATE INDEX IF NOT EXISTS idx_gtm_posts_platform ON gtm_subreddit_posts(platform);

-- Add target_group column (FB group name / Nextdoor neighborhood)
ALTER TABLE gtm_subreddit_posts ADD COLUMN IF NOT EXISTS target_group VARCHAR(200);

-- Back-fill existing rows as reddit posts
UPDATE gtm_subreddit_posts SET platform = 'reddit' WHERE platform IS NULL;
