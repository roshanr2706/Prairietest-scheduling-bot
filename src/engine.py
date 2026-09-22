from __future__ import annotations
from dataclasses import dataclass
from src.models import Session
from src.parser import parse_sessions
from src.ranker import choose_session

@dataclass
class Decision:
    target_id: int
    exam_id: str
    exam_name: str
    session: Session

def plan_decisions(targets, discovered, page_html_by_id, already_booked, dry_run_by_target) -> list[Decision]:
    decisions: list[Decision] = []
    for target_id, exam in targets:
        for exam_id, exam_name in discovered.get(target_id, []):
            if already_booked(target_id, exam_id):
                continue
            html = page_html_by_id.get(exam_id)
            if not html:
                continue
            sessions = parse_sessions(html)
            chosen = choose_session(sessions, exam.preferences, exam.tiebreak, exam.min_seats)
            if chosen:
                decisions.append(Decision(target_id, exam_id, exam_name, chosen))
    return decisions


import asyncio
import logging
import os
import random
from src.config import AuthConfig
from src.notifier import Notifier
from src.booker import book, BOOKED, DRY_RUN
from src.watcher import (
    exam_url, matching_exams, is_logged_out, HOME, _new_logged_in, _relogin,
)
from playwright.async_api import async_playwright

log = logging.getLogger(__name__)

class _Cfg:
    """Adapts an AuthConfig to the .auth attribute expected by watcher helpers."""
    def __init__(self, auth):
        self.auth = auth

def auth_config_from_env() -> AuthConfig:
    return AuthConfig(
        username=os.environ.get("CWL_USERNAME"),
        password=os.environ.get("CWL_PASSWORD"),
        duo_wait_seconds=int(os.environ.get("DUO_WAIT_SECONDS", "120")),
        trust_device=os.environ.get("TRUST_DEVICE", "true").lower() != "false",
    )

def booking_message(cwl: str, exam_name: str, session) -> str:
    return (f"{cwl} has had {exam_name} booked at "
            f"{session.start.strftime('%Y-%m-%d %H:%M')} in {session.room_clean}")

def _poll_delay(db) -> float:
    interval = float(db.get_kv("poll_interval", "300"))
    jitter = float(db.get_kv("poll_jitter", "15"))
    return interval + random.uniform(0, jitter)

async def _discover_and_pages(page, targets):
    """Return (discovered per target, html per exam_id) for one cycle."""
    await page.goto(HOME, wait_until="domcontentloaded")
    home_html = await page.content()
    discovered, pages = {}, {}
    for target_id, exam in targets:
        matches = matching_exams(home_html, exam.match)
        discovered[target_id] = matches
        for exam_id, _ in matches:
            if exam_id not in pages:
                await page.goto(exam_url(exam_id), wait_until="domcontentloaded")
                pages[exam_id] = await page.content()
    return discovered, pages, home_html

async def run_engine(db, notifier: Notifier, storage_state_path: str, stop_event=None):
    auth_cfg = auth_config_from_env()
    cwl = auth_cfg.username or "user"
    db.set_kv("session_state", "connecting")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context, page = await _new_logged_in(browser, _Cfg(auth_cfg), notifier, storage_state_path)
            db.set_kv("session_state", "connected")
            db.add_event("info", "connected to PrairieTest")
        except Exception as e:  # noqa: BLE001
            db.set_kv("session_state", "auth_failed")
            db.add_event("error", f"login failed: {e}")
            notifier.send("auth_failed", f"login failed: {e}")
            return
        backoff = 0
        while stop_event is None or not stop_event.is_set():
            try:
                if db.get_kv("watch_running", "1") != "1":
                    await asyncio.sleep(5)
                    continue
                enabled = db.list_targets(enabled_only=True)
                targets = [(r["id"], db.to_target_exam(r["id"])) for r in enabled]
                if not targets:
                    await asyncio.sleep(_poll_delay(db))
                    continue
                discovered, pages, _ = await _discover_and_pages(page, targets)
                if is_logged_out(await page.content(), page.url):
                    db.set_kv("session_state", "connecting")
                    if await _relogin(page, context, _Cfg(auth_cfg), notifier, storage_state_path):
                        db.set_kv("session_state", "connected")
                    await asyncio.sleep(10)
                    continue
                dry_by = {r["id"]: bool(r["dry_run"]) for r in enabled}
                decisions = plan_decisions(
                    targets, discovered, pages,
                    already_booked=db.already_booked, dry_run_by_target=dry_by,
                )
                for d in decisions:
                    dry = dry_by.get(d.target_id, True)
                    await page.goto(exam_url(d.exam_id), wait_until="domcontentloaded")
                    result = await book(page, d.session, dry)
                    if result.outcome == BOOKED:
                        db.record_booking(d.target_id, d.exam_id, d.exam_name,
                                          d.session.room_clean, d.session.start.isoformat(),
                                          cwl, dry_run=False)
                        msg = booking_message(cwl, d.exam_name, d.session)
                        db.add_event("info", msg)
                        notifier.send("booked", msg)
                    elif result.outcome == DRY_RUN:
                        db.record_booking(d.target_id, d.exam_id, d.exam_name,
                                          d.session.room_clean, d.session.start.isoformat(),
                                          cwl, dry_run=True)
                        db.add_event("info", f"[dry_run] would book {d.exam_name}: {result.detail}")
                    else:
                        db.add_event("warn", f"{d.exam_name}: {result.outcome} {result.detail}")
                backoff = 0
            except Exception as e:  # noqa: BLE001
                backoff = min((backoff or 1) * 2, 120)
                log.exception("engine tick error")
                db.add_event("error", f"tick error: {e}; backoff {backoff}s")
                await asyncio.sleep(backoff)
            await asyncio.sleep(_poll_delay(db))
        await browser.close()
