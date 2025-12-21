"""
DrissionPage provider for cookie extraction.

This is the quaternary (4th) fallback provider using DrissionPage.
DrissionPage was designed from scratch without webdriver, making it
harder to detect than Selenium-based solutions.

Features:
- No WebDriver dependency (designed from scratch)
- Combines browser control with request handling
- Good anti-detection by default
- Simpler API than Selenium

CRITICAL NOTES FOR X.COM:
- NEVER use headless mode - X.com detects it instantly
- Random delays and human-like behavior are essential
"""

import asyncio
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, List, Dict, Any

from ...base import BaseCookieProvider

logger = logging.getLogger(__name__)

# User data directory for persistent sessions
USER_DATA_DIR = Path("./browser_data/drissionpage")

# Updated user agents (Chrome 131, 2024-2025)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

# Thread pool for running sync code
_executor = ThreadPoolExecutor(max_workers=1)


class DrissionPageProvider(BaseCookieProvider):
    """
    Cookie extraction provider using DrissionPage.
    
    This is the 4th fallback option. DrissionPage is designed from scratch
    without webdriver, making it naturally stealthier than Selenium-based tools.
    
    IMPORTANT: NEVER use headless mode for X.com.
    """
    
    name = "drissionpage"
    
    def __init__(self, headless: bool = False):
        # CRITICAL: Force headless=False for X.com
        super().__init__(headless=False)
        self.page = None
        
        if headless:
            logger.warning("Ignoring headless=True - X.com detects headless mode instantly")
    
    @classmethod
    def is_available(cls) -> bool:
        """Check if DrissionPage is installed."""
        try:
            from DrissionPage import ChromiumPage
            return True
        except ImportError:
            return False
    
    def _start_sync(self) -> None:
        """Start browser (sync, runs in thread pool)."""
        from DrissionPage import ChromiumPage, ChromiumOptions
        
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        
        options = ChromiumOptions()
        options.set_user_data_path(str(USER_DATA_DIR))
        options.set_argument("--disable-blink-features=AutomationControlled")
        options.set_argument("--no-sandbox")
        options.set_argument("--disable-dev-shm-usage")
        options.set_argument("--window-size=1920,1080")
        options.set_argument(f"--user-agent={random.choice(USER_AGENTS)}")
        
        # NEVER use headless for X.com
        # options.headless()  # NEVER!
        
        self.page = ChromiumPage(options)
    
    async def start(self) -> None:
        """Start DrissionPage browser session."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_executor, self._start_sync)
        self._is_started = True
        logger.info("DrissionPage browser started (headless=False)")
    
    def _close_sync(self) -> None:
        """Close browser (sync)."""
        if self.page:
            try:
                self.page.quit()
            except Exception as e:
                logger.debug(f"Error closing DrissionPage: {e}")
    
    async def close(self) -> None:
        """Close browser session."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_executor, self._close_sync)
        self._is_started = False
        logger.info("DrissionPage browser closed")
    
    def _human_delay_sync(self, min_ms: int = 500, max_ms: int = 2000) -> None:
        """Add random human-like delay (sync)."""
        import time
        delay = random.randint(min_ms, max_ms) / 1000
        time.sleep(delay)
    
    def _random_scroll_sync(self) -> None:
        """Simulate human-like random scrolling (sync)."""
        if not self.page:
            return
        try:
            scroll_height = random.randint(200, 600)
            self.page.run_js(f"window.scrollTo(0, {scroll_height})")
            self._human_delay_sync(500, 1500)
        except Exception:
            pass
    
    def _simulate_mouse_movement_sync(self) -> None:
        """Simulate mouse movement to appear more human (sync)."""
        if not self.page:
            return
        try:
            self.page.run_js("""
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
    
    def _type_like_human_sync(self, element, text: str) -> None:
        """Type text with human-like speed (sync)."""
        import time
        element.click()
        self._human_delay_sync(300, 700)
        
        for char in text:
            element.input(char, clear=False)
            time.sleep(random.randint(50, 180) / 1000)
            if random.random() < 0.15:
                self._human_delay_sync(200, 500)
    
    def _login_sync(self, username: str, password: str, email: Optional[str] = None) -> bool:
        """Perform login (sync, runs in thread pool)."""
        try:
            logger.info("Navigating to X.com login...")
            self.page.get("https://x.com/i/flow/login")
            self._human_delay_sync(3000, 6000)
            
            # Simulate initial human behavior
            self._simulate_mouse_movement_sync()
            self._random_scroll_sync()
            
            # Step 1: Enter username
            logger.info("Entering username...")
            username_input = self.page.ele("@autocomplete=username", timeout=15)
            if not username_input:
                logger.error("Could not find username input")
                return False
            
            self._type_like_human_sync(username_input, username)
            self._human_delay_sync(800, 2000)
            
            # Step 2: Click Next
            logger.info("Clicking Next button...")
            next_btn = self.page.ele("@text()=Next", timeout=5)
            if next_btn:
                next_btn.click()
                self._human_delay_sync(2500, 5000)
            
            # Step 3: Check for verification
            logger.info("Checking for verification prompt...")
            try:
                verification_input = self.page.ele("@data-testid=ocfEnterTextTextInput", timeout=3)
                if verification_input and email:
                    logger.info("Verification required. Entering email...")
                    self._type_like_human_sync(verification_input, email)
                    self._human_delay_sync(800, 1800)
                    
                    next_btn = self.page.ele("@text()=Next")
                    if next_btn:
                        next_btn.click()
                        self._human_delay_sync(2500, 5000)
            except Exception:
                pass
            
            # Step 4: Enter password
            logger.info("Entering password...")
            password_input = self.page.ele("@name=password", timeout=20)
            if not password_input:
                password_input = self.page.ele("@type=password", timeout=5)
            
            if not password_input:
                logger.error("Could not find password input")
                return False
            
            self._type_like_human_sync(password_input, password)
            self._human_delay_sync(800, 2000)
            
            # Step 5: Click Log in
            logger.info("Clicking Log in button...")
            login_btn = self.page.ele("@data-testid=LoginForm_Login_Button", timeout=5)
            if not login_btn:
                login_btn = self.page.ele("@text()=Log in")
            
            if login_btn:
                login_btn.click()
                self._human_delay_sync(4000, 7000)
            
            # Step 6: Verify login
            logger.info("Verifying login success...")
            home_link = self.page.ele("@data-testid=AppTabBar_Home_Link", timeout=15)
            if home_link:
                logger.info("Login successful!")
                return True
            
            if "/home" in self.page.url:
                logger.info("Login successful (URL check)")
                return True
            
            logger.error("Could not confirm login success")
            return False
            
        except Exception as e:
            logger.error(f"Login failed: {e}")
            return False
    
    async def login_twitter(
        self,
        username: str,
        password: str,
        email: Optional[str] = None
    ) -> bool:
        """Perform login flow on Twitter/X using DrissionPage."""
        if not self.page:
            logger.error("Browser not started")
            return False
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor,
            self._login_sync,
            username,
            password,
            email
        )
    
    def _get_cookies_sync(self) -> List[Dict[str, Any]]:
        """Get cookies (sync)."""
        if not self.page:
            return []
        
        try:
            return self.page.cookies()
        except Exception as e:
            logger.error(f"Failed to get cookies: {e}")
            return []
    
    async def get_cookies(self) -> List[Dict[str, Any]]:
        """Get cookies from the current session."""
        loop = asyncio.get_event_loop()
        cookies = await loop.run_in_executor(_executor, self._get_cookies_sync)
        
        # Normalize cookie format
        return [
            {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain"),
                "path": c.get("path", "/"),
                "expires": c.get("expiry"),
                "httpOnly": c.get("httpOnly", False),
                "secure": c.get("secure", False),
                "sameSite": c.get("sameSite", "Lax"),
            }
            for c in cookies
        ]
