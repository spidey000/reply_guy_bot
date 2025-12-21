# Reply Guy Bot

Personal Twitter/X reply automation tool with security-first design.

## Features

- **Multi-Source Discovery**: Discover tweets from target accounts, search queries, and home feed
- **Topic Filtering**: AI-powered and keyword-based relevance scoring
- **Gatekeeper Filter**: LLM-powered pre-filtering to evaluate tweet relevance before generating replies
- **Ghost Delegate**: Secure credential protection using a dummy account for API operations
- **CookieBot**: Multi-provider browser automation with 4 fallback strategies
- **Cookie Encryption**: Session cookies encrypted at rest with Fernet (AES-128-CBC)
- **Burst Mode**: Anti-detection scheduling with randomized delays and quiet hours
- **Rate Limiting**: Sliding window rate limiter (15/hour, 50/day defaults)
- **AI Replies**: OpenAI-compatible API (supports OpenRouter, Ollama, LMStudio)
- **Telegram Approval**: Review and approve replies before posting
- **Dual Database**: Supabase (production) with SQLite fallback (local/testing)
- **Circuit Breakers**: Automatic failure isolation for external services
- **Session Health Monitoring**: Proactive session validation and auto-refresh

## Implementation Status

| Component | Status | Description |
|-----------|--------|-------------|
| Ghost Delegate | ✅ Done | Secure credential protection via dummy account |
| CookieBot | ✅ Done | 4-provider browser automation with fallback |
| Cookie Encryption | ✅ Done | Fernet (AES-128-CBC) encryption at rest |
| Burst Mode | ✅ Done | Anti-detection scheduling (15-120 min delays, quiet hours) |
| Rate Limiting | ✅ Done | Sliding window (15/hour, 50/day) |
| AI Client | ✅ Done | OpenAI-compatible (supports OpenRouter, Ollama, LMStudio) |
| Database | ✅ Done | Supabase + SQLite fallback |
| Telegram | ✅ Done | Approval workflow with inline buttons |
| Background Worker | ✅ Done | Async publication loop |
| Circuit Breakers | ✅ Done | Failure isolation for Twitter/AI/Database |
| Session Health | ✅ Done | Proactive monitoring with auto-refresh |
| **Gatekeeper Filter** | ✅ Done | LLM-powered tweet relevance pre-filtering |
| **Main Orchestrator** | ✅ Done | Full integration with startup validation |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    SECURITY LAYER                           │
│                   (Ghost Delegate)                          │
├─────────────────────────────────────────────────────────────┤
│  • Main account password NEVER stored                       │
│  • Dummy account handles risky operations                   │
│  • Context switch only at publish time                      │
└─────────────────────────────────────────────────────────────┘
                           +
┌─────────────────────────────────────────────────────────────┐
│                  GATEKEEPER LAYER                           │
│               (LLM-Powered Pre-Filter)                      │
├─────────────────────────────────────────────────────────────┤
│  • Evaluates tweet relevance before reply generation        │
│  • Saves API costs by filtering irrelevant content          │
│  • Configurable model, temperature, and score threshold     │
└─────────────────────────────────────────────────────────────┘
                           +
┌─────────────────────────────────────────────────────────────┐
│                  ANTI-DETECTION LAYER                       │
│                     (Burst Mode)                            │
├─────────────────────────────────────────────────────────────┤
│  • Random delay 15-120 min between approval and post        │
│  • Quiet hours (00:00-07:00 by default)                     │
│  • Timestamp jitter (never exact hours)                     │
└─────────────────────────────────────────────────────────────┘
                           +
┌─────────────────────────────────────────────────────────────┐
│                    COOKIE LAYER                             │
│                    (CookieBot)                              │
├─────────────────────────────────────────────────────────────┤
│  • 4-provider fallback: nodriver → undetected → playwright  │
│  • Advanced anti-fingerprinting (Canvas, WebGL, Audio)      │
│  • Manual import fallback for X.com anti-bot measures       │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# Setup
cp .env.example .env
# Edit .env with your credentials

# Install dependencies
pip install -r requirements.txt

