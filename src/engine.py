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
from src.watcher import exam_url, matching_exams, is_logged_out, HOME
from src.auth import auto_login, session_exists
from playwright.async_api import async_playwright

log = logging.getLogger(__name__)

def next_login_action(session_state: str) -> str:
    """Decide the loop's next move from the persisted session state.

    - 'connected'  -> run a watch cycle
    - 'connecting' -> attempt login now (this is what a UI Connect triggers,
                      and what fires the Duo push)
    - anything else ('not_connected', 'auth_failed', ...) -> wait for Connect
    """
    if session_state == "connected":
        return "watch"
    if session_state == "connecting":
        return "login"
    return "wait"

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

async def _establish_startup_state(db, page, storage_state_path):
    """Silently reuse a saved session if it is still valid; never triggers Duo.

    Leaves session_state 'connected' when the saved session works, else
    'not_connected' so the loop waits for the user to click Connect.
    """
    await page.goto(HOME, wait_until="domcontentloaded")
    if session_exists(storage_state_path) and not is_logged_out(await page.content(), page.url):
        db.set_kv("session_state", "connected")
        db.add_event("info", "reused saved PrairieTest session")
    else:
        db.set_kv("session_state", "not_connected")
        db.add_event("info", "not connected — click Connect to sign in (approve Duo on your phone)")

async def _do_login(db, context, page, auth_cfg, notifier, storage_state_path) -> bool:
    db.add_event("info", "connecting — approve the Duo push on your phone")
    await page.goto(HOME, wait_until="domcontentloaded")
    try:
        ok = await auto_login(page, auth_cfg, notifier)
    except Exception as e:  # noqa: BLE001
        log.exception("login error")
        db.add_event("error", f"login error: {e}")
        ok = False
    if ok:
        await context.storage_state(path=storage_state_path)
        db.set_kv("session_state", "connected")
        db.add_event("info", "connected to PrairieTest")
    else:
        db.set_kv("session_state", "auth_failed")
        db.add_event("error", "login failed — click Connect to retry (see data/debug/)")
        notifier.send("auth_failed", "login failed")
    return ok

async def run_engine(db, notifier: Notifier, storage_state_path: str, stop_event=None):
    auth_cfg = auth_config_from_env()
    cwl = auth_cfg.username or "user"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Reuse a valid saved session if present; otherwise wait for Connect.
        # A fresh context loads the saved cookies only when the file exists.
        kwargs = {"storage_state": storage_state_path} if session_exists(storage_state_path) else {}
        context = await browser.new_context(**kwargs)
        page = await context.new_page()
        await _establish_startup_state(db, page, storage_state_path)
        backoff = 0
        while stop_event is None or not stop_event.is_set():
            try:
                action = next_login_action(db.get_kv("session_state", "not_connected"))
                if action == "wait":
                    await asyncio.sleep(3)
                    continue
                if action == "login":
                    await _do_login(db, context, page, auth_cfg, notifier, storage_state_path)
                    continue
                # action == "watch"
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
                    # Session expired mid-run: flip to 'connecting' so the loop's
                    # login action re-authenticates on the next pass (Duo device
                    # trust usually skips the push).
                    db.set_kv("session_state", "connecting")
                    db.add_event("warn", "session expired; re-authenticating")
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
