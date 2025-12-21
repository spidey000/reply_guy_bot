#!/usr/bin/env python3
"""
Test script to verify X.com login and cookie extraction.
Uses CookieBot with the configured dummy account.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from src.cookiebot import CookieBot
from config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("TEST_LOGIN")

async def test_login():
    print("\n" + "="*60)
    print("🔐 X.COM LOGIN TEST")
    print("="*60)
    
    print(f"\n📋 Account: {settings.dummy_username}")
    print(f"📧 Email: {settings.dummy_email}")
    print(f"🔑 Password: {'*' * 8}")
    
    bot = CookieBot()
    
    print("\n🚀 Starting login attempt...")
    print("   (A browser window will open - DO NOT interact with it)")
    print("   (This may take 30-60 seconds)")
    
    try:
        # get_fresh_cookies uses settings internally
        cookies = await bot.get_fresh_cookies()
        
        if cookies:
            print(f"\n✅ SUCCESS! Got {len(cookies)} cookies")
            
            # Show important cookies
            important = ['auth_token', 'ct0', 'twid']
            for c in cookies:
                if c.get('name') in important:
                    value = c['value']
                    print(f"   ✓ {c['name']}: {value[:20]}..." if len(value) > 20 else f"   ✓ {c['name']}: {value}")
            
            print(f"\n💾 Cookies saved to: cookies.json")
            return True
        else:
            print("\n❌ FAILED - No cookies retrieved")
            return False
            
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("\n⚠️  IMPORTANTE: Este test abrirá una ventana del navegador.")
    print("⚠️  NO interactúes con ella. El bot hará el login automáticamente.")
    input("\nPresiona ENTER para continuar...")
    
    success = asyncio.run(test_login())
    
    if success:
        print("\n✨ Test completado exitosamente!")
    else:
        print("\n💥 Test falló. Revisa los logs arriba.")
