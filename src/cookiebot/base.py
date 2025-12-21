"""
Base provider interface for cookie extraction.

All cookie extraction providers must implement this interface.
"""

import abc
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def normalize_cookies(cookies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalize cookies to a standard format across all providers.
    
    This ensures consistent cookie structure regardless of which provider
    was used to extract them. Compatible with twikit's expected format.
    
    Args:
        cookies: Raw cookies from any provider.
        
    Returns:
        List of normalized cookie dicts with consistent keys.
    """
    normalized = []
    for c in cookies:
        normalized.append({
            "name": c.get("name"),
            "value": c.get("value"),
            "domain": c.get("domain", ".x.com"),
            "path": c.get("path", "/"),
            "expires": c.get("expires") or c.get("expiry"),  # Handle both keys
            "httpOnly": c.get("httpOnly", False),
            "secure": c.get("secure", True),
            "sameSite": c.get("sameSite", "Lax"),
        })
    return normalized


class BaseCookieProvider(abc.ABC):
    """
    Abstract base class for cookie extraction providers.
    
    Each provider implements browser automation using a specific library
    (nodriver, undetected-chromedriver, playwright) to log into X.com
    and extract session cookies.
    """
    
    # Provider name for logging and identification
    name: str = "base"
    
    def __init__(self, headless: bool = False):
        """
        Initialize the provider.
        
        Args:
            headless: Whether to run browser in headless mode.
                      Note: headless mode has higher detection risk.
        """
        self.headless = headless
        self._is_started = False
    
    @classmethod
    def is_available(cls) -> bool:
        """
        Check if this provider's dependencies are installed.
        
        Returns:
            True if the provider can be used.
        """
        return False
    
    @abc.abstractmethod
    async def start(self) -> None:
        """
        Initialize and start the browser session.
        
        Should apply all anti-detection measures.
        """
        pass
    
    @abc.abstractmethod
    async def close(self) -> None:
        """
        Close the browser session and cleanup resources.
        """
        pass
    
    @abc.abstractmethod
    async def login_twitter(
        self,
        username: str,
        password: str,
        email: Optional[str] = None
    ) -> bool:
        """
        Perform login flow on Twitter/X.
        
        Args:
            username: Twitter username or email.
            password: Account password.
            email: Email for verification if prompted.
            
        Returns:
            True if login was successful.
        """
        pass
    
    @abc.abstractmethod
    async def get_cookies(self) -> List[Dict[str, Any]]:
        """
        Get all cookies from the current browser session.
        
        Returns:
            List of cookie dictionaries.
        """
        pass
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
