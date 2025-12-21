"""
Nodriver provider for cookie extraction.

This is the primary (best) provider for anti-detection.
Uses nodriver which communicates directly with the browser via CDP
without Selenium or WebDriver binaries.

Features:
- No WebDriver detection (navigator.webdriver is naturally undefined)
- Built-in Cloudflare bypass with tab.cf_verify()
- Fully async
- Direct CDP communication for better stealth

CRITICAL NOTES FOR X.COM:
- NEVER use headless mode - X.com detects it instantly
- Use existing Chrome profile for session persistence
- Random delays and human-like behavior are essential
"""

import asyncio
import logging
import random
from pathlib import Path
from typing import Optional, List, Dict, Any

from ...base import BaseCookieProvider

logger = logging.getLogger(__name__)

# User data directory for persistent sessions
USER_DATA_DIR = Path("./browser_data/nodriver")

# Updated user agents (Chrome 131, 2024-2025)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]


class NodriverProvider(BaseCookieProvider):
    """
    Cookie extraction provider using nodriver.
    
    This is the recommended provider as nodriver:
    - Has no Selenium/WebDriver dependency
    - Communicates directly with browser via CDP
    - Has built-in Cloudflare challenge bypass
    - Is fully async and performant
    
    IMPORTANT: NEVER use headless=True for X.com - it's detected instantly.
    """
    
    name = "nodriver"
    
    def __init__(self, headless: bool = False):
        # CRITICAL: Force headless=False for X.com
        # Even if caller requests headless, we ignore it
        super().__init__(headless=False)  # Always False for X.com
        self.browser = None
        self.tab = None
        
        if headless:
            logger.warning("Ignoring headless=True - X.com detects headless mode instantly")
    
    @classmethod
    def is_available(cls) -> bool:
        """Check if nodriver is installed."""
        try:
            import nodriver
            return True
        except ImportError:
            return False
    
    async def start(self) -> None:
        """Start nodriver browser session with optimal anti-detection config."""
        import nodriver as uc
        
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        
        # Use Config object for better control
        config = uc.Config()
        config.headless = False  # CRITICAL: Never headless for X.com
        config.user_data_dir = str(USER_DATA_DIR)
        
        # Add browser args using add_argument method
        config.add_argument("--disable-blink-features=AutomationControlled")
        config.add_argument("--disable-dev-shm-usage")
        config.add_argument("--no-sandbox")
        config.add_argument("--disable-setuid-sandbox")
        config.add_argument("--disable-infobars")
        config.add_argument("--window-size=1920,1080")
        config.add_argument("--start-maximized")
        config.add_argument(f"--user-agent={random.choice(USER_AGENTS)}")
        
        self.browser = await uc.start(config=config)
        
        # Get a page to inject stealth scripts
        self.tab = await self.browser.get("about:blank")
        
        # Inject advanced anti-fingerprinting protections
        await self._inject_stealth_scripts()
        
        self._is_started = True
        logger.info("Nodriver browser started with anti-detection config (headless=False)")
    
    async def _inject_stealth_scripts(self) -> None:
        """Inject advanced anti-fingerprinting scripts."""
        if not self.tab:
            return
        
        try:
            # Use CDP to add script that runs on every new document
            await self.tab.send(
                "Page.addScriptToEvaluateOnNewDocument",
                params={
                    "source": '''
                        // Canvas fingerprint protection
                        const originalGetContext = HTMLCanvasElement.prototype.getContext;
                        HTMLCanvasElement.prototype.getContext = function(type, attributes) {
                            const context = originalGetContext.apply(this, arguments);
                            if (type === '2d') {
                                const originalGetImageData = context.getImageData;
                                context.getImageData = function() {
                                    const imageData = originalGetImageData.apply(this, arguments);
                                    // Add subtle noise to prevent fingerprinting
                                    for (let i = 0; i < imageData.data.length; i += 4) {
                                        imageData.data[i] = imageData.data[i] + (Math.random() * 2 - 1);
                                    }
                                    return imageData;
                                };
                            }
                            return context;
                        };
                        
                        // WebGL fingerprint protection
                        const getParameterProxyHandler = {
                            apply: function(target, thisArg, args) {
                                const param = args[0];
                                const result = target.apply(thisArg, args);
                                // Mask common fingerprinting parameters
                                if (param === 37445) return 'Intel Inc.';  // UNMASKED_VENDOR_WEBGL
                                if (param === 37446) return 'Intel Iris OpenGL Engine';  // UNMASKED_RENDERER_WEBGL
                                return result;
                            }
                        };
                        
                        // Protect navigator properties
                        Object.defineProperty(navigator, 'hardwareConcurrency', {
                            get: () => 8
                        });
                        
                        Object.defineProperty(navigator, 'deviceMemory', {
                            get: () => 8
                        });
                        
                        // Fake permissions API
                        const originalQuery = Permissions.prototype.query;
                        Permissions.prototype.query = function(parameters) {
                            if (parameters.name === 'notifications') {
                                return Promise.resolve({ state: Notification.permission });
                            }
                            return originalQuery.apply(this, arguments);
                        };
                    '''
                }
            )
            logger.debug("Advanced stealth scripts injected")
        except Exception as e:
            logger.debug(f"Could not inject stealth scripts: {e}")
    
    async def close(self) -> None:
        """Close browser session."""
        if self.browser:
            try:
                self.browser.stop()
            except Exception as e:
                logger.debug(f"Error closing nodriver browser: {e}")
        self._is_started = False
        logger.info("Nodriver browser closed")
    
    async def _human_delay(self, min_ms: int = 500, max_ms: int = 2000) -> None:
        """Add random human-like delay."""
        delay = random.randint(min_ms, max_ms) / 1000
        await asyncio.sleep(delay)
    
    async def _random_scroll(self) -> None:
        """Simulate human-like random scrolling."""
        if not self.tab:
            return
        try:
            scroll_height = random.randint(200, 600)
            await self.tab.evaluate(f"window.scrollTo(0, {scroll_height})")
            await self._human_delay(500, 1500)
        except Exception:
            pass
    
    async def _simulate_mouse_movement(self) -> None:
        """Simulate mouse movement to appear more human."""
        if not self.tab:
            return
        try:
            await self.tab.evaluate("""
                var event = new MouseEvent('mousemove', {
                    'view': window,
                    'bubbles': true,
                    'cancelable': true,
                    'clientX': Math.random() * window.innerWidth,
                    'clientY': Math.random() * window.innerHeight
                });
                document.dispatchEvent(event);
            """)
        except Exception:
            pass
    
    async def _type_like_human(self, element, text: str) -> None:
        """Type text with human-like speed and occasional pauses."""
        await element.click()
        await self._human_delay(300, 700)
        
        for char in text:
            await element.send_keys(char)
            # Random typing speed (50-180ms per character)
            await asyncio.sleep(random.randint(50, 180) / 1000)
            # 15% chance of small pause (thinking)
            if random.random() < 0.15:
                await self._human_delay(200, 500)
    
    async def login_twitter(
        self,
        username: str,
        password: str,
        email: Optional[str] = None
    ) -> bool:
        """
        Perform login flow on Twitter/X using nodriver.
        
        Flow: Navigate -> Username -> Next -> (verification) -> Password -> Login
        
        Uses human-like behavior simulation to avoid detection.
        """
        if not self.browser:
            logger.error("Browser not started")
            return False
        
        try:
            logger.info("Navigating to X.com login...")
            self.tab = await self.browser.get("https://x.com/i/flow/login")
            
            # Initial wait with random variation
            await self._human_delay(3000, 6000)
            
            # Simulate initial human behavior
            await self._simulate_mouse_movement()
            await self._random_scroll()
            
            # Try to bypass any Cloudflare challenge
            try:
                await self.tab.cf_verify()
                logger.info("Passed Cloudflare check")
            except Exception:
                pass  # No CF challenge or cf_verify not available
            
            # Step 1: Enter username
            logger.info("Entering username...")
            username_input = await self.tab.select("input[autocomplete='username']", timeout=15)
            if not username_input:
                logger.error("Could not find username input")
                return False
            
            await self._simulate_mouse_movement()
            await self._type_like_human(username_input, username)
            await self._human_delay(800, 2000)
            
            # Step 2: Click Next button
            logger.info("Clicking Next button...")
            next_btn = await self.tab.find("Next", timeout=5)
            if next_btn:
                await self._simulate_mouse_movement()
                await next_btn.click()
                await self._human_delay(2500, 5000)
            
            # Step 3: Check for verification prompt (email/phone)
            logger.info("Checking for verification prompt...")
            try:
                verification_input = await self.tab.select(
                    "input[data-testid='ocfEnterTextTextInput']",
                    timeout=3
                )
                if verification_input and email:
                    logger.info("Verification required. Entering email...")
                    await self._type_like_human(verification_input, email)
                    await self._human_delay(800, 1800)
                    
                    next_btn = await self.tab.find("Next", timeout=5)
                    if next_btn:
                        await next_btn.click()
                        await self._human_delay(2500, 5000)
            except Exception:
                pass  # No verification needed
            
            # Step 4: Enter password
            logger.info("Entering password...")
            password_input = await self.tab.select("input[name='password']", timeout=20)
            if not password_input:
                password_input = await self.tab.select("input[type='password']", timeout=5)
            
            if not password_input:
                logger.error("Could not find password input")
                await self.tab.save_screenshot("nodriver_no_password.png")
                return False
            
            await self._simulate_mouse_movement()
            await self._type_like_human(password_input, password)
            await self._human_delay(800, 2000)
            
            # Step 5: Click Log in button
            logger.info("Clicking Log in button...")
            login_btn = await self.tab.select(
                "button[data-testid='LoginForm_Login_Button']",
                timeout=5
            )
            if not login_btn:
                login_btn = await self.tab.find("Log in", timeout=5)
            
            if login_btn:
                await self._simulate_mouse_movement()
                await login_btn.click()
                await self._human_delay(4000, 7000)
            
            # Step 6: Verify login success
            logger.info("Verifying login success...")
            try:
                home_link = await self.tab.select(
                    "[data-testid='AppTabBar_Home_Link']",
                    timeout=15
                )
                if home_link:
                    logger.info("Login successful!")
                    return True
            except Exception:
                pass
            
            # Check URL as fallback
            if self.tab and "/home" in str(self.tab.url):
                logger.info("Login successful (URL check)")
                return True
            
            logger.error("Could not confirm login success")
            await self.tab.save_screenshot("nodriver_login_failure.png")
            return False
            
        except Exception as e:
            logger.error(f"Login failed: {e}")
            if self.tab:
                try:
                    await self.tab.save_screenshot("nodriver_error.png")
                except Exception:
                    pass
            return False
    
    async def get_cookies(self) -> List[Dict[str, Any]]:
        """Get cookies from the current session."""
        if not self.tab:
            return []
        
        try:
            cookies = await self.tab.get_cookies()
            # Convert to standard format if needed
            return [
                {
                    "name": c.get("name"),
                    "value": c.get("value"),
                    "domain": c.get("domain"),
                    "path": c.get("path", "/"),
                    "expires": c.get("expires"),
                    "httpOnly": c.get("httpOnly", False),
                    "secure": c.get("secure", False),
                    "sameSite": c.get("sameSite", "Lax"),
                }
                for c in cookies
            ]
        except Exception as e:
            logger.error(f"Failed to get cookies: {e}")
            return []
