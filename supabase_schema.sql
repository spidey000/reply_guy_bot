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
--   - source_cursors: Persistent since_id cursors per source partition
--   - pipeline_events: Event funnel telemetry and audit records

-- ============================================================================
-- TWEET QUEUE TABLE
-- ============================================================================
-- Stores all generated replies awaiting approval/publication

CREATE TABLE IF NOT EXISTS tweet_queue (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,

    -- Target tweet info
    target_tweet_id TEXT NOT NULL UNIQUE,
    target_author TEXT NOT NULL,
    target_content TEXT,

    -- Generated reply
    reply_text TEXT NOT NULL,

    -- Status tracking
    -- Values: 'pending', 'approved', 'publishing', 'posted', 'rejected', 'failed'
    status TEXT DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'publishing', 'posted', 'rejected', 'failed')),

    -- Scheduling (Burst Mode)
    scheduled_at TIMESTAMP WITH TIME ZONE,

    -- Publish idempotency and in-flight tracking
    publish_request_id TEXT,
    publishing_started_at TIMESTAMP WITH TIME ZONE,
    publish_attempt_count INT DEFAULT 0,
    published_reply_tweet_id TEXT,
    last_publish_error TEXT,
    approval_message_id BIGINT,

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
CREATE INDEX IF NOT EXISTS idx_tweet_queue_approved_due
    ON tweet_queue(scheduled_at)
    WHERE status = 'approved' AND posted_at IS NULL;

-- Index for stale publish-claim recovery
CREATE INDEX IF NOT EXISTS idx_tweet_queue_publishing_stale
    ON tweet_queue(publishing_started_at)
    WHERE status = 'publishing';

-- Index for preventing duplicate replies to same tweet
CREATE INDEX IF NOT EXISTS idx_tweet_queue_target_tweet
    ON tweet_queue(target_tweet_id);

-- Atomic claim helper for approved->publishing transitions.
CREATE OR REPLACE FUNCTION claim_ready_tweets(
    p_before TIMESTAMP WITH TIME ZONE,
    p_limit INT DEFAULT 10
)
RETURNS SETOF tweet_queue
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    WITH candidates AS (
        SELECT id
        FROM tweet_queue
        WHERE status = 'approved'
          AND posted_at IS NULL
          AND scheduled_at IS NOT NULL
          AND scheduled_at <= p_before
        ORDER BY scheduled_at
        FOR UPDATE SKIP LOCKED
        LIMIT GREATEST(p_limit, 0)
    ), claimed AS (
        UPDATE tweet_queue tq
        SET status = 'publishing',
            publish_request_id = gen_random_uuid()::text,
            publishing_started_at = NOW(),
            publish_attempt_count = COALESCE(tq.publish_attempt_count, 0) + 1,
            last_publish_error = NULL
        FROM candidates c
        WHERE tq.id = c.id
        RETURNING tq.*
    )
    SELECT * FROM claimed;
END;
$$;

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
-- Tracks all authentication attempts for monitoring and cooldown logic.
--
-- Used by:
--   - src/database.py: record_login_attempt(), get_last_successful_official_login(),
--                      get_login_cooldown_remaining(), get_login_stats()
--   - src/x_delegate.py: authenticate() checks cooldown before official API login
--
-- Configuration:
--   - LOGIN_COOLDOWN_HOURS (default: 3) - Minimum hours between official API logins
--   - LOGIN_COOLDOWN_ENABLED (default: true) - Enable/disable cooldown check
--
-- Typical flow:
--   1. Authenticate with official X API credentials
--   2. Record success/failure in this table

CREATE TABLE IF NOT EXISTS login_history (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,

    -- Account identifier
    -- 'dummy' = Ghost delegate account (used for authentication)
    -- 'main' = Main account (reserved for future use)
    account_type TEXT NOT NULL CHECK (account_type IN ('dummy', 'main')),

    -- Login method
    -- 'official_x_api' = OAuth-based authentication against official API
    -- 'fresh' and 'cookie_*' are legacy values kept for backward compatibility
    login_type TEXT NOT NULL CHECK (login_type IN ('official_x_api', 'fresh', 'cookie_restore', 'cookie_bot')),

    -- Outcome
    success BOOLEAN NOT NULL,

    -- Error details (only populated on failure)
    error_message TEXT,
    error_type TEXT,

    -- When the attempt occurred (timezone-aware)
    attempted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Cookie state at time of attempt
    cookies_existed BOOLEAN DEFAULT false,
    cookies_valid BOOLEAN  -- NULL for official API logins, true/false for legacy cookie flows
);

