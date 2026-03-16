-- GTM Platform & Posted Tracking Migration
-- Version: 5.62.53
-- 
-- Adds platform support (Reddit + BiggerPockets) and posted URL tracking.
-- Safe to run multiple times (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).

-- Add platform column to scanned threads
ALTER TABLE gtm_scanned_threads ADD COLUMN IF NOT EXISTS platform VARCHAR(30) DEFAULT 'reddit';
CREATE INDEX IF NOT EXISTS idx_gtm_threads_platform ON gtm_scanned_threads(platform);

-- Add posted_url to drafts
ALTER TABLE gtm_reddit_drafts ADD COLUMN IF NOT EXISTS posted_url VARCHAR(500);
