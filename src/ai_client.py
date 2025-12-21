"""
AI Client - OpenRouter API client for reply generation.

This module provides an AI client that uses the OpenRouter API
to generate tweet replies using various LLM providers.

Supported Models (via OpenRouter):
    - openai/gpt-4o-mini (cheap & fast)
    - deepseek/deepseek-chat (very cheap)
    - google/gemini-flash-1.5 (fast)
    - google/gemini-2.0-flash-001 (latest)
    - And many more at https://openrouter.ai/models

Configuration:
    AI_BASE_URL=https://openrouter.ai/api/v1
    AI_API_KEY=sk-or-v1-xxx
    AI_MODEL=openai/gpt-4o-mini

Usage:
    from src.ai_client import AIClient
    from config import settings

    client = AIClient(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        model=settings.ai_model,
    )

    reply = await client.generate_reply(
        tweet_author="elonmusk",
        tweet_content="AI is transforming everything",
    )
"""

import logging
from typing import Optional

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
    before_sleep_log,
)

from config.prompts import REPLY_TEMPLATE, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Character limit constants
MAX_TWEET_LENGTH = 280
TARGET_LENGTH = 250  # Buffer for safety
MAX_LENGTH_RETRIES = 5


class AIClient:
    """
    OpenRouter API client for generating tweet replies.

    Uses asynchronous HTTP requests to call the OpenRouter API endpoint.
    Implements retry logic for responses exceeding character limits.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        fallback_models: list[str] | None = None,
        system_prompt: str | None = None,
    ) -> None:
        """
        Initialize the AI client.

        Args:
            base_url: API endpoint URL (e.g., https://openrouter.ai/api/v1).
            api_key: OpenRouter API key.
            model: Model identifier (e.g., openai/gpt-4o-mini).
            system_prompt: Optional custom system prompt.
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.fallback_models = fallback_models or []
        self.system_prompt = system_prompt or SYSTEM_PROMPT

        # Length violation metrics
        self._length_violations = 0
        self._total_generations = 0

        logger.info(
            f"AI Client initialized: {self.base_url} / {model} "
            f"(Fallbacks: {len(self.fallback_models)})"
        )

    async def generate_reply(
        self,
        tweet_author: str,
        tweet_content: str,
        context: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.8,
    ) -> Optional[str]:
        """
        Generate a reply for a tweet with length validation and retry.

        Args:
            tweet_author: Twitter handle of the tweet author.
            tweet_content: Content of the original tweet.
            context: Additional context for the reply.
            max_tokens: Maximum tokens in response.
            temperature: Creativity setting (0.0-1.0).

        Returns:
            Generated reply text, or None if generation failed or exceeded length.
        """
        user_prompt = REPLY_TEMPLATE.format(
            author=tweet_author,
            content=tweet_content,
            context=context,
        )

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
        # Log the full prompt for debugging
        logger.debug(f"Generated prompt messages: {messages}")

        reply = None

        for attempt in range(MAX_LENGTH_RETRIES + 1):
            # Try primary model and fallbacks
            chain = [self.model] + self.fallback_models
            
            for model_attempt, current_model in enumerate(chain):
                try:
                    logger.debug(f"Attempting generation with {current_model} (attempt {attempt})")
                    content = await self._generate_with_retry(
                        model=current_model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )

                    reply = self._clean_reply(content)
                    if not reply:
                        raise ValueError("Received empty response from model")
                    break # Success!
                    
                except Exception as e:
                    logger.warning(f"Model {current_model} failed: {e}")
                    if model_attempt == len(chain) - 1:
                        # All models failed for this retry attempt
                        logger.error(f"All models failed for attempt {attempt}")
                        # If this was the last length retry, return None
                        if attempt == MAX_LENGTH_RETRIES:
                             return None
                        # Otherwise continue to next length retry (which restarts the model chain)
                        raise e 
                        
            # If we got here with reply=None, it means all models failed for this length attempt
            if not reply:
                continue

            self._total_generations += 1

            # Check length
            if len(reply) <= MAX_TWEET_LENGTH:
                if attempt > 0:
                    logger.info(
                        f"Reply shortened after {attempt} retry(ies): {len(reply)} chars"
                    )
                logger.debug(f"Generated reply ({len(reply)} chars): {reply[:50]}...")
                return reply

            # Log violation
            self._length_violations += 1
            logger.warning(
                f"Reply exceeded {MAX_TWEET_LENGTH} chars ({len(reply)}), "
                f"attempt {attempt + 1}/{MAX_LENGTH_RETRIES + 1}"
            )

            # On retry, add explicit shortening instruction
            if attempt < MAX_LENGTH_RETRIES:
                messages = [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": reply},
                    {
                        "role": "user",
                        "content": f"That reply is {len(reply)} characters which exceeds the 250 character limit. Please rewrite it to be shorter while keeping the same meaning.",
                    },
                ]

        # All retries exhausted - skip this tweet (no truncation)
        logger.error(
            f"Max retries ({MAX_LENGTH_RETRIES}) exceeded, reply still {len(reply)} chars. Skipping."
        )
        logger.debug(
            f"Length violation rate: {self._length_violations}/{self._total_generations}"
        )
        return None

    def _is_retryable_error(e: Exception) -> bool:
        """Check if exception is retryable (Connection, Timeout, 429)."""
        if isinstance(e, (httpx.ConnectError, httpx.TimeoutException)):
            return True
        if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
            return True
        return False

    @retry(
        stop=stop_after_attempt(5), # More attempts for 429s (up from 3)
        wait=wait_exponential(multiplier=1, min=2, max=30), # Backoff up to 30s
        retry=retry_if_exception(_is_retryable_error),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _generate_with_retry(
        self,
        model: str,
        messages: list,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """
        Generate AI response with automatic retry logic for network errors.

        This method implements exponential backoff for transient errors:
        - Retry delays: 1s, 2s, 4s (exponential backoff with base 2)
        - Max attempts: 3
        - Only retries on: ConnectionError, Timeout

        Args:
            messages: List of message dicts with role and content.
            max_tokens: Maximum tokens in response.
            temperature: Creativity setting (0.0-1.0).

        Returns:
            Generated content string.

        Raises:
            httpx.ConnectError: After exhausting retries on connection errors.
            httpx.TimeoutException: After exhausting retries on timeout errors.
            httpx.HTTPStatusError: On non-retryable HTTP errors.
        """
        logger.debug("Attempting AI generation...")

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                url=f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )

            response.raise_for_status()
            data = response.json()

        try:
            content = data["choices"][0]["message"]["content"]
            if not content or not content.strip():
                logger.warning(f"Model {model} returned empty/whitespace content. Raw response: {data}")
                logger.warning(f"Response headers: {dict(response.headers)}")
            return content.strip()
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Unexpected API response structure: {e}")
            logger.error(f"Raw response: {data}")
            raise

    def _clean_reply(self, reply: str) -> str:
        """
        Clean up the generated reply.

        Removes surrounding quotes and other artifacts that
        models sometimes add. Does NOT truncate.

        Args:
            reply: Raw generated reply.

        Returns:
            Cleaned reply text.
        """
        # Remove surrounding quotes
        if reply.startswith('"') and reply.endswith('"'):
            reply = reply[1:-1]
        if reply.startswith("'") and reply.endswith("'"):
            reply = reply[1:-1]

        return reply.strip()

    async def health_check(self) -> bool:
        """
        Check if the AI service is available.

        Returns:
            True if service is responding, False otherwise.
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    url=f"{self.base_url}/models",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                    },
                )
                return response.status_code == 200
        except Exception as e:
            logger.error(f"AI health check failed: {e}")
            return False

    def get_length_stats(self) -> dict:
        """
        Get length violation statistics.

        Returns:
            Dict with total_generations, length_violations, and violation_rate.
        """
        rate = (
            self._length_violations / self._total_generations
            if self._total_generations > 0
            else 0.0
        )
        return {
            "total_generations": self._total_generations,
            "length_violations": self._length_violations,
            "violation_rate": round(rate, 3),
        }
