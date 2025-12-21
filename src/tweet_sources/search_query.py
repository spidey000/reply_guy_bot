"""
Search Query Source - Discover tweets by keyword search.

This source uses Twitter's search API to find tweets
matching specified keywords or phrases.
"""

import logging
from typing import TYPE_CHECKING, Literal

from .base import BaseTweetSource, TweetData, SourceType

if TYPE_CHECKING:
    from twikit import Client

logger = logging.getLogger(__name__)


class SearchQuerySource(BaseTweetSource):
    """
    Fetch tweets matching a search query.
    
    Uses client.search_tweet() to find relevant content
    from across Twitter, not limited to followed accounts.
    """
    
    def __init__(
        self,
        query: str,
        product: Literal["Top", "Latest", "Media"] = "Latest",
        enabled: bool = True,
    ):
        """
        Initialize search query source.
        
        Args:
            query: Search query string (e.g., "AI startup", "#crypto")
            product: Type of search results ("Top", "Latest", "Media")
            enabled: Whether this source is active
        """
        super().__init__(enabled=enabled)
        self.query = query
        self.product = product
    
    @property
    def source_type(self) -> SourceType:
        return SourceType.SEARCH_QUERY
    
    @property
    def identifier(self) -> str:
        return f"search:{self.query}"
    
    async def fetch_tweets(
        self,
        client: "Client",
        count: int = 20,
    ) -> list[TweetData]:
        """
        Search for tweets matching the query.
        
        Args:
            client: Authenticated Twikit client
            count: Maximum number of tweets to fetch (1-20)
            
        Returns:
            List of TweetData objects
        """
        try:
            # Ensure count is within API limits
            count = min(max(1, count), 20)
            
            # Perform search
            tweets = await client.search_tweet(
                query=self.query,
                product=self.product,
                count=count,
            )
            
            # Convert to standardized format
            result = []
            for tweet in tweets:
                tweet_data = TweetData.from_twikit_tweet(
                    tweet=tweet,
                    source_type=self.source_type,
                    source_identifier=self.query,
                )
                result.append(tweet_data)
            
            return result
            
        except Exception as e:
            logger.error(f"Error searching for '{self.query}': {e}")
            return []
    
    def filter_tweet(self, tweet: TweetData) -> bool:
        """
        Apply search-specific filtering.
        
        In addition to base filtering, we may want to
        exclude certain patterns from search results.
        """
        # Apply base filtering first
        if not super().filter_tweet(tweet):
            return False
        
        # Additional search-specific filters can be added here
        # For example, filtering out promotional content, etc.
        
        return True