-- Index for cooldown queries: Find last successful official API login quickly
-- This is the most common query pattern for cooldown enforcement
CREATE INDEX IF NOT EXISTS idx_login_history_official_success
    ON login_history(attempted_at DESC)
    WHERE login_type = 'official_x_api' AND success = true;

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
    next_retry_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    max_retries INT DEFAULT 5,
    last_error_type TEXT,
    request_id TEXT,
    retryable BOOLEAN DEFAULT true,

    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_retry_at TIMESTAMP WITH TIME ZONE,

    -- Status: 'pending', 'retrying', 'exhausted', 'retried_successfully'
    status TEXT DEFAULT 'pending'
        CHECK (status IN ('pending', 'retrying', 'exhausted', 'retried_successfully'))
);

-- Index for finding items ready for retry (pending with retries remaining)
CREATE INDEX IF NOT EXISTS idx_failed_tweets_pending_due
    ON failed_tweets(next_retry_at)
    WHERE status = 'pending' AND retryable = true;

-- Index for looking up DLQ items by original queue entry
CREATE INDEX IF NOT EXISTS idx_failed_tweets_queue_id
    ON failed_tweets(tweet_queue_id);

-- Optional correlation-id lookup for debugging/idempotency traces
CREATE INDEX IF NOT EXISTS idx_failed_tweets_request_id
    ON failed_tweets(request_id);

-- ============================================================================
-- SOURCE CURSORS TABLE (Persistent since_id per source)
-- ============================================================================
-- Stores durable cursors keyed by (source_type, source_identifier).
-- Enables restart-safe incremental pulls from multiple independent sources.

CREATE TABLE IF NOT EXISTS source_cursors (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_identifier TEXT NOT NULL,
    since_id TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT unique_source_cursor UNIQUE (source_type, source_identifier)
);

CREATE INDEX IF NOT EXISTS idx_source_cursors_source
    ON source_cursors(source_type, source_identifier);

-- ============================================================================
-- PIPELINE EVENTS TABLE (Funnel / Audit)
-- ============================================================================
-- Append-only event table for operational observability and funnel stats.

