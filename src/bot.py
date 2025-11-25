"""
Main orchestrator for Reply Guy Bot.

This module coordinates all bot components and manages the main event loop.

Responsibilities:
    1. Initialize all components (AI, Telegram, Database, Ghost Delegate)
    2. Monitor target accounts for new tweets
    3. Generate AI replies for detected tweets
    4. Send approval requests via Telegram
    5. Schedule approved tweets via Burst Mode
    6. Publish tweets using Ghost Delegate

Flow:
    ┌─────────────────────────────────────────────────────────────┐
    │  1. Monitor target accounts for new tweets                  │
    │  2. Generate AI reply                                       │
    │  3. Send to Telegram for approval                          │
    │  4. On approval → Calculate schedule time (Burst Mode)     │
    │  5. Background worker publishes at scheduled time          │
    │  6. Ghost Delegate handles secure publication              │
    └─────────────────────────────────────────────────────────────┘

Entry Point:
    python -m src.bot
"""

import asyncio
import logging
import signal
from typing import Optional, Set

from config import settings
from src.ai_client import AIClient
from src.background_worker import run_worker
from src.database import Database
from src.scheduler import calculate_schedule_time, get_delay_description
from src.telegram_client import TelegramClient
from src.x_delegate import GhostDelegate

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class ReplyGuyBot:
    """
    Main orchestrator that ties all components together.

    This class handles:
    - Component initialization
    - Tweet monitoring loop
    - Approval workflow callbacks
    - Background worker coordination
    - Graceful shutdown
    """

    def __init__(self) -> None:
        """Initialize the bot with empty component references."""
        self.ai: Optional[AIClient] = None
        self.db: Optional[Database] = None
        self.telegram: Optional[TelegramClient] = None
        self.ghost: Optional[GhostDelegate] = None

        self._running = False
        self._seen_tweets: Set[str] = set()
        self._worker_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None

    async def initialize(self) -> bool:
        """
        Initialize all bot components.

        Returns:
            True if all components initialized successfully, False otherwise.
        """
        logger.info("Initializing components...")

        try:
            # 1. Initialize Database
            self.db = Database()
            logger.info("Database initialized")

            # 2. Initialize AI Client
            self.ai = AIClient(
                base_url=settings.ai_base_url,
                api_key=settings.ai_api_key,
                model=settings.ai_model,
            )
            logger.info(f"AI Client initialized (model: {settings.ai_model})")

            # 3. Initialize Telegram
            self.telegram = TelegramClient()
            await self.telegram.initialize()
            self.telegram.set_database(self.db)
            logger.info("Telegram client initialized")

            # 4. Wire approval callbacks
            self.telegram.on_approve(self._handle_approve)
            self.telegram.on_reject(self._handle_reject)
            logger.info("Approval callbacks wired")

            # 5. Initialize Ghost Delegate
            self.ghost = GhostDelegate()
            if not await self.ghost.login_dummy():
                logger.error("Failed to login Ghost Delegate")
                return False
            logger.info("Ghost Delegate authenticated")

            # 6. Pre-populate seen tweets from database to avoid duplicates
            await self._load_seen_tweets()

            logger.info("All components initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize components: {e}")
            return False

    async def _load_seen_tweets(self) -> None:
        """Load existing tweet IDs from database to avoid re-processing."""
        try:
            pending = await self.db.get_pending_tweets()
            for tweet in pending:
                tweet_id = tweet.get("target_tweet_id")
                if tweet_id:
                    self._seen_tweets.add(tweet_id)
            logger.info(f"Loaded {len(self._seen_tweets)} existing tweets to skip")
        except Exception as e:
            logger.warning(f"Could not load seen tweets: {e}")

    async def health_check(self) -> bool:
        """
        Verify all components are healthy.

        Returns:
            True if all components pass health checks.
        """
        logger.info("Running health checks...")

        checks = {
            "AI": await self._check_ai(),
            "Ghost": self.ghost.is_authenticated if self.ghost else False,
            "Database": await self._check_db(),
        }

        for name, ok in checks.items():
            status = "OK" if ok else "FAIL"
            logger.info(f"  {name}: {status}")

        return all(checks.values())

    async def _check_ai(self) -> bool:
        """Check AI service health."""
        try:
            return await self.ai.health_check()
        except Exception:
            return False

    async def _check_db(self) -> bool:
        """Check database connection."""
        try:
            await self.db.get_pending_count()
            return True
        except Exception:
            return False

    async def start(self) -> None:
        """
        Start the bot and all background tasks.

        This method:
        1. Starts the background worker for publishing scheduled tweets
        2. Starts Telegram bot polling
        3. Starts the tweet monitoring loop
        """
        logger.info("Starting bot...")
        self._running = True

        # Start background worker
        self._worker_task = asyncio.create_task(
            run_worker(self.db, self.ghost, self.telegram),
            name="background_worker"
        )
        logger.info("Background worker started")

        # Start tweet monitoring
        self._monitor_task = asyncio.create_task(
            self._monitor_tweets(),
            name="tweet_monitor"
        )
        logger.info("Tweet monitor started")

        # Start Telegram polling (this blocks)
        logger.info("Starting Telegram polling...")
        await self.telegram.app.run_polling(drop_pending_updates=True)

    async def stop(self) -> None:
        """Gracefully stop the bot and all tasks."""
        logger.info("Stopping bot...")
        self._running = False

        # Cancel background tasks
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        # Stop Telegram
        if self.telegram and self.telegram.app:
            await self.telegram.app.stop()

        logger.info("Bot stopped")

    # =========================================================================
    # Tweet Monitoring
    # =========================================================================

    async def _monitor_tweets(self, check_interval: int = 300) -> None:
        """
        Monitor target accounts for new tweets.

        Args:
            check_interval: Seconds between monitoring cycles (default: 5 min)
        """
        logger.info(f"Starting tweet monitor (interval: {check_interval}s)")

        while self._running:
            try:
                targets = await self.db.get_target_accounts()

                if not targets:
                    logger.info("No target accounts configured")
                else:
                    logger.info(f"Monitoring {len(targets)} accounts...")

                    for handle in targets:
                        if not self._running:
                            break
                        await self._check_account(handle)
                        # Rate limit protection - wait between accounts
                        await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")

            # Wait before next cycle
            await asyncio.sleep(check_interval)

    async def _check_account(self, handle: str) -> None:
        """
        Check a single account for new tweets.

        Args:
            handle: Twitter handle to check (without @)
        """
        try:
            # Use Ghost Delegate's client to fetch tweets
            user = await self.ghost.client.get_user_by_screen_name(handle)
            tweets = await user.get_tweets("Tweets", count=10)

            for tweet in tweets:
                # Skip if already processed
                if tweet.id in self._seen_tweets:
                    continue

                # Mark as seen
                self._seen_tweets.add(tweet.id)

                # Skip retweets and replies
                if hasattr(tweet, 'retweeted_tweet') and tweet.retweeted_tweet:
                    continue
                if hasattr(tweet, 'in_reply_to') and tweet.in_reply_to:
                    continue

                logger.info(f"New tweet from @{handle}: {tweet.id}")
                await self._process_new_tweet(tweet, handle)

        except Exception as e:
            logger.error(f"Error checking @{handle}: {e}")

    async def _process_new_tweet(self, tweet, author: str) -> None:
        """
        Process a new tweet: generate reply and send for approval.

        Args:
            tweet: Twikit tweet object
            author: Twitter handle of the author
        """
        try:
            # 1. Generate AI reply
            logger.info(f"Generating reply for tweet {tweet.id}")
            reply = await self.ai.generate_reply(
                tweet_author=author,
                tweet_content=tweet.text,
            )

            if not reply:
                logger.warning(f"AI failed to generate reply for {tweet.id}")
                return

            # 2. Store in database queue
            queue_id = await self.db.add_to_queue(
                target_tweet_id=tweet.id,
                target_author=author,
                target_content=tweet.text,
                reply_text=reply,
            )
            logger.info(f"Added to queue: {queue_id}")

            # 3. Send to Telegram for approval
            tweet_data = {
                "id": queue_id,  # Use queue ID for callbacks
                "author": author,
                "content": tweet.text,
            }
            await self.telegram.send_approval_request(tweet_data, reply)
            logger.info(f"Sent approval request for {queue_id}")

        except Exception as e:
            logger.error(f"Error processing tweet {tweet.id}: {e}")

    # =========================================================================
    # Approval Callbacks
    # =========================================================================

    async def _handle_approve(self, tweet_id: str) -> None:
        """
        Handle tweet approval from Telegram.

        Args:
            tweet_id: Queue ID of the approved tweet
        """
        try:
            # Calculate scheduled time using Burst Mode
            scheduled = calculate_schedule_time()
            delay_desc = get_delay_description(scheduled)

            # Update database
            await self.db.approve_tweet(tweet_id, scheduled)

            # Send confirmation
            await self.telegram.send_scheduled_confirmation(tweet_id, delay_desc)

            logger.info(f"Approved tweet {tweet_id}, scheduled {delay_desc}")

        except Exception as e:
            logger.error(f"Error approving tweet {tweet_id}: {e}")

    async def _handle_reject(self, tweet_id: str) -> None:
        """
        Handle tweet rejection from Telegram.

        Args:
            tweet_id: Queue ID of the rejected tweet
        """
        try:
            await self.db.reject_tweet(tweet_id)
            logger.info(f"Rejected tweet {tweet_id}")

        except Exception as e:
            logger.error(f"Error rejecting tweet {tweet_id}: {e}")


async def main() -> None:
    """
    Main entry point for the bot.

    Initializes all components and starts the main event loop.
    """
    logger.info("=" * 60)
    logger.info("Starting Reply Guy Bot v0.1.0")
    logger.info(f"Burst Mode: {'enabled' if settings.burst_mode_enabled else 'disabled'}")
    logger.info(f"Main Account: @{settings.main_account_handle}")
    logger.info("=" * 60)

    bot = ReplyGuyBot()

    # Initialize components
    if not await bot.initialize():
        logger.error("Failed to initialize bot - exiting")
        return

    # Run health checks
    if not await bot.health_check():
        logger.warning("Some health checks failed - continuing anyway")

    # Handle shutdown signals
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("Shutdown signal received")
        asyncio.create_task(bot.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    # Start the bot
    try:
        await bot.start()
    except asyncio.CancelledError:
        logger.info("Bot cancelled")
    except Exception as e:
        logger.error(f"Bot error: {e}")
    finally:
        await bot.stop()

    logger.info("Bot shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
