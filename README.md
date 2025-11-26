# Reply Guy Bot

Personal Twitter/X reply automation tool with security-first design.

## Features

- **Ghost Delegate**: Secure credential protection using a dummy account for API operations
- **Cookie Encryption**: Session cookies encrypted at rest with Fernet (AES-128-CBC)
- **Burst Mode**: Anti-detection scheduling with randomized delays and quiet hours
- **Rate Limiting**: Sliding window rate limiter (15/hour, 50/day defaults)
- **AI Replies**: OpenAI-compatible API (supports OpenRouter, Ollama, LMStudio)
- **Telegram Approval**: Review and approve replies before posting
- **Supabase Queue**: Persistent queue for scheduled tweets

## Implementation Status

| Component | Status | Description |
|-----------|--------|-------------|
| Ghost Delegate | ✅ Done | Secure credential protection via dummy account |
| Cookie Encryption | ✅ Done | Fernet (AES-128-CBC) encryption at rest |
| Burst Mode | ✅ Done | Anti-detection scheduling (15-120 min delays, quiet hours) |
| Rate Limiting | ✅ Done | Sliding window (15/hour, 50/day) |
| AI Client | ✅ Done | OpenAI-compatible (supports OpenRouter, Ollama, LMStudio) |
| Database | ✅ Done | Supabase integration for queue persistence |
| Telegram | ✅ Done | Approval workflow with inline buttons |
| Background Worker | ✅ Done | Async publication loop |
| **Main Orchestrator** | ✅ Done | Full integration with startup validation |

**MVP Status:** Complete with security hardening. See `TODO_TASKS.json` for detailed breakdown.

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
│                  ANTI-DETECTION LAYER                       │
│                     (Burst Mode)                            │
├─────────────────────────────────────────────────────────────┤
│  • Random delay 15-120 min between approval and post        │
│  • Quiet hours (00:00-07:00 by default)                     │
│  • Timestamp jitter (never exact hours)                     │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# Setup
cp .env.example .env
# Edit .env with your credentials

# Install dependencies
pip install -r requirements.txt

# Run
python -m src.bot
```

## Docker

```bash
docker-compose up -d
```

## Configuration

See `.env.example` for all options:

- **Ghost Delegate**: `DUMMY_USERNAME`, `DUMMY_EMAIL`, `DUMMY_PASSWORD`, `MAIN_ACCOUNT_HANDLE`
- **AI Provider**: `AI_API_KEY`, `AI_BASE_URL`, `AI_MODEL`
- **Telegram**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- **Supabase**: `SUPABASE_URL`, `SUPABASE_KEY`
- **Burst Mode**: `BURST_MODE_ENABLED`, `QUIET_HOURS_START/END`, `MIN/MAX_DELAY_MINUTES`
- **Security**: `COOKIE_ENCRYPTION_KEY` (generate with `Fernet.generate_key()`)

## Development

```bash
pip install -r requirements-dev.txt
pytest tests/
ruff check src/
```

## Telegram Commands

Once the bot is running, interact via Telegram:

| Command | Description |
|---------|-------------|
| `/start` | Initialize bot and show available commands |
| `/queue` | View pending tweets awaiting approval |
| `/stats` | View bot statistics (pending, posted today, Burst Mode config) |

### Approval Workflow

1. Bot detects new tweet from monitored accounts
2. AI generates contextual reply
3. Telegram sends message with:
   - Original tweet preview
   - AI-generated reply suggestion
   - **[Approve]** **[Edit]** **[Reject]** buttons
4. You approve/reject via Telegram
5. Approved tweets scheduled with 15-120 min random delay (Burst Mode)
6. Background worker publishes at scheduled time via Ghost Delegate

## Documentation

| Document | Description |
|----------|-------------|
| [Product Specification](docs/PRODUCT_SPEC.md) | Complete product reference: file responsibilities, component interactions, feature specs, data model, configuration guide |
| [Technical Analysis](docs/ANALISIS_TECNICO.md) | Deep-dive on Ghost Delegate security model, Burst Mode anti-detection algorithms, async architecture |
