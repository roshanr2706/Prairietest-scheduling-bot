from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from src.models import Session

log = logging.getLogger(__name__)

BOOKED = "booked"
DRY_RUN = "dry_run"
TAKEN = "taken"
FAILED = "failed"

# Observed live (2026-09-22): clicking "Reserve this session" books immediately
# and lands on the reservation confirmation page — there is NO confirm dialog.
# We still look for a confirmation control defensively (harmless if absent).
_CONFIRM_NAMES = ["Confirm", "Confirm reservation", "Yes, reserve"]

# Text that appears on the reservation confirmation page after a successful book.
_BOOKED_MARKERS = ("scheduled to start", "exam reservation", "change or delete this reservation")

@dataclass
class BookResult:
    outcome: str
    detail: str

def is_booking_confirmed(page_text: str) -> bool:
    """True if the page text looks like the post-booking reservation page."""
    low = (page_text or "").lower()
    return any(m in low for m in _BOOKED_MARKERS)

async def _dump(page, tag: str, debug_dir: str = "data/debug") -> None:
    try:
        d = Path(debug_dir)
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        await page.screenshot(path=str(d / f"{tag}-{stamp}.png"))
    except Exception:  # noqa: BLE001
        log.warning("could not write debug dump for %s", tag)

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

    # Defensive: click a confirmation control if one ever appears (none observed).
    for cname in _CONFIRM_NAMES:
        try:
            cbtn = page.get_by_role("button", name=cname, exact=True)
            if await cbtn.count() > 0 and await cbtn.first.is_visible():
                await cbtn.first.click()
                break
        except Exception:  # noqa: BLE001
            pass

    # Verify by positively confirming the reservation page.
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    try:
        text = await page.inner_text("body")
    except Exception:  # noqa: BLE001
        try:
            text = await page.content()
        except Exception:  # noqa: BLE001
            text = ""
    if is_booking_confirmed(text):
        return BookResult(BOOKED, f"reserved: {session.room_clean} @ {session.start}")
    # Not confirmed — seat may have been taken between select and click, or the
    # flow changed. Capture evidence and report (the loop will re-scan).
    await _dump(page, "book_unconfirmed")
    return BookResult(TAKEN, f"reservation not confirmed (seat taken?): {session.room_clean} @ {session.start}")
