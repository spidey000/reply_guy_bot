"""
Tweet Sources Module - Multi-source tweet discovery.

This module provides a unified interface for discovering tweets from:
- Target accounts (specific users to monitor)
- Search queries (keyword-based discovery)
- Home feed (timeline tweets)

All sources implement BaseTweetSource and return standardized Tweet objects.
"""

from .base import BaseTweetSource, TweetData
from .target_account import TargetAccountSource
from .search_query import SearchQuerySource
from .home_feed import HomeFeedSource
from .aggregator import TweetAggregator

__all__ = [
    "BaseTweetSource",
    "TweetData",
    "TargetAccountSource",
    "SearchQuerySource",
    "HomeFeedSource",
    "TweetAggregator",
]
