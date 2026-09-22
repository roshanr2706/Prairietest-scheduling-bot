from __future__ import annotations
import random
import re
from datetime import datetime
from bs4 import BeautifulSoup
from src.config import PollConfig

BASE = "https://us.prairietest.com"
_EXAM_HREF = re.compile(r"/pt/student/exam/(\d+)")
_LOGOUT_URL_MARKERS = ("authentication.ubc.ca", "/login", "cwl")
_LOGOUT_HTML_MARKERS = ("Sign in with your CWL", "CWL Login", "Campus-Wide Login")

def exam_url(exam_id: str) -> str:
    return f"{BASE}/pt/student/exam/{exam_id}"

def discover_exam_id(home_html: str, match: re.Pattern) -> str | None:
    soup = BeautifulSoup(home_html, "lxml")
    for a in soup.find_all("a", href=_EXAM_HREF):
        if match.search(a.get_text(" ", strip=True)):
            return _EXAM_HREF.search(a["href"]).group(1)
    return None

def is_logged_out(html: str, url: str) -> bool:
    u = (url or "").lower()
    if any(m in u for m in _LOGOUT_URL_MARKERS):
        return True
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

log = logging.getLogger(__name__)
HOME = f"{BASE}/pt"

async def _resolve_exam_id(page, exam):
    if exam.exam_id:
        return exam.exam_id
    await page.goto(HOME, wait_until="domcontentloaded")
    return discover_exam_id(await page.content(), exam.match)

async def run_watch(config, notifier, storage_state_path: str):
    notifier.send("started", f"watching {len(config.target_exams)} exam(s), dry_run={config.dry_run}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=storage_state_path)
        page = await context.new_page()
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
                        notifier.send("session_invalid", "logged out; re-seed storageState.json")
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
