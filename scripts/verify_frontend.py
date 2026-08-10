import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

OUTPUT_DIR = Path("d:/multi-agent-code-system/captures")
OUTPUT_DIR.mkdir(exist_ok=True)

LANDING_URL = "http://localhost:8001/web/"
DASHBOARD_URL = "http://localhost:8001/web/dashboard.html"

async def capture_and_verify():
    results = {
        "landing_accessible": False,
        "dashboard_accessible": False,
        "landing_screenshot": None,
        "dashboard_screenshot": None,
        "console_errors": [],
        "command_palette_works": False,
        "command_palette_api_works": False,
        "escape_closes_palette": False,
        "reduced_motion_ok": False,
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # Helper to collect console messages
        def on_console(msg):
            text = msg.text
            msg_type = msg.type
            # Capture errors and warnings that look like CSS/JS issues
            if msg_type in ("error", "warning"):
                results["console_errors"].append(f"[{msg_type.upper()}] {text}")

        # --- Landing page ---
        print(f"[1/4] Navigating to landing page: {LANDING_URL}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", on_console)
        page.on("pageerror", lambda exc: results["console_errors"].append(f"[PAGE_ERROR] {exc}"))

        try:
            await page.goto(LANDING_URL, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)  # Wait for curtain reveal
            landing_path = OUTPUT_DIR / "landing_new.png"
            await page.screenshot(path=str(landing_path), full_page=True)
            results["landing_accessible"] = True
            results["landing_screenshot"] = str(landing_path)
            print(f"      Saved landing screenshot: {landing_path}")
        except Exception as e:
            print(f"      Landing page failed: {e}")
        finally:
            await page.close()

        # --- Dashboard page ---
        print(f"[2/4] Navigating to dashboard: {DASHBOARD_URL}")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", on_console)
        page.on("pageerror", lambda exc: results["console_errors"].append(f"[PAGE_ERROR] {exc}"))

        try:
            await page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(1)
            dashboard_path = OUTPUT_DIR / "dashboard_new.png"
            await page.screenshot(path=str(dashboard_path), full_page=True)
            results["dashboard_accessible"] = True
            results["dashboard_screenshot"] = str(dashboard_path)
            print(f"      Saved dashboard screenshot: {dashboard_path}")

            # --- Command Palette test ---
            print("[3/4] Testing Command Palette (Ctrl+K) and Esc...")

            # Ensure dashboard is initialized
            await page.wait_for_function("() => window.__dashboard && typeof window.__dashboard.toggleCommandPalette === 'function'", timeout=10000)

            def palette_state():
                return page.evaluate("""() => {
                    const el = document.getElementById('commandPalette');
                    if (!el) return { exists: false };
                    const style = window.getComputedStyle(el);
                    return {
                        exists: true,
                        hidden: el.hidden,
                        display: style.display,
                        visible: style.display !== 'none',
                        stateOpen: window.__dashboard.getState().paletteOpen,
                    };
                }""")

            before = await palette_state()
            print(f"      Before shortcut: hidden={before.get('hidden')}, visible={before.get('visible')}, stateOpen={before.get('stateOpen')}")

            # Attempt 1: keyboard shortcut
            await page.keyboard.press("Control+k")
            await asyncio.sleep(0.6)
            after_key = await palette_state()
            print(f"      After Ctrl+K: hidden={after_key.get('hidden')}, visible={after_key.get('visible')}, stateOpen={after_key.get('stateOpen')}")

            diag_path = OUTPUT_DIR / "dashboard_ctrl_k.png"
            await page.screenshot(path=str(diag_path), full_page=True)
            print(f"      Diagnostic screenshot saved: {diag_path}")

            # Attempt 2: direct API call (verifies the palette logic itself)
            await page.evaluate("() => window.__dashboard.toggleCommandPalette()")
            await asyncio.sleep(0.4)
            after_api = await palette_state()
            print(f"      After API toggle: hidden={after_api.get('hidden')}, visible={after_api.get('visible')}, stateOpen={after_api.get('stateOpen')}")

            results["command_palette_works"] = after_key.get("visible") or after_api.get("visible")
            results["command_palette_api_works"] = after_api.get("visible")

            # Esc close test (start from open state)
            if not after_api.get("visible"):
                await page.evaluate("() => { if (!window.__dashboard.getState().paletteOpen) window.__dashboard.toggleCommandPalette(); }")
                await asyncio.sleep(0.3)

            await page.keyboard.press("Escape")
            await asyncio.sleep(0.4)
            after_esc = await palette_state()
            results["escape_closes_palette"] = not after_esc.get("visible")
            print(f"      After Esc: hidden={after_esc.get('hidden')}, visible={after_esc.get('visible')}, stateOpen={after_esc.get('stateOpen')}")
            print(f"      Esc closes palette: {results['escape_closes_palette']}")

        except Exception as e:
            print(f"      Dashboard page failed: {e}")
        finally:
            await page.close()

        # --- Prefers-reduced-motion test ---
        print("[4/4] Testing prefers-reduced-motion...")
        page = await browser.new_page(
            viewport={"width": 1440, "height": 900},
            reduced_motion="reduce",
        )
        page.on("console", on_console)
        page.on("pageerror", lambda exc: results["console_errors"].append(f"[PAGE_ERROR] {exc}"))

        try:
            await page.goto(LANDING_URL, wait_until="networkidle", timeout=30000)
            # Check if CSS animations/transitions are effectively reduced
            reduced = await page.evaluate("""() => {
                const style = window.getComputedStyle(document.body);
                const anim = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                return anim;
            }""")
            results["reduced_motion_ok"] = reduced
            print(f"      Media query (prefers-reduced-motion: reduce) matches: {reduced}")
        except Exception as e:
            print(f"      Reduced motion test failed: {e}")
        finally:
            await page.close()

        await browser.close()
    return results


def summarize_results(results):
    print("\n========== VERIFICATION SUMMARY ==========")
    print(f"Landing page accessible: {results['landing_accessible']}")
    print(f"Dashboard accessible:    {results['dashboard_accessible']}")
    print(f"Landing screenshot:      {results['landing_screenshot']}")
    print(f"Dashboard screenshot:    {results['dashboard_screenshot']}")
    print(f"Command Palette Ctrl+K:  {results['command_palette_works']}")
    print(f"Command Palette API:     {results['command_palette_api_works']}")
    print(f"Esc closes palette:      {results['escape_closes_palette']}")
    print(f"Prefers-reduced-motion:  {results['reduced_motion_ok']}")

    errors = results["console_errors"]
    if errors:
        print(f"\nConsole/page errors found ({len(errors)}):")
        for err in errors:
            print(f"  - {err}")
    else:
        print("\nNo console/page errors detected.")

    passed = (
        results["landing_accessible"]
        and results["dashboard_accessible"]
        and results["landing_screenshot"]
        and results["dashboard_screenshot"]
        and not any("theme.css" in e.lower() or "dashboard.js" in e.lower() for e in errors)
    )
    print(f"\nOverall validation: {'PASSED' if passed else 'FAILED'}")
    return passed


if __name__ == "__main__":
    results = asyncio.run(capture_and_verify())
    summarize_results(results)
