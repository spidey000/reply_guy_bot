-- Migration: Add Dead Letter Queue (failed_tweets table)
-- Task: T017-S3 - Error Recovery & Resilience
-- Date: 2025-11-26
-- Description: Creates the failed_tweets table for dead letter queue functionality

-- =============================================================================
-- Create failed_tweets table
-- =============================================================================
CREATE TABLE IF NOT EXISTS failed_tweets (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    tweet_queue_id UUID REFERENCES tweet_queue(id) ON DELETE CASCADE,
    target_tweet_id TEXT NOT NULL,
    error TEXT,
    retry_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    last_retry_at TIMESTAMP,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'retrying', 'retried_successfully', 'exhausted'))
);

-- =============================================================================
-- Create indexes for performance
-- =============================================================================
CREATE INDEX IF NOT EXISTS idx_failed_tweets_status
    ON failed_tweets(status);

CREATE INDEX IF NOT EXISTS idx_failed_tweets_retry_count
    ON failed_tweets(retry_count);

CREATE INDEX IF NOT EXISTS idx_failed_tweets_created_at
    ON failed_tweets(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_failed_tweets_tweet_queue_id
    ON failed_tweets(tweet_queue_id);

-- =============================================================================
-- Add comment for documentation
-- =============================================================================
COMMENT ON TABLE failed_tweets IS
    'Dead Letter Queue - Stores failed tweet operations for retry with exponential backoff';

COMMENT ON COLUMN failed_tweets.status IS
    'Status: pending (ready for retry), retrying (currently being retried), retried_successfully (recovered), exhausted (max retries exceeded)';

COMMENT ON COLUMN failed_tweets.retry_count IS
    'Number of retry attempts (max 5 before marking as exhausted)';

-- =============================================================================
-- Verification query
-- =============================================================================
-- Run this to verify the migration succeeded:
-- SELECT COUNT(*) as failed_tweets_count FROM failed_tweets;
-- SELECT * FROM pg_indexes WHERE tablename = 'failed_tweets';