# First time: Extract cookies from X.com (choose one method)
python scripts/manual_login.py
# OR manually add cookies to cookies.json

# Run
python -m src.bot
```

## Cookie Setup (Required)

The bot needs X.com session cookies. Choose one method:

### Option 1: Manual Login Script (Recommended)
```bash
python scripts/manual_login.py
```
Opens a browser for you to login manually, then automatically extracts and saves cookies.

### Option 2: Manual Cookie Extraction
1. Login to X.com in Chrome
2. Open DevTools (F12) → Application → Cookies → x.com
3. Copy `auth_token` and `ct0` values
4. Create `cookies.json`:
```json
[
  {"name": "auth_token", "value": "YOUR_AUTH_TOKEN"},
  {"name": "ct0", "value": "YOUR_CT0_TOKEN"}
]
```

### Option 3: Environment Variables (Docker)
Set `X_AUTH_TOKEN` and `X_CT0` in your `.env` or Docker environment:
```env
X_AUTH_TOKEN=your_auth_token_value
X_CT0=your_ct0_value
```
These take priority over `cookies.json` if set.

### CookieBot Providers
The bot includes 4 browser automation providers with automatic fallback:

| Provider | Priority | Description |
|----------|----------|-------------|
| **nodriver** | 1st | CDP-based, best anti-detection |
| **undetected-chromedriver** | 2nd | Patched Selenium with stealth |
| **playwright** | 3rd | Chromium with stealth patches |
| **drissionpage** | 4th | No WebDriver dependency |

Each provider includes:
- Canvas fingerprint protection
- WebGL vendor/renderer masking
- Navigator property spoofing
- Human-like typing and mouse movement
- Random scrolling and delays

> ⚠️ **Note**: X.com actively blocks automated logins. Manual cookie import is recommended for reliability.

## Database

The bot supports dual database backends:

### Supabase (Production)
Set `SUPABASE_URL` and `SUPABASE_KEY` in `.env`. Required tables:
- `tweet_queue` - Pending and posted tweets
- `target_accounts` - Accounts to monitor
- `search_queries` - Keyword-based tweet discovery
- `topics` - Relevance filter keywords
- `source_settings` - Source configuration (e.g., home feed)
- `failed_tweets` - Dead letter queue
- `login_history` - Login tracking

See `supabase_schema.sql` for table definitions.

### SQLite (Fallback/Local)
Automatically used when Supabase is unavailable. Creates `reply_bot.db` locally with identical schema. Perfect for:
- Local development
- Testing
- Offline operation
- When Supabase is paused/unavailable

## Docker

```bash
docker-compose up -d
```

## Configuration

See `.env.example` for all options:

| Category | Variables |
|----------|-----------|
| **Ghost Delegate** | `DUMMY_USERNAME`, `DUMMY_EMAIL`, `DUMMY_PASSWORD`, `MAIN_ACCOUNT_HANDLE` |
| **AI Provider** | `AI_API_KEY`, `AI_BASE_URL`, `AI_MODEL`, `AI_FALLBACK_MODELS` |
| **Telegram** | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| **Database** | `SUPABASE_URL`, `SUPABASE_KEY` |
| **Burst Mode** | `BURST_MODE_ENABLED`, `QUIET_HOURS_START/END`, `MIN/MAX_DELAY_MINUTES` |
| **Gatekeeper Filter** | `FILTER_ENABLED`, `FILTER_TEMPERATURE`, `FILTER_MIN_SCORE` |
| **Security** | `COOKIE_ENCRYPTION_KEY` (generate with `Fernet.generate_key()`) |

### Gatekeeper Filter

The Gatekeeper is an LLM-powered pre-filter that evaluates tweets before generating replies. This:
- **Saves API costs** by skipping irrelevant tweets
- **Improves quality** by filtering spam, toxicity, and low-effort content
- **Is configurable** via Telegram settings (`/settings` → Gatekeeper Filter)

| Setting | Default | Description |
|---------|---------|-------------|
| `filter_enabled` | `true` | Enable/disable the filter |
| `filter_model` | `""` | AI model for filter (empty = use reply model) |
| `filter_temperature` | `0.2` | AI temperature (low = consistent) |
| `filter_min_score` | `5` | Minimum relevance score (1-10) |

Prompts are configured in `config/prompts.py` (`GATEKEEPER_SYSTEM_PROMPT`).

## Development

```bash
pip install -r requirements-dev.txt
pytest tests/
ruff check src/
```

### Project Structure
```
src/
├── bot.py              # Main orchestrator
├── x_delegate.py       # Ghost Delegate (secure Twitter operations)
├── database.py         # Supabase client
├── database_sqlite.py  # SQLite fallback
├── telegram_client.py  # Telegram approval flow
├── ai_client.py        # AI reply generation
├── topic_filter.py     # Keyword-based relevance scoring
├── tweet_filter.py     # Gatekeeper (LLM-powered pre-filter)
├── rate_limiter.py     # Rate limiting
├── circuit_breaker.py  # Failure isolation
├── scheduler.py        # Burst Mode scheduling
├── background_worker.py # Async publication
├── tweet_sources/      # Multi-source discovery
│   ├── base.py         # TweetData + BaseTweetSource
│   ├── aggregator.py   # Source aggregation
│   ├── target_account.py
│   ├── search_query.py
│   └── home_feed.py
└── cookiebot/          # Cookie extraction system
    ├── manager.py      # CookieBot orchestrator
    ├── base.py         # Provider base class
    └── providers/      # 4 automation providers
