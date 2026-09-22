"""Interactive one-time session seeder. Run on your own machine (not in Docker).

    python seed_session.py

Opens a browser to PrairieTest. Log in with CWL + Duo, and check
'remember this device for 30 days'. Once you see your PrairieTest home page,
return here and press Enter. The session is saved to data/storageState.json.
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

HOME = "https://us.prairietest.com/pt"
OUT = Path("data/storageState.json")

async def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(HOME)
        print("Log in with CWL + Duo (check 'remember this device 30 days').")
        input("When your PrairieTest home page is loaded, press Enter here to save the session...")
        await context.storage_state(path=str(OUT))
        print(f"Saved session to {OUT.resolve()}")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
