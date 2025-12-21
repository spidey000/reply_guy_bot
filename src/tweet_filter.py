"""
Tweet Filter Engine (Gatekeeper) for Reply Guy Bot.

This module evaluates incoming tweets using an LLM to determine if they are
worth responding to. This saves API costs and improves engagement quality
by filtering out irrelevant or low-quality tweets before generating replies.

Flow:
    1. Tweet arrives from aggregator
    2. TweetFilterEngine.analyze_tweet() evaluates relevance
    3. Returns FilterResult with decision, score, and reason
    4. Bot decides whether to generate a reply based on result

Usage:
    from src.tweet_filter import TweetFilterEngine, FilterDecision

    filter_engine = TweetFilterEngine(ai_client, settings)
    result = await filter_engine.analyze_tweet(tweet_id, content, author)

    if result.decision == FilterDecision.INTERESTING:
        # Generate reply
        pass
"""

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import httpx

from config import settings
from config.prompts import GATEKEEPER_SYSTEM_PROMPT, GATEKEEPER_USER_TEMPLATE

logger = logging.getLogger(__name__)


class FilterDecision(Enum):
    """Decision made by the Gatekeeper filter."""

    INTERESTING = "INTERESANTE"
    REJECTED = "RECHAZADO"
    ERROR = "ERROR"  # When filter fails, default to processing


@dataclass
class FilterResult:
    """Result of tweet analysis by the Gatekeeper."""

    decision: FilterDecision
    score: int  # 1-10 relevance score
    reason: str  # Brief explanation
    raw_response: Optional[str] = None  # For debugging