```

## Telegram Commands

### Status & Info
| Command | Description |
|---------|-------------|
| `/start` | Show help with all available commands |
| `/queue` | View pending tweets awaiting approval |
| `/stats` | View bot statistics |
| `/settings` | Configure bot settings |
| `/sources` | Show all tweet sources and their status |

### Target Accounts
| Command | Description |
|---------|-------------|
| `/add_target @user` | Add account to monitor |
| `/remove_target @user` | Stop monitoring account |
| `/list_targets` | Show all monitored accounts |

### Search Queries
| Command | Description |
|---------|-------------|
| `/add_search <query>` | Add keyword search (e.g., `/add_search AI startup`) |
| `/remove_search <query>` | Remove a search query |
| `/list_searches` | Show active search queries |

### Topic Filters
| Command | Description |
|---------|-------------|
| `/add_topic <keyword>` | Add relevance filter (e.g., `/add_topic machine learning`) |
| `/remove_topic <keyword>` | Remove topic filter |
| `/list_topics` | Show active topic filters |

### Home Feed
| Command | Description |
|---------|-------------|
| `/enable_home_feed` | Enable timeline discovery |
| `/disable_home_feed` | Disable timeline discovery |

### Approval Workflow

1. Bot discovers tweets from multiple sources (targets, search, home feed)
2. Topics filter for relevant content
3. AI generates contextual reply
4. Telegram sends approval request with **[Approve]** **[Edit]** **[Reject]**
5. Approved tweets scheduled with random delay (Burst Mode)
6. Background worker publishes via Ghost Delegate

## Scripts

| Script | Description |
|--------|-------------|
| `scripts/manual_login.py` | Open browser for manual X.com login and cookie extraction |
| `scripts/verify_system.py` | Verify all system components are working |
| `scripts/healthcheck.py` | Health check for Docker/monitoring |

## Security Features

- **Credential Isolation**: Main account password never stored
- **Cookie Encryption**: AES-128-CBC encryption at rest
- **Audit Logging**: All operations logged to `ghost_delegate_audit.log`
- **Rate Limiting**: Prevents account suspension from overuse
- **Circuit Breakers**: Automatic failure isolation
- **Kill Switch**: Emergency stop for all operations

## Documentation

| Document | Description |
|----------|-------------|
| [Technical Analysis](docs/ANALISIS_TECNICO.md) | Ghost Delegate security model, Burst Mode algorithms |
| [Resilience Guide](docs/RESILIENCE_GUIDE.md) | Circuit breakers, error handling, recovery |

## License

MIT
