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
from src.alerts import AlertManager, AlertLevel, initialize_alerts
from src.background_worker import run_worker
from src.circuit_breaker import CircuitBreaker
from src.database import Database
from src.scheduler import calculate_schedule_time, get_delay_description
from src.telegram_client import TelegramClient
from src.topic_filter import TopicFilter
from src.tweet_filter import TweetFilterEngine, FilterDecision
from src.tweet_sources import (
    TweetAggregator,
    TargetAccountSource,
    SearchQuerySource,
    HomeFeedSource,
)
from src.x_delegate import GhostDelegate, SessionHealth

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
# Suppress noisy HTTP logs
logging.getLogger("httpx").setLevel(logging.WARNING)

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

        # Alert manager for centralized notifications
        self.alerts: Optional[AlertManager] = None

        # Session health monitoring (T021)
        self._session_degraded = False  # Graceful degradation flag

        # Circuit breakers for external services (T017-S1)
        self._circuit_breakers: dict[str, CircuitBreaker] = {}

        # Multi-source tweet discovery
        self._aggregator: Optional[TweetAggregator] = None
        self._topic_filter: Optional[TopicFilter] = None

        # Gatekeeper filter (AI-powered relevance analysis)
        self._filter_engine: Optional[TweetFilterEngine] = None

    def _validate_config(self) -> None:
        """
        Validate required configuration at startup.

        Raises:
            ValueError: If required configuration is missing or invalid.
        """
        # Required settings that must be present
        required_settings = [
            ("DUMMY_USERNAME", settings.dummy_username1),
            ("DUMMY_EMAIL", settings.dummy_email1),
            ("DUMMY_PASSWORD", settings.dummy_password1),
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

        # Validate cookie encryption key format (now required)
        try:
            Fernet(settings.cookie_encryption_key.encode())
        except Exception as e:
            raise ValueError(
                f"Invalid COOKIE_ENCRYPTION_KEY format: {e}. "
                "Generate a valid key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
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

            # 1. Initialize Database (with SQLite fallback)
            try:
                self.db = Database()
                # Test connection
                if not await self.db.health_check():
                    raise Exception("Supabase health check failed")
                logger.info("Database initialized (Supabase)")
            except Exception as e:
                logger.warning(f"Supabase unavailable ({e}), falling back to SQLite")
                from src.database_sqlite import SQLiteDatabase
                self.db = SQLiteDatabase()
                logger.info("Database initialized (SQLite fallback)")

            # 2. Initialize AI Client
            self.ai = AIClient(
                base_url=settings.ai_base_url,
                api_key=settings.ai_api_key,
                model=settings.ai_model,
                fallback_models=settings.ai_fallback_models,
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

            # 5. Initialize AlertManager
            self.alerts = initialize_alerts(
                telegram_client=self.telegram,
                settings=settings,
            )
            logger.info("AlertManager initialized")

            # 6. Initialize Ghost Delegate
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

            # 7. Initialize Gatekeeper filter
            self._filter_engine = TweetFilterEngine()
            logger.info(f"Gatekeeper filter initialized (enabled={settings.filter_enabled})")

            # 8. Pre-populate seen tweets from database to avoid duplicates
            await self._load_seen_tweets()

            # 9. Perform crash recovery (T017-S5)
            await self._perform_crash_recovery()

            # 10. Initialize multi-source tweet discovery
            await self._setup_sources()

            logger.info("All components initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize components: {e}")
            # Send error alert if alerts are initialized
            if self.alerts:
                await self.alerts.critical(
                    "initialization_failed",
                    "Bot initialization failed",
                    error=str(e)
                )
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

    async def _setup_sources(self) -> None:
        """
        Initialize multi-source tweet discovery system.
        
        Loads configuration from database and sets up:
        - Target account sources
        - Search query sources
        - Home feed source (if enabled)
        - Topic filter for relevance scoring
        """
        try:
            logger.info("Setting up tweet sources...")
            
            # Initialize aggregator
            self._aggregator = TweetAggregator()
            
            # Load target accounts
            targets = await self.db.get_target_accounts()
            for handle in targets:
                source = TargetAccountSource(handle)
                self._aggregator.add_source(source)
            
            # Load search queries
            searches = await self.db.get_search_queries()
            for search in searches:
                source = SearchQuerySource(
                    query=search["query"],
                    product=search.get("product", "Latest"),
                )
                self._aggregator.add_source(source)
            
            # Check if home feed is enabled
            home_settings = await self.db.get_source_settings("home_feed_following")
            if home_settings.get("enabled"):
                source = HomeFeedSource(feed_type="following")
                self._aggregator.add_source(source)
            
            # Initialize topic filter
            topics = await self.db.get_topics()
            self._topic_filter = TopicFilter(
                topics=topics,
                min_score=0.5,  # Default minimum relevance
                use_ai=False,  # Keyword-based for now
            )
            
            # Sync seen tweets with aggregator
            self._aggregator.mark_seen_batch(list(self._seen_tweets))
            
            logger.info(
                f"Sources initialized: {len(self._aggregator.get_enabled_sources())} sources, "
                f"{len(topics)} topic filters"
            )
            
        except Exception as e:
            logger.error(f"Failed to setup sources: {e}")
            # Initialize empty aggregator as fallback
            self._aggregator = TweetAggregator()
            self._topic_filter = TopicFilter()

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

        # Start Telegram polling (PTB v20+ compatible - manual start)
        logger.info("Starting Telegram polling...")
        await self.telegram.app.initialize()
        await self.telegram.app.start()
        await self.telegram.app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram polling active")

        # Send startup notification via AlertManager
        await self.alerts.startup()
        
        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1)

    async def stop(self, reason: str = "Manual shutdown") -> None:
        """Gracefully stop the bot and all tasks."""
        logger.info(f"Stopping bot (Reason: {reason})...")
        self._running = False

        # Send stop notification via AlertManager
        if self.alerts:
            await self.alerts.shutdown(reason=reason)

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

        # Stop Telegram (PTB v20+ proper shutdown sequence)
        if self.telegram and self.telegram.app:
            try:
                if self.telegram.app.updater and self.telegram.app.updater.running:
                    await self.telegram.app.updater.stop()
                if self.telegram.app.running:
                    await self.telegram.app.stop()
                    await self.telegram.app.shutdown()
            except Exception as e:
                logger.debug(f"Telegram stop error (ignored): {e}")

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

        # Send alert via AlertManager
        if self.alerts:
            await self.alerts.warning(
                f"session_{alert_type}",
                message,
                **details,
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
        Monitor all tweet sources for new content.

        Uses the TweetAggregator to fetch from multiple sources and
        TopicFilter to filter by relevance.

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

                # Refresh sources from database (in case config changed via Telegram)
                await self._refresh_sources()

                if not self._aggregator or not self._aggregator.get_enabled_sources():
                    logger.info("No tweet sources configured")
                    await asyncio.sleep(check_interval)
                    continue

                # Fetch tweets from all sources
                logger.info(f"Fetching from {len(self._aggregator.get_enabled_sources())} sources...")
                tweets = await self._aggregator.fetch_all(
                    client=self.ghost.client,
                    count_per_source=10,
                )

                if not tweets:
                    logger.debug("No new tweets found")
                    await asyncio.sleep(check_interval)
                    continue

                # Apply topic filtering
                if self._topic_filter and self._topic_filter.get_topics():
                    filtered_results = await self._topic_filter.filter_tweets(tweets)
                    logger.info(
                        f"Topic filter: {len(filtered_results)}/{len(tweets)} tweets passed"
                    )
                else:
                    # No topics = pass all tweets
                    filtered_results = [(t, None) for t in tweets]
                    logger.debug("No topic filters - processing all tweets")

                # Process filtered tweets
                for tweet_data, score in filtered_results:
                    if not self._running:
                        break
                    if self._session_degraded:
                        logger.warning("Session degraded mid-cycle, stopping")
                        break

                    # Mark as seen in both places
                    self._seen_tweets.add(tweet_data.id)
                    self._aggregator.mark_seen(tweet_data.id)

                    logger.info(
                        f"New tweet from @{tweet_data.author_handle}: {tweet_data.id} "
                        f"[source: {tweet_data.source_type.value}]"
                    )

                    # Process with existing workflow
                    await self._process_new_tweet_data(tweet_data)

                    # Rate limit protection
                    await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")

            # Wait before next cycle
            await asyncio.sleep(check_interval)

    async def _refresh_sources(self) -> None:
        """Refresh sources from database to pick up config changes."""
        try:
            # Clear and rebuild sources
            self._aggregator = TweetAggregator()

            # Load target accounts
            targets = await self.db.get_target_accounts()
            for handle in targets:
                self._aggregator.add_source(TargetAccountSource(handle))

            # Load search queries
            searches = await self.db.get_search_queries()
            for search in searches:
                self._aggregator.add_source(SearchQuerySource(
                    query=search["query"],
                    product=search.get("product", "Latest"),
                ))

            # Home feed
            home_settings = await self.db.get_source_settings("home_feed_following")
            if home_settings.get("enabled"):
                self._aggregator.add_source(HomeFeedSource(feed_type="following"))

            # Reload topics
            topics = await self.db.get_topics()
            self._topic_filter = TopicFilter(topics=topics, min_score=0.5)

            # Sync seen tweets
            self._aggregator.mark_seen_batch(list(self._seen_tweets))

        except Exception as e:
            logger.warning(f"Failed to refresh sources: {e}")

    async def _process_new_tweet_data(self, tweet_data) -> None:
        """
        Process a new tweet from aggregator.

        Args:
            tweet_data: TweetData object from aggregator
        """
        # Delegate to existing processing with raw tweet if available
        if tweet_data.raw_tweet:
            await self._process_new_tweet(tweet_data.raw_tweet, tweet_data.author_handle)
        else:
            # Fallback for when raw tweet is not available
            logger.warning(f"No raw tweet for {tweet_data.id}, skipping")

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
            # 0. Check if already processed (DB check)
            # This is critical to avoid duplicate AI calls and DB entries
            if await self.db.check_target_tweet_exists(tweet.id):
                logger.info(f"Skipping already processed tweet {tweet.id}")
                return

            # 1. Gatekeeper Filter (evaluate relevance before generating reply)
            if self._filter_engine and self._filter_engine.enabled:
                filter_result = await self._filter_engine.analyze_tweet(
                    tweet_id=tweet.id,
                    content=tweet.text,
                    author=author,
                )
                if not self._filter_engine.is_interesting(filter_result):
                    logger.info(
                        f"Gatekeeper rejected tweet {tweet.id}: {filter_result.reason} "
                        f"(score={filter_result.score})"
                    )
                    return  # Skip this tweet

            # 2. Generate AI reply with circuit breaker protection
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
                    await self.alerts.error(
                        "circuit_breaker_open",
                        "AI service circuit breaker opened",
                        service="ai",
                        error=str(e)
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
            if self.alerts:
                await self.alerts.error(
                    "tweet_processing_failed",
                    f"Failed to process tweet {tweet.id}",
                    author=author,
                    error=str(e)
                )

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
        asyncio.create_task(bot.stop("Signal received (SIGINT/SIGTERM)"))

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
