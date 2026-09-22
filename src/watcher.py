from __future__ import annotations
import random
import re
from datetime import datetime
from bs4 import BeautifulSoup
from src.config import PollConfig

BASE = "https://us.prairietest.com"
_EXAM_HREF = re.compile(r"/pt/student/exam/(\d+)")
_LOGOUT_URL_MARKERS = ("authentication.ubc.ca", "/login", "cwl",
                       "duosecurity.com", "duo.com", "prairielearn.com")
_LOGOUT_HTML_MARKERS = ("Sign in with your CWL", "CWL Login", "Campus-Wide Login",
                        "An exam proctoring system")
_LOGGED_IN_MARKERS = ("Exams available for reservations", "PrairieTest Homepage",
                      "Exam reservations", "Exam information", "Choose a session",
                      "Choose a new session", "Delete this reservation",
                      "scheduled to start")

def exam_url(exam_id: str) -> str:
    return f"{BASE}/pt/student/exam/{exam_id}"

_ROW_CLASS_HINTS = ("list-group-item", "card", "row")

def _exam_search_text(a) -> str:
    """Text to match an exam link against.

    On "available for reservations" rows the <a> text is just the button label
    ("Make a reservation"); the exam name lives in the link's aria-label or a
    sibling cell. So we combine the link text, its aria-label, and the text of
    the nearest enclosing row/card (bounded, never the whole page)."""
    parts = [a.get_text(" ", strip=True)]
    aria = a.get("aria-label")
    if aria:
        parts.append(aria)
    node, depth, row = a.parent, 0, None
    while node is not None and node.name not in ("body", "html", "[document]") and depth < 5:
        cls = " ".join(node.get("class", []) or [])
        if any(k in cls for k in _ROW_CLASS_HINTS):
            row = node
            break
        node, depth = node.parent, depth + 1
    if row is not None:
        parts.append(row.get_text(" ", strip=True))
    return " ".join(p for p in parts if p)

def _exam_name(a) -> str:
    return a.get("aria-label") or a.get_text(" ", strip=True)

def discover_exam_id(home_html: str, match: re.Pattern) -> str | None:
    m = matching_exams(home_html, match)
    return m[0][0] if m else None

def matching_exams(home_html: str, pattern) -> list[tuple[str, str]]:
    soup = BeautifulSoup(home_html, "lxml")
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=_EXAM_HREF):
        if not pattern.search(_exam_search_text(a)):
            continue
        exam_id = _EXAM_HREF.search(a["href"]).group(1)
        if exam_id in seen:
            continue
        seen.add(exam_id)
        out.append((exam_id, _exam_name(a)))
    return out

def is_logged_out(html: str, url: str) -> bool:
    u = (url or "").lower()
    if any(m in u for m in _LOGOUT_URL_MARKERS):
        return True
    if any(m in html for m in _LOGGED_IN_MARKERS):
        return False
    return any(m in html for m in _LOGOUT_HTML_MARKERS)

def next_interval(now: datetime, poll: PollConfig) -> float:
    if poll.open_time is not None:
        delta = (poll.open_time - now).total_seconds()
        if 0 <= delta <= 60:
            return poll.ramp_interval_seconds
    if poll.jitter_seconds:
        return poll.interval_seconds + random.uniform(0, poll.jitter_seconds)
    return poll.interval_seconds


import asyncio
import logging
from playwright.async_api import async_playwright
from src.parser import parse_sessions
from src.ranker import choose_session
from src.booker import book, BOOKED, DRY_RUN, TAKEN
from src.auth import auto_login, session_exists, AuthError

log = logging.getLogger(__name__)
HOME = f"{BASE}/pt"

async def _new_logged_in(browser, config, notifier, storage_path: str):
    """Return (context, page) that is logged in, reusing a saved session or
    performing an auto-login (which is then persisted to storage_path)."""
    if session_exists(storage_path):
        context = await browser.new_context(storage_state=storage_path)
        page = await context.new_page()
        await page.goto(HOME, wait_until="domcontentloaded")
        if not is_logged_out(await page.content(), page.url):
            return context, page
        # Session cookies stale, but the Duo device-trust cookie in this context
        # may still let us skip Duo — reuse it for the login attempt.
    else:
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(HOME, wait_until="domcontentloaded")

    if not await auto_login(page, config.auth, notifier):
        raise AuthError("auto-login failed")
    await context.storage_state(path=storage_path)
    notifier.send("logged_in", "session established and saved")
    return context, page

async def _relogin(page, context, config, notifier, storage_path: str) -> bool:
    await page.goto(HOME, wait_until="domcontentloaded")
    if not is_logged_out(await page.content(), page.url):
        return True
    if not await auto_login(page, config.auth, notifier):
        return False
    await context.storage_state(path=storage_path)
    notifier.send("logged_in", "re-established session")
    return True

async def _resolve_exam_id(page, exam):
    if exam.exam_id:
        return exam.exam_id
    await page.goto(HOME, wait_until="domcontentloaded")
    return discover_exam_id(await page.content(), exam.match)

async def run_watch(config, notifier, storage_state_path: str):
    notifier.send("started", f"watching {len(config.target_exams)} exam(s), dry_run={config.dry_run}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context, page = await _new_logged_in(browser, config, notifier, storage_state_path)
        remaining = list(config.target_exams)
        backoff = 0
        while remaining:
            for exam in list(remaining):
                try:
                    exam_id = await _resolve_exam_id(page, exam)
                    if not exam_id:
                        continue  # not open for reservation yet
                    await page.goto(exam_url(exam_id), wait_until="domcontentloaded")
                    html, url = await page.content(), page.url
                    if is_logged_out(html, url):
                        notifier.send("session_invalid", "logged out; attempting re-login")
                        if not await _relogin(page, context, config, notifier, storage_state_path):
                            notifier.send("auth_failed", "re-login failed; backing off 60s")
                            await asyncio.sleep(60)
                        continue
                    sessions = parse_sessions(html)
                    chosen = choose_session(sessions, exam.preferences, exam.tiebreak, exam.min_seats)
                    if not chosen:
                        continue
                    notifier.send("exam_detected", f"{exam.match.pattern}: candidate {chosen.room_clean} @ {chosen.start}")
                    result = await book(page, chosen, config.dry_run)
                    notifier.send(result.outcome, result.detail)
                    if result.outcome in (BOOKED, DRY_RUN):
                        remaining.remove(exam)
                    # TAKEN/FAILED: keep polling this exam
                    backoff = 0
                except Exception as e:  # noqa: BLE001
                    backoff = min((backoff or 1) * 2, 120)
                    log.exception("tick error")
                    notifier.send("error", f"tick error: {e}; backing off {backoff}s")
                    await asyncio.sleep(backoff)
            if remaining:
                await asyncio.sleep(next_interval(datetime.now(), config.poll))
        notifier.send("done", "all target exams handled")
        await browser.close()
