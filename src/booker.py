from __future__ import annotations
import logging
from dataclasses import dataclass
from src.models import Session

log = logging.getLogger(__name__)

BOOKED = "booked"
DRY_RUN = "dry_run"
TAKEN = "taken"
FAILED = "failed"

# Defensive: names a confirmation step might use if one appears.
_CONFIRM_NAMES = ["Confirm", "Confirm reservation", "Yes, reserve", "Reserve"]

@dataclass
class BookResult:
    outcome: str
    detail: str

async def book(page, session: Session, dry_run: bool) -> BookResult:
    name = session.reserve_button_name
    if not name or not name.startswith("Reserve this session on"):
        return BookResult(FAILED, f"unsafe/missing button name: {name!r}")

    if dry_run:
        log.info("[dry_run] would click: %s", name)
        return BookResult(DRY_RUN, f"would reserve: {session.room_clean} @ {session.start} ({name})")

    try:
        btn = page.get_by_role("button", name=name, exact=True)
        if await btn.count() == 0:
            return BookResult(TAKEN, f"button gone (seat taken?): {name}")
        await btn.first.click()
    except Exception as e:  # noqa: BLE001 - report, don't crash the loop
        return BookResult(FAILED, f"click error: {e}")

    # Handle an optional confirmation step defensively.
    for cname in _CONFIRM_NAMES:
        try:
            cbtn = page.get_by_role("button", name=cname, exact=True)
            if await cbtn.count() > 0 and await cbtn.first.is_visible():
                await cbtn.first.click()
                break
        except Exception:  # noqa: BLE001
            pass

    # Verify: the exam page should now reflect the reservation.
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
        body = (await page.content()).lower()
        if "you don't currently have any" in body:
            return BookResult(FAILED, "no confirmation detected after click")
        return BookResult(BOOKED, f"reserved: {session.room_clean} @ {session.start}")
    except Exception as e:  # noqa: BLE001
        return BookResult(FAILED, f"verify error: {e}")
