-- Reply Guy Bot - Supabase Database Schema
-- Run this in Supabase SQL Editor to create required tables
--
-- Usage:
--   1. Go to your Supabase project
--   2. Navigate to SQL Editor
--   3. Paste this entire file
--   4. Click "Run"
--
-- Tables:
--   - tweet_queue: Stores generated replies and their publication status
--   - target_accounts: Twitter accounts to monitor for new tweets
--   - login_history: Tracks login attempts for ban prevention (T022)
--   - failed_tweets: Dead letter queue for failed posts (T017)

-- ============================================================================
-- TWEET QUEUE TABLE
-- ============================================================================
-- Stores all generated replies awaiting approval/publication

CREATE TABLE IF NOT EXISTS tweet_queue (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,

    -- Target tweet info
    target_tweet_id TEXT NOT NULL,
    target_author TEXT NOT NULL,
    target_content TEXT,

    -- Generated reply
    reply_text TEXT NOT NULL,

    -- Status tracking
    -- Values: 'pending', 'approved', 'posted', 'rejected', 'failed'
    status TEXT DEFAULT 'pending',

    -- Scheduling (Burst Mode)
    scheduled_at TIMESTAMP WITH TIME ZONE,
    posted_at TIMESTAMP WITH TIME ZONE,

    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Error tracking
    error TEXT
);

-- Index for finding pending tweets ready to publish
CREATE INDEX IF NOT EXISTS idx_tweet_queue_status
    ON tweet_queue(status);

-- Index for scheduler queries (find approved tweets due for publication)
CREATE INDEX IF NOT EXISTS idx_tweet_queue_scheduled
    ON tweet_queue(scheduled_at)
    WHERE status = 'approved' AND posted_at IS NULL;

-- Index for preventing duplicate replies to same tweet
CREATE INDEX IF NOT EXISTS idx_tweet_queue_target_tweet
    ON tweet_queue(target_tweet_id);

-- ============================================================================
-- TARGET ACCOUNTS TABLE
-- ============================================================================
-- Twitter accounts to monitor for new tweets

CREATE TABLE IF NOT EXISTS target_accounts (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,

    -- Twitter handle (without @)
    handle TEXT NOT NULL UNIQUE,

    -- Enable/disable monitoring
    enabled BOOLEAN DEFAULT true,

    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Optional notes
    notes TEXT
);

-- Index for active monitoring queries
CREATE INDEX IF NOT EXISTS idx_target_accounts_enabled
    ON target_accounts(enabled)
    WHERE enabled = true;

-- ============================================================================
-- UPDATED_AT TRIGGER
-- ============================================================================
-- Automatically update updated_at timestamp on row changes

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply trigger to tweet_queue
DROP TRIGGER IF EXISTS update_tweet_queue_updated_at ON tweet_queue;
CREATE TRIGGER update_tweet_queue_updated_at
    BEFORE UPDATE ON tweet_queue
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Apply trigger to target_accounts
DROP TRIGGER IF EXISTS update_target_accounts_updated_at ON target_accounts;
CREATE TRIGGER update_target_accounts_updated_at
    BEFORE UPDATE ON target_accounts
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- ROW LEVEL SECURITY (RLS) - Optional
-- ============================================================================
-- Uncomment these lines if you want to enable RLS
-- This restricts access based on authenticated user

-- ALTER TABLE tweet_queue ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE target_accounts ENABLE ROW LEVEL SECURITY;

-- Allow service role full access (for bot backend)
-- CREATE POLICY "Service role has full access to tweet_queue"
--     ON tweet_queue FOR ALL
--     USING (auth.role() = 'service_role');

-- CREATE POLICY "Service role has full access to target_accounts"
--     ON target_accounts FOR ALL
--     USING (auth.role() = 'service_role');

-- ============================================================================
-- LOGIN HISTORY TABLE (Ban Prevention - T022)
-- ============================================================================
-- Tracks all login attempts to enforce cooldown between fresh logins.
-- This prevents X/Twitter from banning the dummy account due to frequent
-- re-authentication without using cookies.
--
-- Used by:
--   - src/database.py: record_login_attempt(), get_last_successful_fresh_login(),
--                      get_login_cooldown_remaining(), get_login_stats()
--   - src/x_delegate.py: login_dummy() checks cooldown before fresh login
--
-- Configuration:
--   - LOGIN_COOLDOWN_HOURS (default: 3) - Minimum hours between fresh logins
--   - LOGIN_COOLDOWN_ENABLED (default: true) - Enable/disable cooldown check
--
-- Flow:
--   1. If cookies exist and valid → Use them (no cooldown check needed)
--   2. If cookies missing/invalid → Check last fresh login timestamp
--   3. If last fresh login < LOGIN_COOLDOWN_HOURS ago → Wait until cooldown expires
--   4. After fresh login → Save cookies and record attempt to this table

