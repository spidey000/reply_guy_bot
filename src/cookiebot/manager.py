"""
CookieBot Manager - Orchestrates cookie extraction with fallback support.

This module manages the lifecycle of Twitter/X cookies using multiple
browser automation providers with automatic fallback.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Type

from cryptography.fernet import Fernet, InvalidToken

from config import settings
from .base import BaseCookieProvider
from .providers import get_available_providers, get_provider, PROVIDER_ORDER

logger = logging.getLogger(__name__)


class CookieBot:
    """
    Manages the lifecycle of Twitter/X cookies.
    
    Uses multiple browser automation providers with automatic fallback:
    1. nodriver (best anti-detection)
    2. undetected-chromedriver (good fallback)
    3. playwright (reliable fallback)
    
    Handles storage, encryption, validation, and renewal via headless browser.
    """
    
    def __init__(
        self,
        cookie_file: Path = Path("cookies.json"),
        preferred_provider: Optional[str] = None
    ):
        """
        Initialize CookieBot.
        
        Args:
            cookie_file: Path to the file where cookies are stored.
            preferred_provider: Force use of a specific provider
                               ("nodriver", "undetected", "playwright").
                               If None, uses automatic fallback order.
        """
        self.cookie_file = cookie_file
        self.preferred_provider = preferred_provider
        self.fernet = self._get_fernet()
        
        # Log available providers
        available = get_available_providers()
        if available:
            logger.info(f"Available cookie providers: {[p.name for p in available]}")
        else:
            logger.warning("No cookie providers available! Install nodriver, undetected-chromedriver, or playwright.")

    def _get_fernet(self) -> Optional[Fernet]:
        """Get Fernet instance for encryption."""
        if not settings.cookie_encryption_key:
            return None
        try:
            return Fernet(settings.cookie_encryption_key.encode())
        except Exception as e:
            logger.error(f"Invalid encryption key: {e}")
            return None

    def load_cookies(self) -> List[Dict[str, Any]]:
        """
        Load cookies from storage.
        
        Returns:
            List of cookie dicts, or empty list if missing/invalid.
        """
        if not self.cookie_file.exists():
            return []

        try:
            # Read file content
            with open(self.cookie_file, 'rb') as f:
                content = f.read()

            # Attempt decryption if configured
            if self.fernet:
                try:
                    plaintext = self.fernet.decrypt(content).decode()
                except InvalidToken:
                    # Fallback for migration (might be plaintext)
                    try:
                        plaintext = content.decode()
                        json.loads(plaintext)  # Validate JSON
                        logger.info("Loaded plaintext cookies (will be encrypted on save)")
                    except:
                        logger.error("Failed to decrypt cookies")
                        return []
            else:
                plaintext = content.decode()

            return json.loads(plaintext)

        except Exception as e:
            logger.error(f"Failed to load cookies: {e}")
            return []

    def save_cookies(self, cookies: List[Dict[str, Any]]) -> bool:
        """
        Save cookies to storage (encrypted if configured).
        
        Args:
            cookies: List of cookie dicts.
            
        Returns:
            True if successful.
        """
        try:
            plaintext = json.dumps(cookies)
            
            if self.fernet:
                data = self.fernet.encrypt(plaintext.encode())
                mode = 'wb'
            else:
                data = plaintext
                mode = 'w'
                
            with open(self.cookie_file, mode) as f:
                f.write(data)
                
            logger.info("Cookies saved successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save cookies: {e}")
            return False

    def _get_providers_to_try(self) -> List[Type[BaseCookieProvider]]:
        """Get list of providers to try, in order."""
        if self.preferred_provider:
            provider = get_provider(self.preferred_provider)
            if provider:
                return [provider]
            else:
                logger.warning(f"Preferred provider '{self.preferred_provider}' not available")
        
        return get_available_providers()

    async def get_fresh_cookies(self, headless: bool = False) -> List[Dict[str, Any]]:
        """
        Launch browser and perform fresh login to get new cookies.
        
        Uses automatic fallback through available providers.
        
        Args:
            headless: Whether to use headless mode.
            
        Returns:
            New cookies list, or empty list if all providers fail.
        """
        providers = self._get_providers_to_try()
        
        if not providers:
            logger.error("No cookie extraction providers available")
            return []
        
        logger.info(f"Starting fresh login flow (providers: {[p.name for p in providers]})...")
        
        for provider_class in providers:
            logger.info(f"Trying provider: {provider_class.name}")
            
            try:
                provider = provider_class(headless=headless)
                
                async with provider:
                    success = await provider.login_twitter(
                        username=settings.dummy_username,
                        password=settings.dummy_password,
                        email=settings.dummy_email
                    )
                    
                    if success:
                        cookies = await provider.get_cookies()
                        if cookies:
                            self.save_cookies(cookies)
                            logger.info(f"Retrieved {len(cookies)} cookies using {provider_class.name}")
                            return cookies
                        else:
                            logger.warning(f"Login succeeded with {provider_class.name} but no cookies found")
                    else:
                        logger.warning(f"Login failed with {provider_class.name}")
                        
            except Exception as e:
                logger.error(f"Provider {provider_class.name} failed with error: {e}")
                continue
        
        logger.error("All providers failed to get cookies")
        return []

    async def get_valid_cookies(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Get valid cookies, refreshing if necessary.
        
        This is the main entry point.
        
        Args:
           force_refresh: Force a new login even if cookies exist.
           
        Returns:
            Valid cookies list.
        """
        if not force_refresh:
            cookies = self.load_cookies()
            if cookies:
                # Basic validation: check if auth_token exists
                if any(c.get('name') == 'auth_token' for c in cookies):
                    return cookies
                logger.info("Cookies loaded but missing auth_token")

        logger.info("Cookies missing or forced refresh, fetching new ones...")
        # Note: Set headless=False if debugging or facing strict bot detection
        return await self.get_fresh_cookies(headless=False)


def get_provider_status() -> Dict[str, bool]:
    """
    Get availability status of all providers.
    
    Returns:
        Dict mapping provider name to availability.
    """
    status = {}
    for name in PROVIDER_ORDER:
        provider = get_provider(name)
        status[name] = provider is not None
    return status
