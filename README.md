# Reply Guy Bot

Personal Twitter/X reply automation tool with security-first design.

## Features

- **Ghost Delegate**: Secure credential protection using a dummy account for API operations
- **Burst Mode**: Anti-detection scheduling with randomized delays and quiet hours
- **AI Replies**: OpenAI-compatible API (supports OpenAI, Ollama, LMStudio, Groq)
- **Telegram Approval**: Review and approve replies before posting
- **Supabase Queue**: Persistent queue for scheduled tweets

## Implementation Status

| Component | Status | Description |
|-----------|--------|-------------|
| Ghost Delegate | ✅ Done | Secure credential protection via dummy account |
| Burst Mode | ✅ Done | Anti-detection scheduling (15-120 min delays, quiet hours) |
| AI Client | ✅ Done | OpenAI-compatible (supports Ollama, LMStudio, Groq) |
| Database | ✅ Done | Supabase integration for queue persistence |
| Telegram | ✅ Done | Approval workflow with inline buttons |
| Background Worker | ✅ Done | Async publication loop |
| **Main Orchestrator** | ✅ Done | Full integration (429 lines) |

**MVP Status:** Complete (10/20 tasks). See `TODO_TASKS.json` for detailed breakdown.

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

- [Technical Analysis](docs/ANALISIS_TECNICO.md) - Architecture decisions, implementation status, and Burst Mode specification
