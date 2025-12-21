"""
Cookie extraction providers registry.

This module provides a unified interface for cookie extraction
using multiple browser automation libraries with automatic fallback.
"""

from typing import List, Type, Optional
import logging

from ..base import BaseCookieProvider

logger = logging.getLogger(__name__)

# Provider order (first = highest priority)
# This can be customized by modifying this list
PROVIDER_ORDER: List[str] = ["nodriver", "undetected", "playwright", "drissionpage"]


def get_available_providers() -> List[Type[BaseCookieProvider]]:
    """
    Get all available providers in priority order.
    
    Returns:
        List of provider classes that have their dependencies installed.
    """
    available = []
    
    for provider_name in PROVIDER_ORDER:
        try:
            if provider_name == "nodriver":
                from .nodriver import NodriverProvider
                if NodriverProvider.is_available():
                    available.append(NodriverProvider)
                    
            elif provider_name == "undetected":
                from .undetected import UndetectedProvider
                if UndetectedProvider.is_available():
                    available.append(UndetectedProvider)
                    
            elif provider_name == "playwright":
                from .playwright import PlaywrightProvider
                if PlaywrightProvider.is_available():
                    available.append(PlaywrightProvider)
            
            elif provider_name == "drissionpage":
                from .drissionpage import DrissionPageProvider
                if DrissionPageProvider.is_available():
                    available.append(DrissionPageProvider)
                    
        except ImportError as e:
            logger.debug(f"Provider {provider_name} not available: {e}")
            continue
    
    return available


def get_provider(name: Optional[str] = None) -> Optional[Type[BaseCookieProvider]]:
    """
    Get a specific provider by name, or the best available one.
    
    Args:
        name: Provider name ("nodriver", "undetected", "playwright", "drissionpage").
              If None, returns the highest priority available provider.
              
    Returns:
        Provider class or None if not available.
    """
    if name:
        try:
            if name == "nodriver":
                from .nodriver import NodriverProvider
                return NodriverProvider if NodriverProvider.is_available() else None
            elif name == "undetected":
                from .undetected import UndetectedProvider
                return UndetectedProvider if UndetectedProvider.is_available() else None
            elif name == "playwright":
                from .playwright import PlaywrightProvider
                return PlaywrightProvider if PlaywrightProvider.is_available() else None
            elif name == "drissionpage":
                from .drissionpage import DrissionPageProvider
                return DrissionPageProvider if DrissionPageProvider.is_available() else None
        except ImportError:
            return None
    else:
        providers = get_available_providers()
        return providers[0] if providers else None


__all__ = [
    "PROVIDER_ORDER",
    "get_available_providers",
    "get_provider",
]
