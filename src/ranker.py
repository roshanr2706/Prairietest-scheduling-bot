from __future__ import annotations
from src.models import Session, RESERVABLE
from src.config import PreferenceRule

_WEEKDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

def _matches(rule: PreferenceRule, s: Session) -> bool:
    if rule.location is not None and not rule.location.search(s.room_clean):
        return False
    if rule.time_start is not None and not (rule.time_start <= s.start.time() <= rule.time_end):
        return False
    if rule.date_start is not None and not (rule.date_start <= s.start.date() <= rule.date_end):
        return False
    if rule.weekdays is not None and _WEEKDAY[s.start.weekday()] not in rule.weekdays:
        return False
    return True

def _pick(candidates: list[Session], tiebreak: str) -> Session:
    if tiebreak == "latest":
        return max(candidates, key=lambda s: s.start)
    if tiebreak == "most_seats":
        return max(candidates, key=lambda s: (s.available, -s.start.timestamp()))
    return min(candidates, key=lambda s: s.start)  # earliest (default)

def choose_session(sessions, rules, tiebreak: str, min_seats: int) -> Session | None:
    bookable = [s for s in sessions if s.status == RESERVABLE and s.available >= min_seats]
    for rule in rules:
        matching = [s for s in bookable if _matches(rule, s)]
        if matching:
            return _pick(matching, tiebreak)
    return None
