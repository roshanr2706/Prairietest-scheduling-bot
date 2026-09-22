"""Headless CWL + Duo auto-login for PrairieTest.

Drives the full observed login chain as a state machine:

  PrairieTest landing ("Login") -> PrairieLearn institution chooser (pick UBC)
  -> UBC CWL form (#username/#password) -> Duo push (auto-sent; user approves on
  phone) -> "Is this your device?" (trust) -> back to a logged-in PrairieTest.

Selectors live in constants below (validated live 2026-09-21). On failure a
screenshot + HTML are written to data/debug/. The CWL password is only ever
typed on the real UBC host (authentication.ubc.ca). Claude never enters these
credentials itself — the bot reads them from its own environment at runtime.
"""
from __future__ import annotations
import logging
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

PRAIRIETEST_HOME = "https://us.prairietest.com/pt"
CWL_HOST = "authentication.ubc.ca"

# --- Selectors (tune here; failures dump to data/debug/) ---------------------
LOGIN_CTA_SELECTORS = [
    "a.btn:has-text('Login')", "a:has-text('Login')", "button:has-text('Login')",
]
INSTITUTION_SELECTORS = [
    "a:has-text('University of British Columbia')",
    "a[href*='/saml/login']:has-text('British Columbia')",
]
USERNAME_SELECTORS = ["#username", "input[name='j_username']", "input[type='text']"]
PASSWORD_SELECTORS = ["#password", "input[name='j_password']", "input[type='password']"]
SUBMIT_SELECTORS = [
    "button[name='_eventId_proceed']", "button[type='submit']",
    "input[type='submit']", "button:has-text('Login')",
]
DUO_TRUST_SELECTORS = [
    "button:has-text('Yes, this is my device')", "#trust-browser-button",
    "button:has-text('Yes, trust browser')",
]
DUO_NOTRUST_SELECTORS = ["button:has-text('No, other people use this device')"]

LOGIN_URL_MARKERS = ("authentication.ubc.ca", "/cas", "/login", "duosecurity.com", "duo.com", "sso")
LOGGED_IN_MARKERS = (
    "Exams available for reservations", "PrairieTest Homepage", "Exam reservations",
    "Exam information", "Choose a session", "Choose a new session",
    "Delete this reservation", "scheduled to start",
)

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

def is_logged_in(url: str, text: str) -> bool:
    return "prairietest.com" in (url or "").lower() and any(m in text for m in LOGGED_IN_MARKERS)

def login_step(url: str, text: str) -> str:
    """Classify the current page into the next login action to take."""
    u = (url or "").lower()
    if is_logged_in(url, text):
        return "done"
    if "duosecurity.com" in u or "duo.com" in u:
        if "Is this your device" in text:
            return "duo_trust"
        return "duo_wait"
    if CWL_HOST in u:
        return "fill_cwl"
    if "prairielearn.com" in u and ("Search for your institution" in text or "Sign in to continue" in text):
        return "choose_institution"
    if "An exam proctoring system" in text:
        return "click_login"
    return "wait"

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

async def _page_text(page) -> str:
    try:
        return await page.inner_text("body")
    except Exception:  # noqa: BLE001
        return ""

async def auto_login(page, auth_config, notifier, debug_dir: str = "data/debug") -> bool:
    """Drive the full CWL + Duo chain until PrairieTest is logged in.

    `page` may start anywhere in the chain (typically the PrairieTest landing).
    Returns True once a logged-in PrairieTest page is reached.
    """
    user, pwd = credentials(auth_config)
    deadline = time.monotonic() + auth_config.duo_wait_seconds + 90
    duo_notified = False

    while time.monotonic() < deadline:
        try:
            await page.wait_for_load_state("domcontentloaded")
        except Exception:  # noqa: BLE001
            pass
        url = page.url
        text = await _page_text(page)
        step = login_step(url, text)

        if step == "done":
            return True

        if step == "click_login":
            await _click_first(page, LOGIN_CTA_SELECTORS)
            await page.wait_for_timeout(1500)

        elif step == "choose_institution":
            await _click_first(page, INSTITUTION_SELECTORS)
            await page.wait_for_timeout(1500)

        elif step == "fill_cwl":
            # SAFETY: only ever type the password on the genuine UBC CWL host.
            if CWL_HOST in url.lower():
                await _fill_first(page, USERNAME_SELECTORS, user)
                await _fill_first(page, PASSWORD_SELECTORS, pwd)
                await _click_first(page, SUBMIT_SELECTORS)
                await page.wait_for_timeout(2500)
            else:
                await page.wait_for_timeout(1500)

        elif step == "duo_trust":
            sel = DUO_TRUST_SELECTORS if auth_config.trust_device else DUO_NOTRUST_SELECTORS
            await _click_first(page, sel)
            await page.wait_for_timeout(2500)

        elif step == "duo_wait":
            if not duo_notified:
                notifier.send("duo_approve", "Approve the Duo push on your phone now.")
                duo_notified = True
            await page.wait_for_timeout(3000)

        else:  # "wait" — e.g. landed back on PrairieLearn; nudge to PrairieTest.
            if "prairielearn.com" in url.lower():
                try:
                    await page.goto(PRAIRIETEST_HOME, wait_until="domcontentloaded")
                except Exception:  # noqa: BLE001
                    await page.wait_for_timeout(2000)
            else:
                await page.wait_for_timeout(2000)

    await _dump(page, debug_dir, "login_timeout")
    return False
