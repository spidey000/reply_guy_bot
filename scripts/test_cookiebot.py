
import asyncio
import logging
import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from dotenv import load_dotenv

# Load .env explicitly BEFORE importing config
load_dotenv()

from config import settings
from src.cookiebot import CookieBot

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



async def main():
    print("==================================================")
    print("CookieBot Verification Test")
    print("==================================================")
    
    # Check if credentials exist
    if not settings.dummy_username or not settings.dummy_password:
        logger.error("Missing dummy credentials in .env")
        return

    print(f"Target Account: {settings.dummy_username}")
    print(f"Encryption Key Configured: {bool(settings.cookie_encryption_key)}")
    
    bot = CookieBot()
    
    print("\n[1] Attempting to fetch cookies (Headed Mode)...")
    try:
        # Force refresh to test the browser logic
        cookies = await bot.get_valid_cookies(force_refresh=True)
        
        if cookies:
            print(f"\n[SUCCESS] Retrieved {len(cookies)} cookies!")
            
            # Verify specific cookies
            auth_token = next((c for c in cookies if c['name'] == 'auth_token'), None)
            ct0 = next((c for c in cookies if c['name'] == 'ct0'), None)
            
            if auth_token:
                print(" - auth_token: FOUND")
            else:
                print(" - auth_token: MISSING")
                
            if ct0:
                print(" - ct0: FOUND")
            else:
                print(" - ct0: MISSING")
                
            print(f"\nCookies saved to: {bot.cookie_file}")
            
        else:
            print("\n[FAILURE] No cookies obtained.")
            
    except Exception as e:
        print(f"\n[ERROR] Test failed with exception: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
