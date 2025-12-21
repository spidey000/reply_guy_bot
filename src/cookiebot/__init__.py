"""
CookieBot - Cloudflare-bypassing cookie retriever for Twitter/X.

This module provides a secure and stealthy way to obtain session cookies
using multiple browser automation libraries with automatic fallback:
1. nodriver (best anti-detection, no WebDriver)
2. undetected-chromedriver (Selenium with patches)
3. playwright (reliable fallback with stealth)
"""

from .manager import CookieBot, get_provider_status
from .base import BaseCookieProvider, normalize_cookies
from .providers import get_available_providers, get_provider, PROVIDER_ORDER

__all__ = [
    "CookieBot",
    "BaseCookieProvider",
    "normalize_cookies",
    "get_available_providers",
    "get_provider",
    "get_provider_status",
    "PROVIDER_ORDER",
]

