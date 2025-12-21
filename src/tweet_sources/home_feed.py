"""
Home Feed Source - Discover tweets from timeline.

This source fetches tweets from the authenticated user's
home timeline, providing access to followed accounts' content.
"""

import logging
from typing import TYPE_CHECKING, Literal

from .base import BaseTweetSource, TweetData, SourceType

if TYPE_CHECKING:
    from twikit import Client

logger = logging.getLogger(__name__)


class HomeFeedSource(BaseTweetSource):
    """
    Fetch tweets from the home timeline.
    
    Supports two feed types:
    - "for_you": Algorithmic feed (Twitter's recommendations)
    - "following": Chronological feed (accounts you follow)
    """
    
    def __init__(
        self,
        feed_type: Literal["for_you", "following"] = "following",
        enabled: bool = True,
    ):
        """
        Initialize home feed source.
        
        Args:
            feed_type: "for_you" or "following"
            enabled: Whether this source is active
        """
        super().__init__(enabled=enabled)
        self.feed_type = feed_type
    
    @property
    def source_type(self) -> SourceType:
        if self.feed_type == "for_you":
            return SourceType.HOME_FEED_FOR_YOU
        return SourceType.HOME_FEED_FOLLOWING
    
    @property
    def identifier(self) -> str:
        return f"home:{self.feed_type}"
    
    async def fetch_tweets(
        self,
        client: "Client",
        count: int = 20,
    ) -> list[TweetData]:
        """
        Fetch tweets from the home timeline.
        
        Args:
            client: Authenticated Twikit client
            count: Maximum number of tweets to fetch
            
        Returns:
            List of TweetData objects
        """
        try:
            # Choose the appropriate timeline method
            if self.feed_type == "for_you":
                tweets = await client.get_timeline(count=count)
            else:
                tweets = await client.get_latest_timeline(count=count)
            
            # Convert to standardized format
            result = []
            for tweet in tweets:
                tweet_data = TweetData.from_twikit_tweet(
                    tweet=tweet,
                    source_type=self.source_type,
                    source_identifier=self.feed_type,
                )
                result.append(tweet_data)
            
            return result
            
        except Exception as e:
            logger.error(f"Error fetching {self.feed_type} timeline: {e}")
            return []
    
    def filter_tweet(self, tweet: TweetData) -> bool:
        """
        Apply home feed-specific filtering.
        
        For home feed, we might want to be more selective
        to avoid noise from the algorithmic feed.
        """
        # Apply base filtering first
        if not super().filter_tweet(tweet):
            return False
        
        # Skip quote tweets from home feed (often less actionable)
        if tweet.is_quote:
            return False
        
        return True
