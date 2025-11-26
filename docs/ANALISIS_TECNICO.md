# Technical Analysis - Reply Guy Bot

**Version:** 1.0
**Date:** November 2025
**Perspective:** Software Architecture Analysis

---

## Overview

Reply Guy Bot implements two core technical innovations for automated Twitter engagement:

1. **Ghost Delegate**: Security pattern protecting main account credentials through X's delegation API
2. **Burst Mode**: Anti-detection system using humanized timing patterns

This document provides technical analysis of these systems and the overall architecture.

---

## 1. Ghost Delegate Architecture

### Security Model

The Ghost Delegate pattern solves a critical security problem: how to automate Twitter posting without exposing the main account's credentials.

```
┌─────────────────────────────────────────────────────────────┐
│                    CREDENTIAL ISOLATION                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────┐        ┌─────────────────┐            │
│  │  Dummy Account  │        │   Main Account  │            │
│  ├─────────────────┤        ├─────────────────┤            │
│  │ Credentials:    │        │ Credentials:    │            │
│  │ - Username  ✓   │        │ - Username  ✓   │            │
│  │ - Email     ✓   │        │ - Password  ✗   │            │
│  │ - Password  ✓   │        │   (never stored)│            │
│  └────────┬────────┘        └────────▲────────┘            │
│           │                          │                      │
│           │    X Delegation API      │                      │
│           └──────────────────────────┘                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Implementation Details

**Authentication Flow:**
1. Bot authenticates using dummy account credentials
2. Twikit client stores session cookies for persistence
3. For posting, `set_delegate_account(main_user.id)` switches context
4. After posting, `set_delegate_account(None)` reverts to dummy

**Cookie Persistence & Encryption:**
- Sessions are stored in `cookies.json` for faster startup
- Cookies are encrypted at rest using Fernet (AES-128-CBC) symmetric encryption
- On startup, existing cookies are decrypted and validated before fresh login
- Invalid/expired cookies trigger automatic re-authentication
- Automatic migration: plaintext cookies are encrypted on first save after upgrade

**Error Handling Strategy:**
| Error Type | Response |
|------------|----------|
| `TooManyRequests` | Log and retry later (rate limiting) |
| `Unauthorized` | Clear auth state, require fresh login |
| `Forbidden` | Check delegation settings |
| `BadRequest` | Check for duplicate content |

### Security Benefits

1. **Credential Protection**: Main account password is never stored or transmitted by the bot
2. **Risk Isolation**: If dummy account is banned, main account remains intact
3. **Instant Revocation**: Delegation can be revoked immediately via X.com settings
4. **Recovery Time**: New dummy account can be created in ~5 minutes

### Threat Model

| Threat | Mitigation |
|--------|------------|
| Credential theft | Main password never stored |
| Account ban | Only dummy exposed to automation risk |
| Session hijacking | Cookies encrypted at rest with Fernet |
| Cookie theft | AES-128-CBC encryption protects session tokens |
| Rate limit abuse | Delegated actions count against dummy |

### Login Cooldown System (Ban Prevention)

Twitter/X may ban accounts that perform frequent fresh logins without using cookies. The Login Cooldown System prevents this by:

1. **Tracking all login attempts** in the `login_history` database table
2. **Enforcing a 3-hour cooldown** between fresh credential-based logins
3. **Automatic waiting** when cooldown is active

**Login Flow with Cooldown:**
```
Login Request
      │
      ▼
┌─────────────────┐
│ Cookies exist?  │──No──┐
└────────┬────────┘      │
         │Yes            │
         ▼               ▼
┌─────────────────┐  ┌─────────────────┐
│ Load & validate │  │ Check cooldown  │
│ cookies         │  │ (last login)    │
└────────┬────────┘  └────────┬────────┘
         │                    │
    ┌────┴────┐          ┌────┴────┐
  Valid    Invalid    Active   Expired
    │         │         │         │
    │         └────┬────┘         │
    │              │              │
    ▼              ▼              ▼
 Return     Wait for         Fresh Login
 Success    cooldown         (credentials)
            to expire            │
                                ▼
                           Save cookies
                           Record to DB