CREATE TABLE IF NOT EXISTS login_history (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,

    -- Account identifier
    -- 'dummy' = Ghost delegate account (used for authentication)
    -- 'main' = Main account (reserved for future use)
    account_type TEXT NOT NULL CHECK (account_type IN ('dummy', 'main')),

    -- Login method
    -- 'fresh' = Credential-based login (username/email/password)
    -- 'cookie_restore' = Session restored from cookies.json
    login_type TEXT NOT NULL CHECK (login_type IN ('fresh', 'cookie_restore')),

    -- Outcome
    success BOOLEAN NOT NULL,

    -- Error details (only populated on failure)
    error_message TEXT,
    error_type TEXT,

    -- When the attempt occurred (timezone-aware)
    attempted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Cookie state at time of attempt
    cookies_existed BOOLEAN DEFAULT false,
    cookies_valid BOOLEAN  -- NULL for fresh logins, true/false for cookie restores
);

-- Index for cooldown queries: Find last successful fresh login quickly
-- This is the most common query pattern for cooldown enforcement
CREATE INDEX IF NOT EXISTS idx_login_history_fresh_success
    ON login_history(attempted_at DESC)
    WHERE login_type = 'fresh' AND success = true;

-- Index for failure analysis: Find recent failed logins
CREATE INDEX IF NOT EXISTS idx_login_history_recent_failures
    ON login_history(attempted_at DESC)
    WHERE success = false;

-- Index for account-specific queries
CREATE INDEX IF NOT EXISTS idx_login_history_account_type
    ON login_history(account_type, attempted_at DESC);

-- ============================================================================
-- FAILED TWEETS TABLE (Dead Letter Queue - T017)
-- ============================================================================
-- Stores tweets that failed to post for retry logic.
-- Part of the Error Recovery & Resilience system.
--
-- Used by:
--   - src/database.py: add_to_dead_letter_queue(), get_dead_letter_items(),
--                      retry_dead_letter_item(), get_dead_letter_stats()
--   - src/background_worker.py: Retries failed tweets from this queue

CREATE TABLE IF NOT EXISTS failed_tweets (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,

    -- Reference to original tweet in queue
    tweet_queue_id UUID REFERENCES tweet_queue(id),
    target_tweet_id TEXT NOT NULL,

    -- Error tracking
    error TEXT,
    retry_count INT DEFAULT 0,

    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_retry_at TIMESTAMP WITH TIME ZONE,

    -- Status: 'pending', 'retrying', 'exhausted', 'retried_successfully'
    status TEXT DEFAULT 'pending'
);

-- Index for finding items ready for retry (pending with retries remaining)
CREATE INDEX IF NOT EXISTS idx_failed_tweets_pending
    ON failed_tweets(created_at)
    WHERE status = 'pending' AND retry_count < 5;

-- ============================================================================
-- SAMPLE DATA (Optional)
-- ============================================================================
-- Uncomment to add sample target accounts

-- INSERT INTO target_accounts (handle, notes) VALUES
--     ('elonmusk', 'Test account'),
--     ('naval', 'Startup wisdom'),
--     ('paulgraham', 'YC founder');

-- ============================================================================
-- VERIFICATION QUERIES
-- ============================================================================
-- Run these to verify tables were created correctly

-- SELECT * FROM tweet_queue LIMIT 5;
-- SELECT * FROM target_accounts LIMIT 5;
-- SELECT count(*) as pending_count FROM tweet_queue WHERE status = 'pending';
-- SELECT count(*) as posted_today FROM tweet_queue WHERE posted_at >= CURRENT_DATE;

-- Login history verification:
-- SELECT * FROM login_history ORDER BY attempted_at DESC LIMIT 10;
-- SELECT * FROM login_history WHERE login_type = 'fresh' AND success = true ORDER BY attempted_at DESC LIMIT 1;

-- Failed tweets (DLQ) verification:
-- SELECT * FROM failed_tweets WHERE status = 'pending' LIMIT 5;
-- SELECT count(*) as pending_dlq FROM failed_tweets WHERE status = 'pending';
-- SELECT count(*) as exhausted_dlq FROM failed_tweets WHERE status = 'exhausted';
