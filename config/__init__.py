"""
Configuration package for Reply Guy Bot.

Modules:
    settings: Centralized configuration using Pydantic Settings
    prompts: AI system prompts and response templates
"""

from config.settings import settings, create_user_settings, SettingValidator

__all__ = ["settings", "create_user_settings", "SettingValidator"]
