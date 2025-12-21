"""
Target Account Source - Monitor specific Twitter users.

This source fetches tweets from a list of target accounts
that the user wants to monitor for reply opportunities.
"""

import logging
from typing import TYPE_CHECKING

from .base import BaseTweetSource, TweetData, SourceType

if TYPE_CHECKING:
    from twikit import Client

logger = logging.getLogger(__name__)


class TargetAccountSource(BaseTweetSource):
    """
    Fetch tweets from a specific Twitter account.
    
    This is the original monitoring behavior, now encapsulated
    as a source for the unified aggregator.
    """
    
    def __init__(self, handle: str, enabled: bool = True):
        """
        Initialize target account source.
        
        Args:
            handle: Twitter handle to monitor (without @)
            enabled: Whether this source is active
        """
        super().__init__(enabled=enabled)
        self.handle = handle.lower().lstrip("@")
    
    @property
    def source_type(self) -> SourceType:
        return SourceType.TARGET_ACCOUNT
    
    @property
    def identifier(self) -> str:
        return f"@{self.handle}"
    
    async def fetch_tweets(
        self,
        client: "Client",
        count: int = 10,
    ) -> list[TweetData]:
        """
        Fetch recent tweets from the target account.
        
        Args:
            client: Authenticated Twikit client
            count: Maximum number of tweets to fetch
            
        Returns:
            List of TweetData objects
        """
        try:
            # Get user by screen name
            user = await client.get_user_by_screen_name(self.handle)
            
            if not user:
                logger.warning(f"User @{self.handle} not found")
                return []
            
            # Fetch tweets
            tweets = await user.get_tweets("Tweets", count=count)
            
            # Convert to standardized format
            result = []
            for tweet in tweets:
                tweet_data = TweetData.from_twikit_tweet(
                    tweet=tweet,
                    source_type=self.source_type,
                    source_identifier=self.handle,
                )
                result.append(tweet_data)
            
            return result
            
        except Exception as e:
            logger.error(f"Error fetching tweets from @{self.handle}: {e}")
            return []
