"""
Tweet Aggregator - Combines multiple sources with deduplication.

This module orchestrates fetching from all enabled sources,
deduplicates results, and prepares tweets for topic filtering.
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from .base import BaseTweetSource, TweetData

if TYPE_CHECKING:
    from twikit import Client

logger = logging.getLogger(__name__)


class TweetAggregator:
    """
    Aggregates tweets from multiple sources.
    
    Handles:
    - Fetching from all enabled sources
    - Deduplication across sources
    - Tracking seen tweets to avoid reprocessing
    """
    
    def __init__(self):
        """Initialize the aggregator."""
        self._sources: list[BaseTweetSource] = []
        self._seen_tweet_ids: set[str] = set()
        self._last_fetch: Optional[datetime] = None
    
    def add_source(self, source: BaseTweetSource) -> None:
        """
        Add a source to the aggregator.
        
        Args:
            source: Tweet source to add
        """
        self._sources.append(source)
        logger.info(f"Added source: {source.source_type.value}:{source.identifier}")
    
    def remove_source(self, identifier: str) -> bool:
        """
        Remove a source by identifier.
        
        Args:
            identifier: Source identifier to remove
            
        Returns:
            True if source was found and removed
        """
        for i, source in enumerate(self._sources):
            if source.identifier == identifier:
                self._sources.pop(i)
                logger.info(f"Removed source: {identifier}")
                return True
        return False
    
    def get_sources(self) -> list[BaseTweetSource]:
        """Get all registered sources."""
        return self._sources.copy()
    
    def get_enabled_sources(self) -> list[BaseTweetSource]:
        """Get only enabled sources."""
        return [s for s in self._sources if s.enabled]
    
    def mark_seen(self, tweet_id: str) -> None:
        """
        Mark a tweet as seen to avoid reprocessing.
        
        Args:
            tweet_id: Tweet ID to mark as seen
        """
        self._seen_tweet_ids.add(tweet_id)
    
    def mark_seen_batch(self, tweet_ids: list[str]) -> None:
        """
        Mark multiple tweets as seen.
        
        Args:
            tweet_ids: List of tweet IDs to mark as seen
        """
        self._seen_tweet_ids.update(tweet_ids)
    
    def is_seen(self, tweet_id: str) -> bool:
        """Check if a tweet has been seen."""
        return tweet_id in self._seen_tweet_ids
    
    def clear_seen(self) -> None:
        """Clear the seen tweets set (use with caution)."""
        self._seen_tweet_ids.clear()
        logger.info("Cleared seen tweets cache")
    
    async def fetch_all(
        self,
        client: "Client",
        count_per_source: int = 10,
    ) -> list[TweetData]:
        """
        Fetch tweets from all enabled sources.
        
        Args:
            client: Authenticated Twikit client
            count_per_source: Maximum tweets to fetch per source
            
        Returns:
            Deduplicated list of TweetData objects
        """
        all_tweets: list[TweetData] = []
        source_stats: dict[str, int] = {}
        
        enabled_sources = self.get_enabled_sources()
        
        if not enabled_sources:
            logger.warning("No enabled sources configured")
            return []
        
        logger.info(f"Fetching from {len(enabled_sources)} sources...")
        
        for source in enabled_sources:
            try:
                tweets = await source.get_tweets(client, count=count_per_source)
                source_stats[source.identifier] = len(tweets)
                all_tweets.extend(tweets)
            except Exception as e:
                logger.error(f"Error fetching from {source.identifier}: {e}")
                source_stats[source.identifier] = 0
        
        # Deduplicate by tweet ID
        unique_tweets = self._deduplicate(all_tweets)
        
        # Filter out already-seen tweets
        new_tweets = [t for t in unique_tweets if not self.is_seen(t.id)]
        
        # Update metadata
        self._last_fetch = datetime.utcnow()
        
        # Log summary
        logger.info(
            f"Aggregated {len(all_tweets)} total, "
            f"{len(unique_tweets)} unique, "
            f"{len(new_tweets)} new tweets"
        )
        for source_id, count in source_stats.items():
            logger.debug(f"  {source_id}: {count} tweets")
        
        return new_tweets
    
    def _deduplicate(self, tweets: list[TweetData]) -> list[TweetData]:
        """
        Remove duplicate tweets, keeping first occurrence.
        
        Args:
            tweets: List of tweets to deduplicate
            
        Returns:
            Deduplicated list
        """
        seen_ids: set[str] = set()
        unique: list[TweetData] = []
        
        for tweet in tweets:
            if tweet.id not in seen_ids:
                seen_ids.add(tweet.id)
                unique.append(tweet)
        
        return unique
    
    def get_status(self) -> dict:
        """Get aggregator status information."""
        return {
            "total_sources": len(self._sources),
            "enabled_sources": len(self.get_enabled_sources()),
            "seen_tweet_count": len(self._seen_tweet_ids),
            "last_fetch": self._last_fetch.isoformat() if self._last_fetch else None,
            "sources": [s.get_status() for s in self._sources],
        }
