from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import datetime

RESERVABLE = "reservable"
NO_SEATS = "no_seats"
TIME_LIMIT = "time_limit"
NOT_RESERVABLE = "not_reservable"

def clean_room(raw: str) -> str:
    """Strip leading non-alphanumeric chars (emoji) and surrounding whitespace."""
    return re.sub(r"^[^A-Za-z0-9]+", "", raw.strip()).strip()

@dataclass
class Session:
    status: str
    start: datetime
    center: str
    room: str
    room_clean: str
    attributes: str
    available: int
    capacity: int
    reserve_button_name: str | None = None
