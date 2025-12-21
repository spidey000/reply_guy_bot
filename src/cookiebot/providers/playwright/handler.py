"""
Playwright provider for cookie extraction.

This is the tertiary (fallback) provider using Playwright with stealth patches.
Migrated from the original browser.py implementation.

Features:
- Uses playwright-stealth for anti-detection
- Persistent context for session storage
- Good JavaScript support for complex pages
- Well-documented and widely used
"""

import asyncio
import logging
import random
from pathlib import Path
from typing import Optional, List, Dict, Any

from ...base import BaseCookieProvider

logger = logging.getLogger(__name__)

# User data directory for persistent sessions
USER_DATA_DIR = Path("./browser_data/playwright")

# Updated user agents (Chrome 131, 2024-2025)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]


class PlaywrightProvider(BaseCookieProvider):
    """
    Cookie extraction provider using Playwright with stealth.
    
    This is the fallback provider when nodriver and undetected-chromedriver
    fail. Uses playwright-stealth library for anti-detection.
    """
    
    name = "playwright"
    
    def __init__(self, headless: bool = False):
        # CRITICAL: Force headless=False for X.com
        super().__init__(headless=False)  # Always False for X.com
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        
        if headless:
            logger.warning("Ignoring headless=True - X.com detects headless mode instantly")
    
    @classmethod
    def is_available(cls) -> bool:
        """Check if playwright is installed."""
        try:
            from playwright.async_api import async_playwright
            return True
        except ImportError:
            return False
    
    async def start(self) -> None:
        """Start Playwright browser session with stealth patches."""
        from playwright.async_api import async_playwright
        
        try:
            from playwright_stealth import stealth_async
            has_stealth = True
        except ImportError:
            has_stealth = False
            logger.warning("playwright-stealth not installed, using vanilla Playwright")
        
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        
        self.playwright = await async_playwright().start()
        
        # Browser launch arguments for stealth
        args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
            "--window-position=0,0",
            "--ignore-certificate-errors",
            "--disable-accelerated-2d-canvas",
            "--disable-gpu",
        ]
        
        user_agent = random.choice(USER_AGENTS)
        viewport = {"width": 1920, "height": 1080}
        
        # Use persistent context - saves cookies and session data
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=self.headless,
            args=args,
            viewport=viewport,
            user_agent=user_agent,
            locale="en-US",
            timezone_id="America/New_York",
            color_scheme="dark",
            slow_mo=100,  # Slow down actions to appear more human
        )
        
        # Get the first page or create one
        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()
        
        # Apply stealth patches
        if has_stealth:
            await stealth_async(self.page)
        
        # Additional anti-detection scripts
        await self.page.add_init_script("""
            // Hide webdriver property
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            
            // Hide automation flags
            window.navigator.chrome = { runtime: {} };
            
            // Fake plugins (realistic)
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin' }
                ]
            });
            
            // Fake languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
            
            // Canvas fingerprint protection
            const originalGetContext = HTMLCanvasElement.prototype.getContext;
            HTMLCanvasElement.prototype.getContext = function(type, attributes) {
                const context = originalGetContext.apply(this, arguments);
                if (type === '2d' && context) {
                    const originalGetImageData = context.getImageData;
                    context.getImageData = function() {
                        const imageData = originalGetImageData.apply(this, arguments);
                        for (let i = 0; i < imageData.data.length; i += 4) {
                            imageData.data[i] = imageData.data[i] + (Math.random() * 2 - 1);
                        }
                        return imageData;
                    };
                }
                return context;
            };
            
            // Navigator properties
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
            Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
        """)
        
        self._is_started = True
        logger.info(f"Playwright browser started (headless={self.headless})")
    
    async def close(self) -> None:
        """Close browser session."""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self._is_started = False
        logger.info("Playwright browser closed")
    
    async def _human_delay(self, min_ms: int = 500, max_ms: int = 2000) -> None:
        """Add random human-like delay."""
        delay = random.randint(min_ms, max_ms) / 1000
        await asyncio.sleep(delay)
    
    async def _random_scroll(self) -> None:
        """Simulate human-like random scrolling."""
        if not self.page:
            return
        try:
            scroll_height = random.randint(200, 600)
            await self.page.evaluate(f"window.scrollTo(0, {scroll_height})")
            await self._human_delay(500, 1500)
        except Exception:
            pass
    
    async def _simulate_mouse_movement(self) -> None:
        """Simulate mouse movement to appear more human."""
        if not self.page:
            return
        try:
            await self.page.evaluate("""
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
    
    async def _type_like_human(self, selector: str, text: str) -> None:
        """Type text with human-like speed and occasional pauses."""
        element = self.page.locator(selector)
        await element.click()
        await self._human_delay(200, 500)
        
        for char in text:
            await self.page.keyboard.type(char, delay=random.randint(50, 150))
            if random.random() < 0.1:  # 10% chance of small pause
                await self._human_delay(100, 300)
    
    async def login_twitter(
        self,
        username: str,
        password: str,
        email: Optional[str] = None
    ) -> bool:
        """
        Perform login flow on Twitter/X using Playwright.
        
        Flow: Navigate -> Username -> Next -> (verification) -> Password -> Login
        """
        if not self.page:
            logger.error("Browser not started")
            return False
        
        try:
            logger.info("Navigating to X.com login...")
            await self.page.goto("https://x.com/i/flow/login", wait_until="networkidle")
            await self._human_delay(2000, 4000)
            
            # Simulate initial human behavior
            await self._simulate_mouse_movement()
            await self._random_scroll()
            
            # Step 1: Enter username
            logger.info("Entering username...")
            username_selector = "input[autocomplete='username']"
            await self.page.wait_for_selector(username_selector, timeout=15000)
            await self._simulate_mouse_movement()
            await self._type_like_human(username_selector, username)
            await self._human_delay(500, 1500)
            
            # Step 2: Click Next button
            logger.info("Clicking Next button...")
            next_selectors = [
                "button[role='button'] span:has-text('Next')",
                "button:has-text('Next')",
            ]
            for selector in next_selectors:
                try:
                    btn = self.page.locator(selector).first
                    if await btn.count() > 0:
                        await self._simulate_mouse_movement()
                        await btn.click()
                        logger.info("Clicked Next")
                        break
                except Exception:
                    continue
            
            await self._human_delay(2000, 4000)
            
            # Step 3: Check for verification prompt
            logger.info("Checking for verification prompt...")
            verification_selectors = [
                "input[data-testid='ocfEnterTextTextInput']",
                "input[name='text']"
            ]
            
            for selector in verification_selectors:
                verification_input = self.page.locator(selector)
                if await verification_input.count() > 0 and await verification_input.is_visible():
                    if email:
                        logger.info("Verification required. Entering email...")
                        await self._type_like_human(selector, email)
                        await self._human_delay(500, 1500)
                        
                        for next_sel in next_selectors:
                            try:
                                btn = self.page.locator(next_sel).first
                                if await btn.count() > 0:
                                    await btn.click()
                                    break
                            except Exception:
                                continue
                        
                        await self._human_delay(2000, 4000)
                        break
            
            # Step 4: Enter password
            logger.info("Entering password...")
            password_selector = "input[name='password'], input[type='password']"
            await self.page.wait_for_selector(password_selector, timeout=20000)
            await self._type_like_human(password_selector, password)
            await self._human_delay(500, 1500)
            
            # Step 5: Click Log in button
            logger.info("Clicking Log in button...")
            login_selectors = [
                "button[data-testid='LoginForm_Login_Button']",
                "button:has-text('Log in')",
            ]
            for selector in login_selectors:
                try:
                    btn = self.page.locator(selector).first
                    if await btn.count() > 0:
                        await self._simulate_mouse_movement()
                        await btn.click()
                        logger.info("Clicked Log in")
                        break
                except Exception:
                    continue
            
            # Step 6: Wait for home page
            logger.info("Waiting for home page...")
            home_selectors = [
                "[data-testid='AppTabBar_Home_Link']",
                "[data-testid='primaryColumn']",
                "a[href='/home']"
            ]
            
            for selector in home_selectors:
                try:
                    await self.page.wait_for_selector(selector, timeout=15000)
                    logger.info("Login successful!")
                    return True
                except Exception:
                    continue
            
            if "/home" in self.page.url:
                logger.info("Login successful (URL check)")
                return True
            
            logger.error("Could not confirm login success")
            await self.page.screenshot(path="playwright_login_failure.png")
            return False
            
        except Exception as e:
            logger.error(f"Login failed: {e}")
            try:
                await self.page.screenshot(path="playwright_error.png")
            except Exception:
                pass
            return False
    
    async def get_cookies(self) -> List[Dict[str, Any]]:
        """Get cookies from the current session."""
        if not self.context:
            return []
        
        try:
            return await self.context.cookies()
        except Exception as e:
            logger.error(f"Failed to get cookies: {e}")
            return []
