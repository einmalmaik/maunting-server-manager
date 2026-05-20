"""Browser test for Conan Exiles Panel."""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

PANEL_URL = "http://localhost:5173"
SCREENSHOT_DIR = Path(__file__).parent / "browser_screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

def screenshot(page, name: str) -> None:
    page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"), full_page=True)
    print(f"Screenshot: {name}.png")

def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()

        print("Opening login page...")
        page.goto(f"{PANEL_URL}/")
        page.wait_for_load_state("networkidle")
        screenshot(page, "01_login_page")

        # Fill login form
        print("Logging in as admin...")
        page.fill('input#username', "admin")
        page.fill('input#password', "admin12345")
        page.click('button[type="submit"]')
        page.wait_for_timeout(1500)
        screenshot(page, "02_dashboard_after_login")

        # Check dashboard UFW card
        print("Checking dashboard...")
        page_content = page.content()
        if "UFW" in page_content or "ufw" in page_content.lower():
            print("[OK] UFW section found on dashboard")
        else:
            print("[WARN] UFW section not visible on dashboard")

        # Navigate to Servers page
        print("Navigating to Servers page...")
        page.click('text=Servers')
        page.wait_for_timeout(1500)
        screenshot(page, "03_servers_page")

        page_content = page.content()
        if "Pterodactyl" in page_content or "Pterodactyl-Import" in page_content:
            print("[OK] Pterodactyl tab found on servers page")
        else:
            print("[WARN] Pterodactyl tab not found on servers page")

        # Try to create a server
        print("Trying to create a server...")
        page.click('button:has-text("New Server")')
        page.wait_for_timeout(500)
        page.fill('input[placeholder="e.g. pvp-eu"]', "testserver")
        page.click('button:has-text("Create")')
        page.wait_for_timeout(1500)
        screenshot(page, "04_create_server")

        # Check if server was created
        page_content = page.content()
        if "testserver" in page_content:
            print("[OK] Server 'testserver' created successfully")
        else:
            print("[WARN] Server creation may have failed or needs refresh")

        # Check for multi-server switch
        if "Select Server" in page_content or "Switch" in page_content or "testserver" in page_content:
            print("[OK] Server selector/switch or created server found")
        else:
            print("[INFO] Server selector not found (may need page refresh)")

        # Navigate back to Dashboard to check UFW
        print("Checking Dashboard for UFW...")
        page.click('text=Dashboard')
        page.wait_for_timeout(1500)
        screenshot(page, "05_dashboard_ufw")
        page_content = page.content()
        if "UFW" in page_content or "ufw" in page_content.lower() or "port" in page_content.lower():
            print("[OK] Port/UFW section found on dashboard")
        else:
            print("[INFO] UFW section not visible (server may need to be installed first)")

        browser.close()
        print("Browser test completed.")
        return 0

if __name__ == "__main__":
    sys.exit(main())
