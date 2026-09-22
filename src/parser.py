from __future__ import annotations
import re
from datetime import datetime
from bs4 import BeautifulSoup
from src.models import (
    Session, clean_room, RESERVABLE, NO_SEATS, TIME_LIMIT, NOT_RESERVABLE,
)

_STATUS_LABELS = [
    ("No available seats", NO_SEATS),
    ("Time limit doesn't fit", TIME_LIMIT),
    ("Not reservable for this exam", NOT_RESERVABLE),
]
_AVAIL_RE = re.compile(r"Available:\s*(\d+)\s*/\s*(\d+)")
_ATTRS_RE = re.compile(r"(\d+h\s*\d+min.*?time)")

def parse_sessions(html: str) -> list[Session]:
    soup = BeautifulSoup(html, "lxml")
    sessions: list[Session] = []
    for li in soup.select("ul.list-group li.list-group-item"):
        sessions.append(_parse_row(li))
    return sessions

def _parse_row(li) -> Session:
    text = li.get_text(" ", strip=True)

    reserve_btn = None
    for b in li.select("button.btn-success"):
        if b.get_text(strip=True).startswith("Reserve this session"):
            reserve_btn = b
            break

    if reserve_btn is not None:
        status = RESERVABLE
        name = reserve_btn.get("aria-label") or reserve_btn.get_text(strip=True)
    else:
        status, name = NOT_RESERVABLE, None
        for label, val in _STATUS_LABELS:
            if label in text:
                status = val
                break

    time_el = li.find("time")
    start = datetime.fromisoformat(time_el["datetime"])

    room_link = li.select_one('a[href*="maps.ubc.ca"], a[href*="learningspaces"]')
    room = room_link.get_text(strip=True) if room_link else ""
    loc_text = room_link.parent.get_text(" ", strip=True) if room_link else ""
    center = loc_text.split(":")[0].strip() if ":" in loc_text else ""

    avail_m = _AVAIL_RE.search(text)
    available = int(avail_m.group(1)) if avail_m else 0
    capacity = int(avail_m.group(2)) if avail_m else 0

    attrs_m = _ATTRS_RE.search(li.get_text("\n"))
    attributes = attrs_m.group(1).strip() if attrs_m else ""

    return Session(
        status=status, start=start, center=center, room=room,
        room_clean=clean_room(room), attributes=attributes,
        available=available, capacity=capacity,
        reserve_button_name=name,
    )