```

**Configuration:**
| Parameter | Default | Purpose |
|-----------|---------|---------|
| `LOGIN_COOLDOWN_HOURS` | `3` | Hours between fresh logins |
| `LOGIN_COOLDOWN_ENABLED` | `true` | Enable/disable cooldown |

**Database Table: `login_history`**
| Column | Type | Purpose |
|--------|------|---------|
| `account_type` | TEXT | 'dummy' or 'main' |
| `login_type` | TEXT | 'fresh' or 'cookie_restore' |
| `success` | BOOLEAN | Login outcome |
| `error_message` | TEXT | Error details if failed |
| `attempted_at` | TIMESTAMP | When attempt occurred |
| `cookies_existed` | BOOLEAN | Did cookies exist? |
| `cookies_valid` | BOOLEAN | Were cookies valid? |

---

## 2. Burst Mode Anti-Detection System

### Algorithm Overview

Burst Mode prevents bot detection by simulating human posting patterns. Instead of immediate responses (a red flag for automation), tweets are scheduled with randomized delays.

```
Traditional Bot:
Tweet detected → 3 seconds → Reply posted
                     ↑
        Unnatural latency = Detection risk

Burst Mode:
Tweet detected → Approval → [15-120 min random delay] → Reply posted
                                      ↑
                    Human-like pattern = Lower detection risk
```

### Timing Strategy

**1. Random Delay (15-120 minutes)**
```python
delay = random.randint(MIN_DELAY, MAX_DELAY)  # Default: 15-120
scheduled = now + timedelta(minutes=delay)
```

**2. Quiet Hours Enforcement (00:00-07:00)**
```python
if QUIET_START <= hour < QUIET_END:
    scheduled = move_to_end_of_quiet_hours()
```

**3. Timestamp Jitter (0-300 seconds)**
```python
jitter = random.randint(0, 300)
scheduled += timedelta(seconds=jitter)
```

### Pattern Generation

The combination of these strategies produces a human-like activity pattern:

```
00:00 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  Quiet zone (no activity)
07:00 ▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░  Morning start
13:00 ░░▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░  Lunch activity
18:00 ░░▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░  Evening activity
23:00 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  Wind down
```

### Anti-Detection Matrix

| Bot Signal | Detection Risk | Burst Mode Mitigation |
|------------|---------------|----------------------|
| Instant replies | High | 15-120 min random delay |
| 24/7 activity | High | Quiet hours (00:00-07:00) |
| Exact timestamps | Medium | Jitter (0-300s randomization) |
| Regular intervals | Medium | Variable delays |
| Immediate correlation | High | Temporal decoupling |

### Configuration Parameters

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `BURST_MODE_ENABLED` | `true` | Enable/disable scheduler |
| `QUIET_HOURS_START` | `0` | Start of quiet period (hour) |
| `QUIET_HOURS_END` | `7` | End of quiet period (hour) |
| `MIN_DELAY_MINUTES` | `15` | Minimum delay before posting |
| `MAX_DELAY_MINUTES` | `120` | Maximum delay before posting |
| `SCHEDULER_CHECK_INTERVAL` | `60` | Seconds between queue checks |

### Limitations

1. **Not Real-Time**: Urgent replies require manual posting
2. **Queue Accumulation**: High approval rate can build up queue
3. **Static Quiet Hours**: Not adaptive to user timezone changes
4. **Single Pattern**: Same pattern for all target accounts

---

## 3. Asynchronous Architecture

### Event Loop Design

The bot uses Python's `asyncio` for concurrent operations:

```
┌─────────────────────────────────────────────────────────────┐
│                     MAIN EVENT LOOP                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────┐ │
│  │  Tweet Monitor  │  │ Background      │  │  Telegram   │ │
│  │  (asyncio.Task) │  │ Worker          │  │  Polling    │ │
│  │                 │  │ (asyncio.Task)  │  │  (blocking) │ │
│  │  - Check every  │  │                 │  │             │ │
│  │    5 minutes    │  │ - Check every   │  │ - Handles   │ │
│  │  - Detect new   │  │   60 seconds    │  │   callbacks │ │
│  │    tweets       │  │ - Publish       │  │ - Commands  │ │
│  │  - Generate AI  │  │   scheduled     │  │             │ │
│  │    replies      │  │   tweets        │  │             │ │
│  └─────────────────┘  └─────────────────┘  └─────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Task Coordination

| Component | Runs As | Interval | Purpose |
|-----------|---------|----------|---------|
| Tweet Monitor | asyncio.Task | 300s | Detect new tweets |
| Background Worker | asyncio.Task | 60s | Publish scheduled tweets |
| Telegram | Polling | Continuous | User interaction |

### Race Condition Prevention

