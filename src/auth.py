"""Headless CWL + Duo auto-login.

Fills CWL credentials from config (env-backed), ticks 'trust this browser',
triggers a Duo push, notifies the user to approve it, and waits for the login
to complete. All site selectors are constants below so they can be tuned on the
first real login; on failure a screenshot + HTML are written to data/debug/.

Claude never enters these credentials itself — the bot reads them from its own
environment at runtime.
"""
from __future__ import annotations
import logging
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# --- Selectors (tune on first real login; failures dump to data/debug/) ------
USERNAME_SELECTORS = ["#username", "input[name='username']", "input[autocomplete='username']"]
PASSWORD_SELECTORS = ["#password", "input[name='password']", "input[type='password']"]
SUBMIT_SELECTORS = ["button[type='submit']", "input[type='submit']", "button[name='submit']", "button:has-text('Continue')", "button:has-text('Sign in')"]
DUO_PUSH_SELECTORS = ["button:has-text('Send me a Push')", "button:has-text('Send Me a Push')", "#auth-methods button:has-text('Push')"]
DUO_TRUST_SELECTORS = ["#trust-browser-button", "button:has-text('Yes, trust browser')", "button:has-text('Trust this browser')", "button:has-text('Yes, this is my device')"]

LOGIN_URL_MARKERS = ("authentication.ubc.ca", "/cas", "/login", "duosecurity.com", "duo.com", "sso")
SUCCESS_URL = "us.prairietest.com/pt"

class AuthError(Exception):
    pass

def credentials(auth_config) -> tuple[str, str]:
    if not auth_config.username or not auth_config.password:
        raise AuthError(
            "CWL_USERNAME / CWL_PASSWORD not set (config auth.username/auth.password)"
        )
    return auth_config.username, auth_config.password

def session_exists(path: str) -> bool:
    return Path(path).is_file()

def is_login_page(url: str) -> bool:
    u = (url or "").lower()
    return any(m in u for m in LOGIN_URL_MARKERS)

async def _fill_first(page, selectors, value) -> bool:
    for sel in selectors:
        loc = page.locator(sel)
        try:
            if await loc.count() > 0:
                await loc.first.fill(value)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False

async def _click_first(page, selectors) -> bool:
    for sel in selectors:
        loc = page.locator(sel)
        try:
            if await loc.count() > 0 and await loc.first.is_visible():
                await loc.first.click()
                return True
        except Exception:  # noqa: BLE001
            continue
    return False

async def _visible_any(page, selectors) -> bool:
    for sel in selectors:
        loc = page.locator(sel)
        try:
            if await loc.count() > 0 and await loc.first.is_visible():
                return True
        except Exception:  # noqa: BLE001
            continue
    return False

async def _dump(page, debug_dir: str, tag: str) -> None:
    try:
        d = Path(debug_dir)
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        await page.screenshot(path=str(d / f"{tag}-{stamp}.png"))
        (d / f"{tag}-{stamp}.url.txt").write_text(page.url, encoding="utf-8")
    except Exception:  # noqa: BLE001
        log.warning("could not write debug dump for %s", tag)

async def auto_login(page, auth_config, notifier, debug_dir: str = "data/debug") -> bool:
    """Drive the CWL + Duo flow on `page` (already on the login redirect).

    Returns True once the browser is back on PrairieTest and logged in.
    """
    user, pwd = credentials(auth_config)
    try:
        await page.wait_for_load_state("domcontentloaded")

        # CWL may be single-step or username-then-password.
        await _fill_first(page, USERNAME_SELECTORS, user)
        if not await _visible_any(page, PASSWORD_SELECTORS):
            await _click_first(page, SUBMIT_SELECTORS)
            await page.wait_for_timeout(1500)
        await _fill_first(page, PASSWORD_SELECTORS, pwd)
        await _click_first(page, SUBMIT_SELECTORS)
        await page.wait_for_timeout(2500)

        # Duo Universal Prompt (push may auto-send; click if a button is present).
        if "duo" in page.url.lower() or await _visible_any(page, DUO_PUSH_SELECTORS + DUO_TRUST_SELECTORS):
            await _click_first(page, DUO_PUSH_SELECTORS)
            notifier.send("duo_approve", "Approve the Duo push on your phone now.")

        ok = await _await_success(page, auth_config)
        if not ok:
            await _dump(page, debug_dir, "login_timeout")
        return ok
    except Exception as e:  # noqa: BLE001
        log.exception("auto_login error")
        await _dump(page, debug_dir, "login_error")
        notifier.send("auth_failed", f"auto-login error: {e}")
        return False

async def _await_success(page, auth_config) -> bool:
    deadline = time.monotonic() + auth_config.duo_wait_seconds
    while time.monotonic() < deadline:
        if SUCCESS_URL in page.url and not is_login_page(page.url):
            return True
        if auth_config.trust_device:
            await _click_first(page, DUO_TRUST_SELECTORS)
        await page.wait_for_timeout(2000)
    return SUCCESS_URL in page.url and not is_login_page(page.url)
