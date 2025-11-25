"""
Reply Guy Bot - Automated Twitter/X reply bot with human-like behavior.

This bot monitors target Twitter/X accounts and generates contextual replies
using AI, with built-in anti-detection measures.

Architecture:
    Two-layer protection system:
    1. Ghost Delegate: Credential security via account delegation
    2. Burst Mode: Anti-detection via humanized scheduling

Modules:
    bot: Main orchestrator that coordinates all components
    x_delegate: Ghost Delegate - secure credential management
    scheduler: Burst Mode - humanized timing calculations
    background_worker: Async publication loop
    ai_client: OpenAI-compatible AI client for reply generation
    telegram_client: Telegram notifications and approval flow
    database: Supabase client for persistence

Entry Point:
    python -m src.bot
"""

__version__ = "0.1.0"
