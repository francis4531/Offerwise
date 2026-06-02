-- ============================================================================
-- Database Migration: Add Onboarding Tracking Columns
-- ============================================================================
-- 
-- This script adds onboarding_completed and onboarding_completed_at columns
-- to the users table.
-- 
-- Run this ONCE on your production database if the Python migration fails.
-- 
-- ============================================================================

-- Step 1: Add columns to users table
-- ============================================================================

-- For PostgreSQL (Render production):
ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_completed BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMP;

-- For SQLite (local development):
-- ALTER TABLE users ADD COLUMN onboarding_completed BOOLEAN DEFAULT 0;
-- ALTER TABLE users ADD COLUMN onboarding_completed_at DATETIME;


-- Step 2: Set onboarding_completed = TRUE for users who already have legal consents
-- ============================================================================
-- 
-- Logic: If a user has accepted all 3 legal consents (terms, privacy, analysis_disclaimer),
-- they've completed onboarding, so mark them as such.
-- 

UPDATE users 
SET onboarding_completed = TRUE,
    onboarding_completed_at = NOW()
WHERE id IN (
    SELECT user_id 
    FROM consent_records 
    WHERE consent_type IN ('terms', 'privacy', 'analysis_disclaimer')
    GROUP BY user_id 
    HAVING COUNT(DISTINCT consent_type) >= 3
);


-- Step 3: Verify the migration
-- ============================================================================

-- Check total users
SELECT COUNT(*) as total_users FROM users;

-- Check users with onboarding completed
SELECT COUNT(*) as completed_onboarding 
FROM users 
WHERE onboarding_completed = TRUE;

-- Check users without onboarding completed
SELECT COUNT(*) as not_completed 
FROM users 
WHERE onboarding_completed = FALSE OR onboarding_completed IS NULL;

-- Sample of users with onboarding completed
SELECT id, email, onboarding_completed, onboarding_completed_at, created_at
FROM users 
WHERE onboarding_completed = TRUE
LIMIT 5;


-- ============================================================================
-- Expected Results:
-- ============================================================================
-- 
-- - Users who have accepted all 3 legal consents should have:
--   * onboarding_completed = TRUE
--   * onboarding_completed_at = (timestamp)
-- 
-- - New users should have:
--   * onboarding_completed = FALSE (default)
--   * onboarding_completed_at = NULL
-- 
-- ============================================================================
