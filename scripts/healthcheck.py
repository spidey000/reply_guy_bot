#!/usr/bin/env python3
"""
Docker health check script for Reply Guy Bot.

This script performs actual health verification:
1. Checks if critical modules can be imported
2. Verifies database connectivity
3. Validates AI client configuration

Exit codes:
    0 - Healthy
    1 - Unhealthy

Usage:
    python scripts/healthcheck.py
"""

import sys


def check_health() -> bool:
    """
    Perform health checks on bot components.

    Returns:
        True if all checks pass, False otherwise.
    """
    try:
        # 1. Check critical imports
        from config import settings
        from src.database import Database
        from src.ai_client import AIClient

        # 2. Check required settings are configured
        required = [
            settings.dummy_username1,
            settings.telegram_bot_token,
            settings.supabase_url,
            settings.ai_api_key,
        ]
        if not all(required):
            print("UNHEALTHY: Missing required configuration")
            return False

        # 3. Quick database connectivity check
        try:
            db = Database()
            # Simple query to verify connection
            db.client.table("target_accounts").select("handle").limit(1).execute()
        except Exception as e:
            print(f"UNHEALTHY: Database connection failed - {e}")
            return False

        print("HEALTHY: All checks passed")
        return True

    except ImportError as e:
        print(f"UNHEALTHY: Import error - {e}")
        return False
    except Exception as e:
        print(f"UNHEALTHY: Unexpected error - {e}")
        return False


if __name__ == "__main__":
    healthy = check_health()
    sys.exit(0 if healthy else 1)
