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

from cryptography.fernet import Fernet

from config import settings
from src.ai_client import AIClient
from src.background_worker import run_worker
from src.circuit_breaker import CircuitBreaker
from src.database import Database
from src.scheduler import calculate_schedule_time, get_delay_description
from src.telegram_client import TelegramClient
from src.x_delegate import GhostDelegate, SessionHealth

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
        self._session_health_task: Optional[asyncio.Task] = None

        # Session health monitoring (T021)
        self._session_degraded = False  # Graceful degradation flag

        # Circuit breakers for external services (T017-S1)
        self._circuit_breakers: dict[str, CircuitBreaker] = {}

    def _validate_config(self) -> None:
        """
        Validate required configuration at startup.

        Raises:
            ValueError: If required configuration is missing or invalid.
        """
        # Required settings that must be present
        required_settings = [
            ("DUMMY_USERNAME", settings.dummy_username),
            ("DUMMY_EMAIL", settings.dummy_email),
            ("DUMMY_PASSWORD", settings.dummy_password),
            ("MAIN_ACCOUNT_HANDLE", settings.main_account_handle),
            ("TELEGRAM_BOT_TOKEN", settings.telegram_bot_token),
            ("TELEGRAM_CHAT_ID", settings.telegram_chat_id),
            ("SUPABASE_URL", settings.supabase_url),
            ("SUPABASE_KEY", settings.supabase_key),
            ("AI_API_KEY", settings.ai_api_key),
        ]

        missing = [name for name, value in required_settings if not value]
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")

        # Validate cookie encryption key if provided
        if settings.cookie_encryption_key:
            try:
                Fernet(settings.cookie_encryption_key.encode())
            except Exception as e:
                raise ValueError(
                    f"Invalid COOKIE_ENCRYPTION_KEY format: {e}. "
                    "Generate a valid key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )
        else:
            logger.warning(
                "SECURITY WARNING: COOKIE_ENCRYPTION_KEY not set. "
                "Cookies will be stored in plaintext. "
                "Generate a key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )

        logger.info("Configuration validation passed")

    async def initialize(self) -> bool:
        """
        Initialize all bot components.

        Returns:
            True if all components initialized successfully, False otherwise.
        """
        logger.info("Initializing components...")

        try:
            # 0. Validate configuration first (fail fast)
            self._validate_config()

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

            # Register session alert callback (T021-S4)
            self.ghost.set_session_alert_callback(self._handle_session_alert)

            # Login with database for cooldown tracking
            if not await self.ghost.login_dummy(db=self.db):
                logger.error("Failed to login Ghost Delegate")
                return False
            logger.info("Ghost Delegate authenticated")

            # Perform initial session health check (T021-S1)
            initial_health = await self.ghost.check_session_health(auto_refresh=False, db=self.db)
            logger.info(f"Initial session health: {initial_health.value}")

            # 6. Initialize circuit breakers (T017-S1)
            self._circuit_breakers = {
                "twitter": CircuitBreaker(
                    name="twitter_api",
                    failure_threshold=5,
                    recovery_timeout=120,
                    half_open_max_calls=3,
                ),
                "ai": CircuitBreaker(
                    name="ai_service",
                    failure_threshold=3,
                    recovery_timeout=60,
                    half_open_max_calls=2,
                ),
            }
            logger.info("Circuit breakers initialized")

            # 7. Pre-populate seen tweets from database to avoid duplicates
            await self._load_seen_tweets()

            # 8. Perform crash recovery (T017-S5)
            await self._perform_crash_recovery()

            logger.info("All components initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize components: {e}")
            # Send error alert if telegram is initialized
            if self.telegram:
                try:
                    await self.telegram.send_error_alert(
                        "initialization_failed",
                        "Bot initialization failed",
                        {"error": str(e)}
                    )
                except Exception:
                    pass
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

    async def _perform_crash_recovery(self) -> None:
        """
        Perform crash recovery on startup (T017-S5).

        This method:
        1. Recovers stale/failed tweets
        2. Processes dead letter queue items
        3. Validates pending operations
        """
        try:
            logger.info("Performing crash recovery...")

            # Recover stale tweets
            recovered = await self.db.recover_stale_tweets(timeout_minutes=30)
            if recovered > 0:
                logger.info(f"Recovered {recovered} stale tweets")

            # Get dead letter queue stats
            dlq_stats = await self.db.get_dead_letter_stats()
            if dlq_stats["pending"] > 0:
                logger.warning(
                    f"Dead letter queue has {dlq_stats['pending']} pending items, "
                    f"{dlq_stats['exhausted']} exhausted"
                )

            logger.info("Crash recovery completed")

        except Exception as e:
            logger.error(f"Crash recovery failed: {e}")
            # Don't fail startup if recovery fails
            pass

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

    async def health_check_all(self) -> dict:
        """
        Comprehensive health check of all services (T017-S6).

        Returns:
            Dictionary with health status of all components.
        """
        logger.info("Running comprehensive health checks...")

        health = {
            "database": {
                "status": "unknown",
                "connected": False,
                "circuit_breaker": None,
            },
            "twitter": {
                "status": "unknown",
                "authenticated": False,
                "circuit_breaker": None,
            },
            "ai": {
                "status": "unknown",
                "available": False,
                "circuit_breaker": None,
            },
            "telegram": {
                "status": "unknown",
                "connected": False,
            },
            "overall": "unknown",
        }

        try:
            # Database health
            db_healthy = await self._check_db()
            health["database"]["status"] = "healthy" if db_healthy else "unhealthy"
            health["database"]["connected"] = db_healthy
            if self.db and hasattr(self.db, "circuit_breaker"):
                health["database"]["circuit_breaker"] = self.db.circuit_breaker.get_status()

            # Twitter/Ghost health
            twitter_healthy = self.ghost.is_authenticated if self.ghost else False
            health["twitter"]["status"] = "healthy" if twitter_healthy else "unhealthy"
            health["twitter"]["authenticated"] = twitter_healthy
            if "twitter" in self._circuit_breakers:
                health["twitter"]["circuit_breaker"] = self._circuit_breakers["twitter"].get_status()

            # AI health
            ai_healthy = await self._check_ai()
            health["ai"]["status"] = "healthy" if ai_healthy else "unhealthy"
            health["ai"]["available"] = ai_healthy
            if "ai" in self._circuit_breakers:
                health["ai"]["circuit_breaker"] = self._circuit_breakers["ai"].get_status()

            # Telegram health (if we're running, telegram is working)
            telegram_healthy = self.telegram is not None
            health["telegram"]["status"] = "healthy" if telegram_healthy else "unhealthy"
            health["telegram"]["connected"] = telegram_healthy

            # Overall health
            all_healthy = db_healthy and twitter_healthy and ai_healthy and telegram_healthy
            health["overall"] = "healthy" if all_healthy else "degraded"

            # Log summary
            logger.info(f"Health check complete: {health['overall']}")
            for service, status in health.items():
                if service != "overall" and isinstance(status, dict):
                    logger.info(f"  {service}: {status['status']}")

            return health

        except Exception as e:
            logger.error(f"Health check failed: {e}")
            health["overall"] = "error"
            return health

    async def _check_ai(self) -> bool:
        """Check AI service health."""
        try:
            return await self.ai.health_check()
        except Exception:
            return False

    async def _check_db(self) -> bool:
        """Check database connection."""
        try:
            return await self.db.health_check()
        except Exception:
            return False

    def _get_circuit_status(self) -> dict:
        """Get status of all circuit breakers."""
        return {
            name: breaker.get_status()
            for name, breaker in self._circuit_breakers.items()
        }

    async def start(self) -> None:
        """
        Start the bot and all background tasks.

        This method:
        1. Starts the background worker for publishing scheduled tweets
        2. Starts session health monitoring (T021-S2)
        3. Starts Telegram bot polling
        4. Starts the tweet monitoring loop
        """
        logger.info("Starting bot...")
        self._running = True

        # Start background worker
        self._worker_task = asyncio.create_task(
            run_worker(self.db, self.ghost, self.telegram),
            name="background_worker"
        )
        logger.info("Background worker started")

        # Start session health monitoring (T021-S2)
        self._session_health_task = asyncio.create_task(
            self._session_health_check_loop(),
            name="session_health_monitor"
        )
        logger.info("Session health monitor started")

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

        # Cancel session health task (T021)
        if self._session_health_task and not self._session_health_task.done():
            self._session_health_task.cancel()
            try:
                await self._session_health_task
            except asyncio.CancelledError:
                pass

        # Stop Telegram
        if self.telegram and self.telegram.app:
            await self.telegram.app.stop()

        logger.info("Bot stopped")

    # =========================================================================
    # Session Health Monitoring (T021)
    # =========================================================================

    async def _handle_session_alert(
        self,
        alert_type: str,
        message: str,
        details: dict,
    ) -> None:
        """
        Handle session alerts from Ghost Delegate (T021-S4).

        Args:
            alert_type: Type of alert
            message: Human-readable message
            details: Additional details
        """
        logger.warning(f"Session alert: {alert_type} - {message}")

        # Send alert via Telegram
        if self.telegram:
            await self.telegram.send_error_alert(
                f"session_{alert_type}",
                message,
                details,
            )

        # Enable graceful degradation if session is problematic
        if alert_type in ("session_expired", "session_refresh_failed", "auth_failed"):
            self._session_degraded = True
            logger.warning("Graceful degradation enabled due to session issues")

    async def _session_health_check_loop(self, interval: int = 300) -> None:
        """
        Periodically check session health (T021-S2).

        Args:
            interval: Seconds between health checks (default: 5 min)
        """
        logger.info(f"Starting session health monitor (interval: {interval}s)")

        while self._running:
            try:
                # Perform health check with auto-refresh (with db for cooldown tracking)
                health = await self.ghost.check_session_health(auto_refresh=True, db=self.db)

                if health == SessionHealth.HEALTHY:
                    if self._session_degraded:
                        logger.info("Session recovered - disabling graceful degradation")
                        self._session_degraded = False

                        # Notify user of recovery
                        await self.telegram.app.bot.send_message(
                            chat_id=self.telegram.chat_id,
                            text="✅ *Session Recovered*\n\nTwitter session is healthy again.",
                            parse_mode="Markdown",
                        )

                elif health == SessionHealth.DEGRADED:
                    logger.warning("Session is degraded - monitoring closely")

                elif health in (SessionHealth.EXPIRED, SessionHealth.FAILED):
                    self._session_degraded = True
                    logger.error(f"Session health critical: {health.value}")

                # Log session status
                status = self.ghost.get_session_status()
                logger.debug(f"Session status: {status}")

            except Exception as e:
                logger.error(f"Error in session health check: {e}")

            await asyncio.sleep(interval)

    def is_operational(self) -> bool:
        """
        Check if bot is operational (T021-S5).

        Returns:
            True if bot can perform operations, False if in degraded mode.
        """
        if self._session_degraded:
            return False
        if self.ghost and not self.ghost.is_session_healthy():
            return False
        return True

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
                # Check for graceful degradation (T021-S5)
                if self._session_degraded:
                    logger.warning("Skipping monitoring cycle - session degraded")
                    await asyncio.sleep(check_interval)
                    continue

                if not self.is_operational():
                    logger.warning("Skipping monitoring cycle - bot not operational")
                    await asyncio.sleep(check_interval)
                    continue

                targets = await self.db.get_target_accounts()

                if not targets:
                    logger.info("No target accounts configured")
                else:
                    logger.info(f"Monitoring {len(targets)} accounts...")

                    for handle in targets:
                        if not self._running:
                            break
                        # Skip if session became degraded mid-cycle
                        if self._session_degraded:
                            logger.warning("Session degraded mid-cycle, stopping monitoring")
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
            # 1. Generate AI reply with circuit breaker protection
            logger.info(f"Generating reply for tweet {tweet.id}")

            try:
                reply = await self._circuit_breakers["ai"].call(
                    self.ai.generate_reply,
                    tweet_author=author,
                    tweet_content=tweet.text,
                )
            except Exception as e:
                logger.error(f"AI circuit breaker error: {e}")
                # Send alert if circuit is open
                if self._circuit_breakers["ai"].state.value == "open":
                    await self.telegram.send_error_alert(
                        "circuit_breaker_open",
                        "AI service circuit breaker opened",
                        {"service": "ai", "error": str(e)}
                    )
                return

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
            # Send error alert for critical failures
            try:
                await self.telegram.send_error_alert(
                    "tweet_processing_failed",
                    f"Failed to process tweet {tweet.id}",
                    {"author": author, "error": str(e)}
                )
            except Exception:
                pass

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
