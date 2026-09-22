from __future__ import annotations
import os
import re
from dataclasses import dataclass
from datetime import datetime, time, date
import yaml

VALID_TIEBREAKS = {"earliest", "latest", "most_seats"}

class ConfigError(Exception):
    pass

@dataclass
class PreferenceRule:
    name: str
    location: re.Pattern | None
    time_start: time | None
    time_end: time | None
    date_start: date | None
    date_end: date | None
    weekdays: set[str] | None

@dataclass
class TargetExam:
    match: re.Pattern
    exam_id: str | None
    min_seats: int
    tiebreak: str
    preferences: list[PreferenceRule]

@dataclass
class PollConfig:
    interval_seconds: int
    jitter_seconds: int
    open_time: datetime | None
    ramp_interval_seconds: int

@dataclass
class NotifyConfig:
    webhook_url: str | None

@dataclass
class AuthConfig:
    username: str | None
    password: str | None
    duo_wait_seconds: int
    trust_device: bool

@dataclass
class AppConfig:
    target_exams: list[TargetExam]
    poll: PollConfig
    notify: NotifyConfig
    auth: AuthConfig
    dry_run: bool

_ENV_RE = re.compile(r"^\$\{([A-Z0-9_]+)\}$")

def _expand(value):
    if isinstance(value, str):
        m = _ENV_RE.match(value.strip())
        if m:
            return os.environ.get(m.group(1))
    return value

def _compile_regex(pattern: str) -> re.Pattern:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise ConfigError(f"Invalid regex {pattern!r}: {e}")

def _parse_time_range(raw):
    m = re.match(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$", raw or "")
    if not m:
        raise ConfigError(f"Invalid time_range {raw!r}; expected HH:MM-HH:MM")
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    return time(h1, m1), time(h2, m2)

def _parse_date_range(raw):
    if ".." in raw:
        a, b = raw.split("..", 1)
        return date.fromisoformat(a.strip()), date.fromisoformat(b.strip())
    d = date.fromisoformat(raw.strip())
    return d, d

def _rule(d: dict) -> PreferenceRule:
    loc = _compile_regex(d["location"]) if d.get("location") is not None else None
    ts = te = None
    if d.get("time_range"):
        ts, te = _parse_time_range(d["time_range"])
    ds = de = None
    if d.get("date_range"):
        ds, de = _parse_date_range(d["date_range"])
    elif d.get("date"):
        ds = de = date.fromisoformat(str(d["date"]))
    wd = set(d["weekdays"]) if d.get("weekdays") else None
    return PreferenceRule(d.get("name", "rule"), loc, ts, te, ds, de, wd)

def load_config(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping")

    exams = []
    for e in raw.get("target_exams", []):
        tiebreak = e.get("tiebreak", "earliest")
        if tiebreak not in VALID_TIEBREAKS:
            raise ConfigError(f"Invalid tiebreak {tiebreak!r}; must be one of {sorted(VALID_TIEBREAKS)}")
        exams.append(TargetExam(
            match=_compile_regex(e["match"]),
            exam_id=str(e["exam_id"]) if e.get("exam_id") else None,
            min_seats=int(e.get("min_seats", 1)),
            tiebreak=tiebreak,
            preferences=[_rule(r) for r in e.get("preferences", [])],
        ))
    if not exams:
        raise ConfigError("At least one target_exam is required")

    p = raw.get("poll", {})
    ot = p.get("open_time")
    poll = PollConfig(
        interval_seconds=int(p.get("interval_seconds", 15)),
        jitter_seconds=int(p.get("jitter_seconds", 5)),
        open_time=datetime.fromisoformat(ot) if ot else None,
        ramp_interval_seconds=int(p.get("ramp_interval_seconds", 2)),
    )
    notify = NotifyConfig(webhook_url=_expand(raw.get("notify", {}).get("webhook_url")))

    a = raw.get("auth", {}) or {}
    auth = AuthConfig(
        username=_expand(a.get("username")),
        password=_expand(a.get("password")),
        duo_wait_seconds=int(a.get("duo_wait_seconds", 120)),
        trust_device=bool(a.get("trust_device", True)),
    )
    return AppConfig(exams, poll, notify, auth, bool(raw.get("dry_run", True)))
