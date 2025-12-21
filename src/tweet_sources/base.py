"""
Base Tweet Source - Abstract interface for tweet discovery.

All tweet sources must implement this interface to provide a
consistent API for the aggregator.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from twikit import Client

logger = logging.getLogger(__name__)


class SourceType(Enum):
    """Types of tweet sources."""
    TARGET_ACCOUNT = "target_account"
    SEARCH_QUERY = "search_query"
    HOME_FEED_FOR_YOU = "home_feed_for_you"
    HOME_FEED_FOLLOWING = "home_feed_following"


@dataclass
class TweetData:
    """
    Standardized tweet data structure.
    
    This provides a common format regardless of source,
    making filtering and processing consistent.
    """
    id: str
    text: str
    author_handle: str
    author_id: str
    created_at: Optional[datetime] = None
    
    # Source metadata
    source_type: SourceType = SourceType.TARGET_ACCOUNT
    source_identifier: str = ""  # e.g., target handle, search query
    
    # Engagement metrics (for prioritization)
    like_count: int = 0
    retweet_count: int = 0
    reply_count: int = 0
    view_count: int = 0
    
    # Flags
    is_retweet: bool = False
    is_reply: bool = False
    is_quote: bool = False
    
    # Raw tweet object for later use
    raw_tweet: Optional[object] = field(default=None, repr=False)
    
    @classmethod
    def from_twikit_tweet(
        cls,
        tweet,
        source_type: SourceType,
        source_identifier: str,
    ) -> "TweetData":
        """
        Create TweetData from a Twikit Tweet object.
        
        Args:
            tweet: Twikit Tweet object
            source_type: Type of source this tweet came from
            source_identifier: Identifier for the source (handle, query, etc.)
            
        Returns:
            Standardized TweetData object
        """
        return cls(
            id=tweet.id,
            text=tweet.text or "",
            author_handle=tweet.user.screen_name if tweet.user else "unknown",
            author_id=tweet.user.id if tweet.user else "",
            created_at=tweet.created_at_datetime if hasattr(tweet, 'created_at_datetime') else None,
            source_type=source_type,
            source_identifier=source_identifier,
            like_count=tweet.favorite_count or 0,
            retweet_count=tweet.retweet_count or 0,
            reply_count=tweet.reply_count or 0 if hasattr(tweet, 'reply_count') else 0,
            view_count=tweet.view_count or 0 if hasattr(tweet, 'view_count') else 0,
            is_retweet=bool(getattr(tweet, 'retweeted_tweet', None)),
            is_reply=bool(getattr(tweet, 'in_reply_to', None)),
            is_quote=bool(getattr(tweet, 'quoted_tweet', None)),
            raw_tweet=tweet,
        )


class BaseTweetSource(ABC):
    """
    Abstract base class for all tweet sources.
    
    Implementations must provide:
    - fetch_tweets(): Retrieve tweets from the source
    - source_type: Type identifier for this source
    """
    
    def __init__(self, enabled: bool = True):
        """
        Initialize the source.
        
        Args:
            enabled: Whether this source is active
        """
        self.enabled = enabled
        self._last_fetch: Optional[datetime] = None
        self._fetch_count = 0
    
    @property
    @abstractmethod
    def source_type(self) -> SourceType:
        """Return the type of this source."""
        pass
    
    @property
    @abstractmethod
    def identifier(self) -> str:
        """Return a unique identifier for this source instance."""
        pass
    
    @abstractmethod
    async def fetch_tweets(
        self,
        client: "Client",
        count: int = 10,
    ) -> list[TweetData]:
        """
        Fetch tweets from this source.
        
        Args:
            client: Authenticated Twikit client
            count: Maximum number of tweets to fetch
            
        Returns:
            List of standardized TweetData objects
        """
        pass
    
    def filter_tweet(self, tweet: TweetData) -> bool:
        """
        Apply source-specific filtering.
        
        Override this method to add custom filtering logic.
        Default implementation skips retweets and replies.
        
        Args:
            tweet: Tweet to evaluate
            
        Returns:
            True if tweet should be included, False to skip
        """
        # Skip retweets
        if tweet.is_retweet:
            return False
        
        # Skip replies (we want original content)
        if tweet.is_reply:
            return False
        
        return True
    
    async def get_tweets(
        self,
        client: "Client",
        count: int = 10,
    ) -> list[TweetData]:
        """
        Fetch and filter tweets from this source.
        
        This is the main entry point that handles:
        1. Fetching raw tweets
        2. Applying source-specific filters
        3. Updating fetch metadata
        
        Args:
            client: Authenticated Twikit client
            count: Maximum number of tweets to fetch
            
        Returns:
            List of filtered TweetData objects
        """
        if not self.enabled:
            logger.debug(f"Source {self.identifier} is disabled, skipping")
            return []
        
        try:
            # Fetch tweets
            tweets = await self.fetch_tweets(client, count)
            
            # Apply filtering
            filtered = [t for t in tweets if self.filter_tweet(t)]
            
            # Update metadata
            self._last_fetch = datetime.utcnow()
            self._fetch_count += 1
            
            logger.info(
                f"Source {self.source_type.value}:{self.identifier} "
                f"fetched {len(tweets)} tweets, {len(filtered)} after filtering"
            )
            
            return filtered
            
        except Exception as e:
            logger.error(f"Error fetching from {self.identifier}: {e}")
            return []
    
    def get_status(self) -> dict:
        """Get status information about this source."""
        return {
            "type": self.source_type.value,
            "identifier": self.identifier,
            "enabled": self.enabled,
            "last_fetch": self._last_fetch.isoformat() if self._last_fetch else None,
            "fetch_count": self._fetch_count,
        }
