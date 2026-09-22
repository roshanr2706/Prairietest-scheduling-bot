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
