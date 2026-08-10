import asyncio
from playwright.async_api import async_playwright
from pathlib import Path

output_dir = Path("d:/multi-agent-code-system/captures")
output_dir.mkdir(exist_ok=True)

urls = [
    ("http://localhost:8001/web/", "landing.png"),
    ("http://localhost:8001/web/dashboard.html", "dashboard.png"),
]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for url, filename in urls:
            print(f"Capturing {url} ...")
            page = await browser.new_page(viewport={"width": 1440, "height": 900})
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(1)
            path = output_dir / filename
            await page.screenshot(path=str(path), full_page=True)
            print(f"Saved {path}")
            await page.close()
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
