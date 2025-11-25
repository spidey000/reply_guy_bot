"""
AI Client - OpenAI API compatible client for reply generation.

This module provides a provider-agnostic AI client that works with any
service implementing the OpenAI API specification.

Supported Providers:
    - OpenAI (GPT-4, GPT-4o, GPT-4o-mini, etc.)
    - Ollama (local models: llama, mistral, etc.)
    - LMStudio (local models)
    - Together AI
    - Groq
    - Any OpenAI-compatible endpoint

Configuration Examples:
    OpenAI:
        AI_BASE_URL=https://api.openai.com/v1
        AI_API_KEY=sk-xxx
        AI_MODEL=gpt-4o-mini

    Ollama:
        AI_BASE_URL=http://localhost:11434/v1
        AI_API_KEY=ollama
        AI_MODEL=llama3.2

    LMStudio:
        AI_BASE_URL=http://localhost:1234/v1
        AI_API_KEY=lm-studio
        AI_MODEL=local-model

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

from openai import AsyncOpenAI

from config.prompts import REPLY_TEMPLATE, SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class AIClient:
    """
    OpenAI API compatible client for generating tweet replies.

    This client uses the OpenAI SDK which is compatible with many
    AI providers that implement the same API specification.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        system_prompt: str | None = None,
    ) -> None:
        """
        Initialize the AI client.

        Args:
            base_url: API endpoint URL.
            api_key: API key for authentication.
            model: Model identifier to use.
            system_prompt: Optional custom system prompt.
        """
        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
        )
        self.model = model
        self.system_prompt = system_prompt or SYSTEM_PROMPT

        logger.info(f"AI Client initialized: {base_url} / {model}")

    async def generate_reply(
        self,
        tweet_author: str,
        tweet_content: str,
        context: str = "",
        max_tokens: int = 150,
        temperature: float = 0.8,
    ) -> Optional[str]:
        """
        Generate a reply for a tweet.

        Args:
            tweet_author: Twitter handle of the tweet author.
            tweet_content: Content of the original tweet.
            context: Additional context for the reply.
            max_tokens: Maximum tokens in response.
            temperature: Creativity setting (0.0-1.0).

        Returns:
            Generated reply text, or None if generation failed.
        """
        user_prompt = REPLY_TEMPLATE.format(
            author=tweet_author,
            content=tweet_content,
            context=context,
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )

            reply = response.choices[0].message.content.strip()

            # Clean up response (remove quotes if present)
            reply = self._clean_reply(reply)

            logger.debug(f"Generated reply: {reply[:50]}...")
            return reply

        except Exception as e:
            logger.error(f"Failed to generate reply: {e}")
            return None

    def _clean_reply(self, reply: str) -> str:
        """
        Clean up the generated reply.

        Removes surrounding quotes and other artifacts that
        models sometimes add.

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

        # Ensure it's within Twitter's character limit
        if len(reply) > 280:
            reply = reply[:277] + "..."

        return reply.strip()

    async def health_check(self) -> bool:
        """
        Check if the AI service is available.

        Returns:
            True if service is responding, False otherwise.
        """
        try:
            await self.client.models.list()
            return True
        except Exception as e:
            logger.error(f"AI health check failed: {e}")
            return False