CREATE TABLE IF NOT EXISTS pipeline_events (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    event_name TEXT NOT NULL,
    target_tweet_id TEXT,
    tweet_queue_id UUID REFERENCES tweet_queue(id) ON DELETE SET NULL,
    source_type TEXT,
    source_identifier TEXT,
    request_id TEXT,
    telegram_user_id BIGINT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_events_created_at
    ON pipeline_events(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pipeline_events_event_name
    ON pipeline_events(event_name, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pipeline_events_request_id
    ON pipeline_events(request_id);

-- ============================================================================
-- USER SETTINGS TABLE (Settings Editor)
-- ============================================================================
-- Stores user-specific configuration overrides for the Telegram bot settings editor.
-- Allows each Telegram user to have their own preferences while maintaining
-- system defaults in the environment configuration.
--
-- Used by:
--   - src/telegram_client.py: /settings command displays and manages user settings
--   - src/database.py: get_user_settings(), update_user_settings(), reset_user_settings()
--   - config/settings.py: Settings class loads user overrides on initialization
--
-- Flow:
--   1. User opens /settings command → Show numbered menu of all available settings
--   2. User selects setting number → Display current value and prompt for new value
--   3. User enters new value → Validate and show confirmation dialog
--   4. User confirms → Update settings_json and create audit trail entry
--   5. Settings change applies to future operations (graceful transition)

CREATE TABLE IF NOT EXISTS user_settings (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,

    -- Telegram user identifier for settings association
    telegram_user_id BIGINT NOT NULL,

    -- JSON object containing all user setting overrides
    -- Example: {"burst_mode_enabled": false, "quiet_hours_start": 1}
    settings_json JSONB NOT NULL DEFAULT '{}',

    -- Version tracking for settings migration and conflict resolution
    settings_version INT DEFAULT 1,

    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Ensure each user has only one settings record
    CONSTRAINT unique_user_settings UNIQUE (telegram_user_id)
);
-- Apply trigger to user_settings
DROP TRIGGER IF EXISTS update_user_settings_updated_at ON user_settings;
CREATE TRIGGER update_user_settings_updated_at
    BEFORE UPDATE ON user_settings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();


-- Index for fast user settings lookup (most common query)
CREATE INDEX IF NOT EXISTS idx_user_settings_telegram_id
    ON user_settings(telegram_user_id);

-- ============================================================================
-- SETTINGS HISTORY TABLE (Audit Trail)
-- ============================================================================
-- Complete audit trail of all settings changes made by users.
-- Tracks who changed what, when, and provides rollback capability.
-- Essential for debugging, security analysis, and change tracking.
--
-- Used by:
--   - src/database.py: record_setting_change(), get_settings_history()
--   - src/telegram_client.py: Settings editor shows change impact and history
--   - config/settings.py: Settings class validates changes before applying
--
-- Security:
--   - All changes attributed to specific Telegram user IDs
--   - Complete before/after values stored for audit purposes
--   - Change reasons help with debugging and compliance

CREATE TABLE IF NOT EXISTS settings_history (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,

    -- User who made the change
    telegram_user_id BIGINT NOT NULL,

    -- Setting that was changed
    setting_key TEXT NOT NULL,

    -- Previous value (can be NULL for new settings)
    old_value JSONB,

    -- New value that was applied
    new_value JSONB NOT NULL,

    -- Optional reason for the change (for audit and debugging)
    change_reason TEXT,

    -- When the change was made
    changed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Verification status: 'applied', 'failed', 'rolled_back'
    verification_status TEXT DEFAULT 'applied'
        CHECK (verification_status IN ('applied', 'failed', 'rolled_back'))
);

-- Index for querying user's setting history (audit trail)
CREATE INDEX IF NOT EXISTS idx_settings_history_user_setting
    ON settings_history(telegram_user_id, setting_key, changed_at DESC);

-- Index for finding recent changes across all users (admin use)
CREATE INDEX IF NOT EXISTS idx_settings_history_recent
    ON settings_history(changed_at DESC);

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
-- SELECT * FROM login_history WHERE login_type = 'official_x_api' AND success = true ORDER BY attempted_at DESC LIMIT 1;

-- Failed tweets (DLQ) verification:
-- SELECT * FROM failed_tweets WHERE status = 'pending' LIMIT 5;
-- SELECT count(*) as pending_dlq FROM failed_tweets WHERE status = 'pending';
-- SELECT count(*) as exhausted_dlq FROM failed_tweets WHERE status = 'exhausted';
-- SELECT * FROM failed_tweets WHERE status = 'pending' AND retryable = true AND next_retry_at <= NOW() LIMIT 5;

-- Source cursor verification:
-- SELECT * FROM source_cursors ORDER BY updated_at DESC LIMIT 20;

-- Pipeline events verification:
-- SELECT event_name, count(*) FROM pipeline_events WHERE created_at >= NOW() - INTERVAL '24 hours' GROUP BY event_name ORDER BY count(*) DESC;

-- User settings verification:
-- SELECT * FROM user_settings LIMIT 5;
-- SELECT count(*) as users_with_settings FROM user_settings;
-- SELECT settings_json FROM user_settings WHERE telegram_user_id = YOUR_USER_ID;

-- Settings history verification:
-- SELECT * FROM settings_history ORDER BY changed_at DESC LIMIT 10;
-- SELECT count(*) as changes_today FROM settings_history WHERE changed_at >= CURRENT_DATE;
-- SELECT * FROM settings_history WHERE telegram_user_id = YOUR_USER_ID ORDER BY changed_at DESC LIMIT 5;
