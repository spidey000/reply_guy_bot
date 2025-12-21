"""
Topic Filter - AI-powered and keyword-based relevance scoring.

This module filters tweets based on configured topics/keywords,
optionally using AI for semantic relevance scoring.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

from src.tweet_sources.base import TweetData

logger = logging.getLogger(__name__)


@dataclass
class TopicScore:
    """Result of topic relevance scoring."""
    tweet_id: str
    score: float  # 0.0 to 1.0
    matched_topics: list[str]
    method: str  # "keyword" or "ai"


class TopicFilter:
    """
    Filters tweets based on topic relevance.
    
    Supports two modes:
    - Keyword matching: Fast, rule-based filtering
    - AI scoring: Semantic relevance (requires AI client)
    
    If no topics are configured, all tweets pass through.
    """
    
    def __init__(
        self,
        topics: Optional[list[str]] = None,
        min_score: float = 0.5,
        use_ai: bool = False,
        ai_client: Optional[object] = None,
    ):
        """
        Initialize the topic filter.
        
        Args:
            topics: List of topics/keywords to filter for
            min_score: Minimum relevance score to pass (0.0-1.0)
            use_ai: Whether to use AI for semantic scoring
            ai_client: AIClient instance (required if use_ai=True)
        """
        self.topics = [t.lower().strip() for t in (topics or [])]
        self.min_score = min_score
        self.use_ai = use_ai
        self.ai_client = ai_client
        
        # Compile regex patterns for efficient matching
        self._patterns = [
            re.compile(r'\b' + re.escape(topic) + r'\b', re.IGNORECASE)
            for topic in self.topics
        ]
        
        logger.info(
            f"TopicFilter initialized with {len(self.topics)} topics, "
            f"min_score={min_score}, use_ai={use_ai}"
        )
    
    def add_topic(self, topic: str) -> None:
        """Add a topic to the filter."""
        topic = topic.lower().strip()
        if topic and topic not in self.topics:
            self.topics.append(topic)
            self._patterns.append(
                re.compile(r'\b' + re.escape(topic) + r'\b', re.IGNORECASE)
            )
            logger.info(f"Added topic: {topic}")
    
    def remove_topic(self, topic: str) -> bool:
        """Remove a topic from the filter."""
        topic = topic.lower().strip()
        if topic in self.topics:
            idx = self.topics.index(topic)
            self.topics.pop(idx)
            self._patterns.pop(idx)
            logger.info(f"Removed topic: {topic}")
            return True
        return False
    
    def get_topics(self) -> list[str]:
        """Get current list of topics."""
        return self.topics.copy()
    
    def _keyword_score(self, tweet: TweetData) -> TopicScore:
        """
        Score a tweet using keyword matching.
        
        Score is based on:
        - Number of matching topics / total topics
        - Bonus for multiple matches
        """
        if not self.topics:
            # No topics = everything passes with max score
            return TopicScore(
                tweet_id=tweet.id,
                score=1.0,
                matched_topics=[],
                method="keyword",
            )
        
        text = tweet.text.lower()
        matched = []
        
        for topic, pattern in zip(self.topics, self._patterns):
            if pattern.search(tweet.text):
                matched.append(topic)
        
        # Calculate score
        if not matched:
            score = 0.0
        else:
            # Base score from match ratio
            base_score = len(matched) / len(self.topics)
            # Bonus for having at least one match (makes it more lenient)
            score = min(1.0, base_score + 0.5 if matched else 0)
        
        return TopicScore(
            tweet_id=tweet.id,
            score=score,
            matched_topics=matched,
            method="keyword",
        )
    
    async def _ai_score(self, tweet: TweetData) -> TopicScore:
        """
        Score a tweet using AI semantic analysis.
        
        This provides better matching for:
        - Synonyms and related concepts
        - Context-aware relevance
        - Nuanced topic detection
        """
        if not self.ai_client:
            logger.warning("AI scoring requested but no AI client configured")
            return self._keyword_score(tweet)
        
        if not self.topics:
            return TopicScore(
                tweet_id=tweet.id,
                score=1.0,
                matched_topics=[],
                method="ai",
            )
        
        try:
            # Build prompt for relevance scoring
            topics_str = ", ".join(self.topics)
            prompt = f"""Rate the relevance of this tweet to the following topics on a scale of 0 to 10.

Topics: {topics_str}

Tweet by @{tweet.author_handle}:
"{tweet.text}"

Respond with ONLY a number from 0-10, where:
0 = completely unrelated
5 = somewhat related
10 = highly relevant

Score:"""
            
            # Call AI for scoring
            # Note: This is a simplified implementation
            # In production, you might want a dedicated scoring endpoint
            response = await self.ai_client.generate_reply(
                tweet_author="system",
                tweet_content=prompt,
                max_tokens=10,
                temperature=0.1,
            )
            
            # Parse score from response
            if response:
                try:
                    score_raw = float(response.strip().split()[0])
                    score = max(0.0, min(1.0, score_raw / 10.0))
                except ValueError:
                    logger.warning(f"Could not parse AI score: {response}")
                    score = 0.5
            else:
                score = 0.5
            
            # Determine matched topics (AI doesn't give us this directly)
            matched = self._keyword_score(tweet).matched_topics
            
            return TopicScore(
                tweet_id=tweet.id,
                score=score,
                matched_topics=matched,
                method="ai",
            )
            
        except Exception as e:
            logger.error(f"AI scoring failed: {e}")
            # Fall back to keyword scoring
            return self._keyword_score(tweet)
    
    async def score_tweet(self, tweet: TweetData) -> TopicScore:
        """
        Score a single tweet's relevance.
        
        Args:
            tweet: Tweet to score
            
        Returns:
            TopicScore with relevance information
        """
        if self.use_ai and self.ai_client:
            return await self._ai_score(tweet)
        return self._keyword_score(tweet)
    
    async def filter_tweets(
        self,
        tweets: list[TweetData],
    ) -> list[tuple[TweetData, TopicScore]]:
        """
        Filter tweets by topic relevance.
        
        Args:
            tweets: Tweets to filter
            
        Returns:
            List of (tweet, score) tuples that pass the minimum score
        """
        if not self.topics:
            # No topics = pass everything through
            logger.debug("No topics configured, passing all tweets")
            return [
                (t, TopicScore(t.id, 1.0, [], "none"))
                for t in tweets
            ]
        
        results: list[tuple[TweetData, TopicScore]] = []
        
        for tweet in tweets:
            score = await self.score_tweet(tweet)
            
            if score.score >= self.min_score:
                results.append((tweet, score))
                logger.debug(
                    f"Tweet {tweet.id} passed filter: "
                    f"score={score.score:.2f}, topics={score.matched_topics}"
                )
            else:
                logger.debug(
                    f"Tweet {tweet.id} filtered out: "
                    f"score={score.score:.2f} < {self.min_score}"
                )
        
        logger.info(
            f"TopicFilter: {len(results)}/{len(tweets)} tweets passed "
            f"(min_score={self.min_score})"
        )
        
        return results
    
    def get_status(self) -> dict:
        """Get filter status information."""
        return {
            "topics": self.topics,
            "topic_count": len(self.topics),
            "min_score": self.min_score,
            "use_ai": self.use_ai,
            "ai_available": self.ai_client is not None,
        }
