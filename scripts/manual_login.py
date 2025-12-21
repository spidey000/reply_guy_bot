#!/usr/bin/env python3
"""
Manual Cookie Extraction Script
================================

Opens a browser window for you to login to X.com manually.
After you complete the login, press ENTER and the script will
extract and save your cookies.

Usage:
    python scripts/manual_login.py
"""

import asyncio
import json
import sys
from pathlib import Path

# Add project root
sys.path.append(str(Path(__file__).parent.parent))


async def manual_login():
    print("\n" + "="*60)
    print("🔐 MANUAL LOGIN - COOKIE EXTRACTION")
    print("="*60)
    
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("❌ Playwright not installed. Run: pip install playwright")
        return False
    
    print("\n📋 Instrucciones:")
    print("   1. Se abrirá una ventana del navegador")
    print("   2. Haz login manualmente en X.com")
    print("   3. Cuando estés en la página principal, vuelve aquí")
    print("   4. Presiona ENTER para extraer las cookies")
    
    input("\n▶️  Presiona ENTER para abrir el navegador...")
    
    async with async_playwright() as p:
        # Launch browser (visible, not headless)
        browser = await p.chromium.launch(
            headless=False,
            args=["--start-maximized"]
        )
        
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900}
        )
        page = await context.new_page()
        
        # Navigate to X.com login
        print("\n🌐 Abriendo X.com...")
        await page.goto("https://x.com/login")
        
        print("\n⏳ Esperando que hagas login...")
        print("   (Haz login manualmente en la ventana del navegador)")
        
        input("\n✅ Cuando hayas terminado el login, presiona ENTER aquí...")
        
        # Get current URL to verify login
        current_url = page.url
        print(f"\n📍 URL actual: {current_url}")
        
        # Extract cookies
        cookies = await context.cookies()
        
        if not cookies:
            print("❌ No se encontraron cookies")
            await browser.close()
            return False
        
        # Check for important cookies
        important_cookies = ['auth_token', 'ct0', 'twid']
        found = {name: None for name in important_cookies}
        
        for cookie in cookies:
            if cookie['name'] in important_cookies:
                found[cookie['name']] = cookie['value'][:20] + "..."
        
        print("\n🍪 Cookies encontradas:")
        for name, value in found.items():
            status = "✅" if value else "❌"
            print(f"   {status} {name}: {value or 'NO ENCONTRADA'}")
        
        # Check if we have auth_token
        if not any(c['name'] == 'auth_token' for c in cookies):
            print("\n⚠️  No se encontró auth_token. ¿Completaste el login?")
            retry = input("¿Quieres intentar de nuevo? (s/n): ")
            if retry.lower() == 's':
                await browser.close()
                return await manual_login()
            await browser.close()
            return False
        
        # Save cookies
        cookie_file = Path("cookies.json")
        
        # Check for encryption
        try:
            from config import settings
            if settings.cookie_encryption_key:
                from cryptography.fernet import Fernet
                fernet = Fernet(settings.cookie_encryption_key.encode())
                encrypted = fernet.encrypt(json.dumps(cookies).encode())
                with open(cookie_file, 'wb') as f:
                    f.write(encrypted)
                print(f"\n🔒 Cookies guardadas (encriptadas): {cookie_file}")
            else:
                with open(cookie_file, 'w') as f:
                    json.dump(cookies, f, indent=2)
                print(f"\n💾 Cookies guardadas: {cookie_file}")
        except Exception as e:
            # Fallback: save unencrypted
            with open(cookie_file, 'w') as f:
                json.dump(cookies, f, indent=2)
            print(f"\n💾 Cookies guardadas: {cookie_file}")
        
        await browser.close()
        print("\n✨ ¡Listo! Las cookies están guardadas.")
        print("   El bot ahora puede usar estas cookies para autenticarse.")
        return True


if __name__ == "__main__":
    success = asyncio.run(manual_login())
    sys.exit(0 if success else 1)