1. **Seen Tweets Set**: Prevents duplicate processing of same tweet
2. **Database Status**: Atomic status transitions prevent double-posting
3. **Graceful Shutdown**: Signal handlers cancel tasks cleanly

---

## 4. Integration Patterns

### Twikit Integration

The bot uses [Twikit](https://github.com/d60/twikit) for Twitter API access:

- **Authentication**: Email/username/password flow
- **Rate Limiting**: Handled via exception catching
- **Delegation**: `set_delegate_account()` for context switching
- **Session**: Cookie-based persistence

### OpenAI API Compatibility

The AI client implements OpenAI's chat completion interface, enabling:

- Direct OpenAI usage
- Local models via Ollama/LMStudio
- Alternative providers (Groq, Together AI)

**Provider Configuration:**
```
OpenAI:     https://api.openai.com/v1
Ollama:     http://localhost:11434/v1
LMStudio:   http://localhost:1234/v1
```

### Telegram Bot API

Uses `python-telegram-bot` for:

- Inline keyboards for approval flow
- Command handlers for bot control
- Callback queries for button actions

### Supabase Integration

PostgreSQL database via Supabase client:

- **tweet_queue**: Stores pending/approved/posted tweets
- **target_accounts**: Accounts to monitor

---

## 5. Performance Considerations

### Memory Footprint

- Seen tweets set: O(n) where n = processed tweets
- Cookie storage: ~2KB per session
- Queue: Stored in database, not memory

### Rate Limiting

| Service | Limit | Handling |
|---------|-------|----------|
| Twitter | Variable | Exponential backoff via Twikit |
| AI Provider | Model-dependent | Retry on 429 |
| Telegram | 30 msg/sec | Native library handling |
| Supabase | 500 req/sec | Well within limits |

### Scalability Limits

- **Single Instance**: Designed for one main account
- **Database**: Supabase free tier (500MB, 50K rows)
- **AI Calls**: One per detected tweet

---

## 6. Security Features

### Implemented Security Measures

| Feature | Status | Description |
|---------|--------|-------------|
| Ghost Delegate | Implemented | Main account password never stored |
| Cookie Encryption | Implemented | Fernet (AES-128-CBC) encryption at rest |
| Login Cooldown | Implemented | 3-hour cooldown prevents ban from frequent logins |
| Startup Validation | Implemented | Fails fast with clear errors on missing config |
| Audit Logging | Implemented | All delegation ops logged to `ghost_delegate_audit.log` |
| Rate Limiting | Implemented | Sliding window (15/hour, 50/day) |
| Session Health | Implemented | 4-state monitoring with auto-refresh |

### Cookie Encryption Details

```
┌─────────────────────────────────────────────────────────────┐
│                  COOKIE ENCRYPTION FLOW                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  SAVE:                                                      │
│  Twikit cookies → JSON → Fernet.encrypt() → cookies.json   │
│                                                             │
│  LOAD:                                                      │
│  cookies.json → Fernet.decrypt() → JSON → Twikit client    │
│                                                             │
│  Migration: Plaintext detected → auto-encrypt on save      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Configuration:**
| Parameter | Required | Purpose |
|-----------|----------|---------|
| `COOKIE_ENCRYPTION_KEY` | Yes* | 32-byte Fernet key (base64) |

*If not set, cookies save as plaintext with warning.

**Key Generation:**
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Remaining Hardening Opportunities

1. **Secrets Management**: Use vault or KMS for credentials in production
2. **IP Allowlisting**: Restrict bot access by IP in cloud deployments
3. **Metrics & Monitoring**: Add Prometheus metrics for production observability

---

## Conclusion

The Reply Guy Bot implements two complementary protection layers:

1. **Ghost Delegate**: Protects credentials through delegation
2. **Burst Mode**: Protects account through behavioral mimicry

The combination creates a system that is both secure (main account never at direct risk) and stealthy (posting patterns appear human-like).

**Technical Achievements:**
- Clean separation of concerns across modules
- Async-first architecture for efficient I/O
- Provider-agnostic AI integration
- Human-in-the-loop approval workflow

**Known Limitations:**
- Single account only
- No multi-user support
- Static quiet hours
- No adaptive learning

---

## References

- [Twikit Documentation](https://github.com/d60/twikit)
- [X Delegation Settings](https://x.com/settings/delegate)
- [OpenAI API Reference](https://platform.openai.com/docs)
- [Telegram Bot API](https://core.telegram.org/bots/api)
