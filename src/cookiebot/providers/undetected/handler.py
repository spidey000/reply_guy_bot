"""
Undetected-chromedriver provider for cookie extraction.

This is the secondary provider using Selenium with anti-detection patches.
While Selenium-based, undetected-chromedriver patches the browser to avoid
common bot detection triggers.

Features:
- Patches ChromeDriver to avoid detection
- Automatically downloads and patches driver binary
- Works with Cloudflare, Imperva, DataDome
- Selenium compatibility for complex interactions

CRITICAL NOTES FOR X.COM:
- NEVER use headless mode - X.com detects it instantly
- Use existing Chrome profile for session persistence
- Use CDP commands to delete automation fingerprints
- Random delays and human-like behavior are essential
- Specify Chrome version_main for better compatibility
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
USER_DATA_DIR = Path("./browser_data/undetected")

# Updated user agents (Chrome 131, 2024-2025)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

# Thread pool for running sync Selenium code
_executor = ThreadPoolExecutor(max_workers=1)


class UndetectedProvider(BaseCookieProvider):
    """
    Cookie extraction provider using undetected-chromedriver.
    
    This is the secondary provider. It uses Selenium but with 
    ChromeDriver patches to avoid bot detection. Good fallback
    when nodriver fails.
    
    IMPORTANT: NEVER use headless=True for X.com - it's detected instantly.
    """
    
    name = "undetected"
    
    def __init__(self, headless: bool = False):
        # CRITICAL: Force headless=False for X.com
        super().__init__(headless=False)  # Always False for X.com
        self.driver = None
        
        if headless:
            logger.warning("Ignoring headless=True - X.com detects headless mode instantly")
    
    @classmethod
    def is_available(cls) -> bool:
        """Check if undetected-chromedriver is installed."""
        try:
            import undetected_chromedriver
            return True
        except ImportError:
            return False
    
    def _start_sync(self) -> None:
        """Start browser (sync, runs in thread pool)."""
        import undetected_chromedriver as uc
        
        USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        
        options = uc.ChromeOptions()
        
        # CRITICAL anti-detection options for X.com
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--start-maximized")
        options.add_argument(f"--user-data-dir={USER_DATA_DIR}")
        options.add_argument(f"--user-agent={random.choice(USER_AGENTS)}")
        
        # NEVER use headless for X.com - it's detected instantly
        # options.add_argument("--headless")  # NEVER!
        
        # If you MUST hide the window, use this instead of headless:
        # options.add_argument("--window-position=-2400,-2400")
        
        self.driver = uc.Chrome(
            options=options,
            use_subprocess=True,
            # version_main removed - let uc auto-detect Chrome version
        )
        
        # CRITICAL: Remove automation fingerprints via CDP
        self._remove_automation_fingerprints()
        
        # Set timeouts
        self.driver.set_page_load_timeout(30)
        self.driver.implicitly_wait(10)
    
    def _remove_automation_fingerprints(self) -> None:
        """Remove automation fingerprints that X.com checks for."""
        if not self.driver:
            return
        
        try:
            # Delete cdc_ properties and add comprehensive fingerprint protection
            self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': '''
                    // Delete ChromeDriver fingerprints
                    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
                    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
                    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
                    
                    // Ensure webdriver is undefined
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                    
                    // Hide automation flags
                    window.navigator.chrome = { runtime: {} };
                    
                    // Fake plugins array (not empty)
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
                    
                    // WebGL fingerprint protection
                    const getParameterProxy = new Proxy(WebGLRenderingContext.prototype.getParameter, {
                        apply: function(target, thisArg, args) {
                            const param = args[0];
                            const result = Reflect.apply(target, thisArg, args);
                            if (param === 37445) return 'Intel Inc.';
                            if (param === 37446) return 'Intel Iris OpenGL Engine';
                            return result;
                        }
                    });
                    WebGLRenderingContext.prototype.getParameter = getParameterProxy;
                    
                    // AudioContext fingerprint protection
                    const originalCreateAnalyser = AudioContext.prototype.createAnalyser;
                    AudioContext.prototype.createAnalyser = function() {
                        const analyser = originalCreateAnalyser.apply(this, arguments);
                        const originalGetFloatFrequencyData = analyser.getFloatFrequencyData;
                        analyser.getFloatFrequencyData = function(array) {
                            originalGetFloatFrequencyData.apply(this, arguments);
                            for (let i = 0; i < array.length; i++) {
                                array[i] = array[i] + (Math.random() * 0.0001);
                            }
                        };
                        return analyser;
                    };
                    
                    // Navigator properties
                    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
                    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
                    Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
                '''
            })
            logger.debug("Automation fingerprints removed via CDP")
        except Exception as e:
            logger.debug(f"Could not remove fingerprints: {e}")
    
    async def start(self) -> None:
        """Start undetected-chromedriver browser session."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_executor, self._start_sync)
        self._is_started = True
        logger.info("Undetected-chromedriver browser started with anti-detection config (headless=False)")
    
    def _close_sync(self) -> None:
        """Close browser (sync)."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception as e:
                logger.debug(f"Error closing undetected browser: {e}")
    
    async def close(self) -> None:
        """Close browser session."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_executor, self._close_sync)
        self._is_started = False
        logger.info("Undetected-chromedriver browser closed")
    
    def _human_delay_sync(self, min_ms: int = 500, max_ms: int = 2000) -> None:
        """Add random human-like delay (sync)."""
        import time
        delay = random.randint(min_ms, max_ms) / 1000
        time.sleep(delay)
    
    def _random_scroll_sync(self) -> None:
        """Simulate human-like random scrolling (sync)."""
        if not self.driver:
            return
        try:
            scroll_height = random.randint(200, 600)
            self.driver.execute_script(f"window.scrollTo(0, {scroll_height})")
            self._human_delay_sync(500, 1500)
        except Exception:
            pass
    
    def _simulate_mouse_movement_sync(self) -> None:
        """Simulate mouse movement to appear more human (sync)."""
        if not self.driver:
            return
        try:
            self.driver.execute_script("""
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
            element.send_keys(char)
            time.sleep(random.randint(50, 180) / 1000)
            if random.random() < 0.15:
                self._human_delay_sync(200, 500)
    
    def _login_sync(self, username: str, password: str, email: Optional[str] = None) -> bool:
        """Perform login (sync, runs in thread pool)."""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException
        
        try:
            logger.info("Navigating to X.com login...")
            self.driver.get("https://x.com/i/flow/login")
            self._human_delay_sync(3000, 6000)
            
            # Simulate initial human behavior
            self._simulate_mouse_movement_sync()
            self._random_scroll_sync()
            
            # Step 1: Enter username
            logger.info("Entering username...")
            try:
                username_input = WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "input[autocomplete='username']"))
                )
                self._simulate_mouse_movement_sync()
                self._type_like_human_sync(username_input, username)
                self._human_delay_sync(800, 2000)
            except TimeoutException:
                logger.error("Could not find username input")
                return False
            
            # Step 2: Click Next button
            logger.info("Clicking Next button...")
            try:
                next_btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, "//span[text()='Next']/ancestor::button"))
                )
                self._simulate_mouse_movement_sync()
                next_btn.click()
                self._human_delay_sync(2500, 5000)
            except TimeoutException:
                logger.warning("Could not find Next button")
            
            # Step 3: Check for verification prompt
            logger.info("Checking for verification prompt...")
            try:
                verification_input = WebDriverWait(self.driver, 3).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "input[data-testid='ocfEnterTextTextInput']"))
                )
                if email:
                    logger.info("Verification required. Entering email...")
                    self._type_like_human_sync(verification_input, email)
                    self._human_delay_sync(800, 1800)
                    
                    try:
                        next_btn = self.driver.find_element(By.XPATH, "//span[text()='Next']/ancestor::button")
                        next_btn.click()
                        self._human_delay_sync(2500, 5000)
                    except Exception:
                        pass
            except TimeoutException:
                pass  # No verification needed
            
            # Step 4: Enter password
            logger.info("Entering password...")
            try:
                password_input = WebDriverWait(self.driver, 20).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='password'], input[type='password']"))
                )
                self._simulate_mouse_movement_sync()
                self._type_like_human_sync(password_input, password)
                self._human_delay_sync(800, 2000)
            except TimeoutException:
                logger.error("Could not find password input")
                self.driver.save_screenshot("undetected_no_password.png")
                return False
            
            # Step 5: Click Log in button
            logger.info("Clicking Log in button...")
            try:
                login_btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-testid='LoginForm_Login_Button']"))
                )
                self._simulate_mouse_movement_sync()
                login_btn.click()
                self._human_delay_sync(4000, 7000)
            except TimeoutException:
                try:
                    login_btn = self.driver.find_element(By.XPATH, "//span[text()='Log in']/ancestor::button")
                    login_btn.click()
                    self._human_delay_sync(4000, 7000)
                except Exception:
                    logger.warning("Could not find Log in button")
            
            # Step 6: Verify login success
            logger.info("Verifying login success...")
            try:
                WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='AppTabBar_Home_Link']"))
                )
                logger.info("Login successful!")
                return True
            except TimeoutException:
                pass
            
            # Check URL as fallback
            if "/home" in self.driver.current_url:
                logger.info("Login successful (URL check)")
                return True
            
            logger.error("Could not confirm login success")
            self.driver.save_screenshot("undetected_login_failure.png")
            return False
            
        except Exception as e:
            logger.error(f"Login failed: {e}")
            try:
                self.driver.save_screenshot("undetected_error.png")
            except Exception:
                pass
            return False
    
    async def login_twitter(
        self,
        username: str,
        password: str,
        email: Optional[str] = None
    ) -> bool:
        """Perform login flow on Twitter/X using undetected-chromedriver."""
        if not self.driver:
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
        if not self.driver:
            return []
        
        try:
            return self.driver.get_cookies()
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
