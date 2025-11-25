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
