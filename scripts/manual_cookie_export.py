#!/usr/bin/env python3
"""
Manual Cookie Export Script for X.com

This script opens a browser for you to login manually.
After you login, it will save the cookies automatically.

Usage:
    .venv/bin/python scripts/manual_cookie_export.py
"""

import asyncio
import json
import logging
import sys
import os

sys.path.append(os.getcwd())

from dotenv import load_dotenv
load_dotenv()

from playwright.async_api import async_playwright
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

COOKIE_FILE = Path("cookies.json")


async def main():
    print("=" * 60)
    print("Manual Cookie Export for X.com")
    print("=" * 60)
    print()
    print("A browser will open. Please login to X.com manually.")
    print("After logging in successfully, press Enter here to save cookies.")
    print()
    
    async with async_playwright() as p:
        # Launch browser in headed mode
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()
        
        # Navigate to X.com login
        await page.goto("https://x.com/i/flow/login")
        
        print("Browser opened. Login to X.com now...")
        print()
        
        # Wait for user to complete login
        input("Press ENTER after you have logged in successfully...")
        
        # Get cookies
        cookies = await context.cookies()
        
        if cookies:
            # Save cookies
            with open(COOKIE_FILE, 'w') as f:
                json.dump(cookies, f, indent=2)
            
            # Check for important cookies
            auth_token = any(c['name'] == 'auth_token' for c in cookies)
            ct0 = any(c['name'] == 'ct0' for c in cookies)
            
            print()
            print(f"Saved {len(cookies)} cookies to {COOKIE_FILE}")
            print(f"  - auth_token: {'✅ Found' if auth_token else '❌ Missing'}")
            print(f"  - ct0: {'✅ Found' if ct0 else '❌ Missing'}")
            
            if auth_token and ct0:
                print()
                print("✅ SUCCESS! Cookies saved. The bot should now work.")
            else:
                print()
                print("⚠️  Some cookies may be missing. Try logging in again.")
        else:
            print("❌ No cookies found. Make sure you logged in successfully.")
        
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