class TweetFilterEngine:
    """
    AI-powered tweet relevance filter (Gatekeeper).

    Evaluates tweets before generating replies to:
    - Save API costs by skipping irrelevant tweets
    - Improve engagement quality
    - Filter spam, toxicity, and low-effort content

    Attributes:
        enabled: Whether filtering is active
        min_score: Minimum score to consider tweet interesting
        temperature: AI temperature for consistent decisions
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        """
        Initialize the filter engine.

        Args:
            base_url: AI API base URL. Defaults to settings.
            api_key: AI API key. Defaults to settings.
            model: AI model to use. Defaults to filter_model or ai_model.
        """
        self.base_url = base_url or settings.ai_base_url
        self.api_key = api_key or settings.ai_api_key
        # Use filter_model if set, otherwise fallback to ai_model
        if model:
            self.model = model
        elif settings.filter_model:
            self.model = settings.filter_model
        else:
            self.model = settings.ai_model

        # Filter settings
        self.enabled = settings.filter_enabled
        self.min_score = settings.filter_min_score
        self.temperature = settings.filter_temperature

        # Statistics
        self._total_analyzed = 0
        self._passed = 0
        self._rejected = 0
        self._errors = 0

        logger.info(
            f"TweetFilterEngine initialized (enabled={self.enabled}, "
            f"model={self.model}, min_score={self.min_score})"
        )

    def build_evaluation_prompt(self, content: str, author: str) -> str:
        """
        Build the user prompt for tweet evaluation.

        Args:
            content: Tweet text content.
            author: Tweet author handle.

        Returns:
            Formatted prompt string.
        """
        return GATEKEEPER_USER_TEMPLATE.format(
            author=author,
            content=content,
        )

    async def analyze_tweet(
        self,
        tweet_id: str,
        content: str,
        author: str,
    ) -> FilterResult:
        """
        Analyze a tweet and decide if it's worth responding to.

        Args:
            tweet_id: Unique tweet identifier (for logging).
            content: Tweet text content.
            author: Tweet author handle.

        Returns:
            FilterResult with decision, score, and reason.
        """
        # If filter is disabled, pass everything
        if not self.enabled:
            logger.debug(f"Filter disabled, passing tweet {tweet_id}")
            return FilterResult(
                decision=FilterDecision.INTERESTING,
                score=10,
                reason="Filter disabled - auto-pass",
            )

        self._total_analyzed += 1

        try:
            # Build messages
            messages = [
                {"role": "system", "content": GATEKEEPER_SYSTEM_PROMPT},
                {"role": "user", "content": self.build_evaluation_prompt(content, author)},
            ]

            # Call AI
            logger.debug(f"Analyzing tweet {tweet_id} from @{author}")
            raw_response = await self._call_ai(messages)

            # Parse response
            result = self._parse_response(raw_response, tweet_id)

            # Apply minimum score threshold
            if result.decision == FilterDecision.INTERESTING and result.score < self.min_score:
                logger.info(
                    f"Tweet {tweet_id} scored {result.score} < {self.min_score}, rejecting"
                )
                result = FilterResult(
                    decision=FilterDecision.REJECTED,
                    score=result.score,
                    reason=f"Score {result.score} below threshold {self.min_score}",
                    raw_response=raw_response,
                )

            # Update stats
            if result.decision == FilterDecision.INTERESTING:
                self._passed += 1
                logger.info(f"[PASS] Tweet {tweet_id}: {result.reason} (score={result.score})")
            else:
                self._rejected += 1
                logger.info(f"[REJECT] Tweet {tweet_id}: {result.reason} (score={result.score})")

            return result

        except Exception as e:
            logger.error(f"Filter error for tweet {tweet_id}: {e}")
            self._errors += 1
            # On error, default to passing (fail-open)
            return FilterResult(
                decision=FilterDecision.INTERESTING,
                score=5,
                reason=f"Filter error (fail-open): {str(e)[:50]}",
            )

    async def _call_ai(self, messages: list) -> str:
        """
        Call the AI API for evaluation.

        Args:
            messages: Chat messages to send.

        Returns:
            Raw response content string.

        Raises:
            Exception: On API errors.
        """
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                url=f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": 150,  # Short responses expected
                    "temperature": self.temperature,
                },
            )
            response.raise_for_status()
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        return content.strip()

    def _parse_response(self, raw_response: str, tweet_id: str) -> FilterResult:
        """
        Parse the AI response into a FilterResult.

        Handles various response formats and edge cases.

        Args:
            raw_response: Raw AI response string.
            tweet_id: Tweet ID for logging.

        Returns:
            Parsed FilterResult.
        """
        try:
            # Clean up response - remove markdown code blocks if present
            cleaned = raw_response.strip()
            
            # Remove ```json ... ``` or ``` ... ``` wrappers
            if cleaned.startswith("```"):
                # Find the end of the code block
                lines = cleaned.split("\n")
                # Remove first line (```json or ```)
                lines = lines[1:]
                # Remove last line if it's just ```
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines)
            
            # Try to extract JSON from response
            json_match = re.search(r'\{[^{}]*\}', cleaned, re.DOTALL)
            if not json_match:
                raise ValueError("No JSON object found in response")

            data = json.loads(json_match.group())

            # Extract fields
            decision_str = data.get("decision", "").upper()
            score = int(data.get("score", 5))
            reason = data.get("reason", "No reason provided")

            # Map decision
            if "INTERESANTE" in decision_str or "INTERESTING" in decision_str:
                decision = FilterDecision.INTERESTING
            elif "RECHAZADO" in decision_str or "REJECTED" in decision_str:
                decision = FilterDecision.REJECTED
            else:
                logger.warning(f"Unknown decision '{decision_str}' for tweet {tweet_id}")
                decision = FilterDecision.INTERESTING  # Fail-open

            # Clamp score
            score = max(1, min(10, score))

            return FilterResult(
                decision=decision,
                score=score,
                reason=reason,
                raw_response=raw_response,
            )

        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            logger.warning(f"Failed to parse filter response for {tweet_id}: {e}")
            logger.debug(f"Raw response: {raw_response}")
            # Fail-open on parse errors
            return FilterResult(
                decision=FilterDecision.INTERESTING,
                score=5,
                reason=f"Parse error (fail-open): {str(e)[:50]}",
                raw_response=raw_response,
            )

    def get_stats(self) -> dict:
        """
        Get filter statistics.

        Returns:
            Dict with total_analyzed, passed, rejected, errors, pass_rate.
        """
        pass_rate = self._passed / self._total_analyzed if self._total_analyzed > 0 else 0.0
        return {
            "total_analyzed": self._total_analyzed,
            "passed": self._passed,
            "rejected": self._rejected,
            "errors": self._errors,
            "pass_rate": round(pass_rate, 3),
        }

    def is_interesting(self, result: FilterResult) -> bool:
        """
        Check if a filter result indicates an interesting tweet.

        Args:
            result: FilterResult to check.

        Returns:
            True if tweet should be processed.
        """
        return result.decision == FilterDecision.INTERESTING
