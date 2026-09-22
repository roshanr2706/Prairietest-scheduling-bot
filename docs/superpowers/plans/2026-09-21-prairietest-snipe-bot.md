# PrairieTest Snipe Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a headless, Docker-deployable bot that reuses a saved PrairieTest session, polls a target exam, and books the best open seat by ranked range/regex preferences, notifying via webhook.

**Architecture:** Small single-purpose Python modules. Pure logic (`models`, `parser`, `ranker`, `config`, `notifier`) is unit-tested offline against a captured HTML fixture. Playwright-driven modules (`seed_session`, `booker`, `watcher`, `main`) wire the pure logic to the live site; their pure helpers are unit-tested and the browser flow is validated with `dry_run`.

**Tech Stack:** Python 3.11+, Playwright (async), BeautifulSoup4 + lxml (parsing), PyYAML (config), requests (webhooks), pytest (tests), Docker.

## Global Constraints

- Python 3.11+.
- Never store a CWL password; never automate/approve Duo; never click `Delete this reservation` / `button.btn-danger` / anything in `#deleteReservationModal`.
- Only ever click reserve controls matched to the chosen slot: `button.btn.btn-success` whose accessible name starts with `Reserve this session on`.
- `dry_run: true` must fully detect + rank + log but never submit a reservation.
- Secrets (webhook URL) come from env only; never commit `.env`, `data/`, or `storageState.json` (already in `.gitignore`).
- Base Docker image: `mcr.microsoft.com/playwright/python:v1.55.0-jammy` (browsers preinstalled).
- Session statuses (exact strings on the site): `Reserve this session`, `No available seats`, `Time limit doesn't fit`, `Not reservable for this exam`.

---

## File Structure

- `src/__init__.py` — package marker.
- `src/models.py` — `Session` dataclass + status constants + `clean_room()`.
- `src/parser.py` — `parse_sessions(html) -> list[Session]` (pure).
- `src/config.py` — config dataclasses + `load_config(path) -> AppConfig` + validation.
- `src/ranker.py` — `choose_session(sessions, rules, tiebreak, min_seats) -> Session | None` (pure).
- `src/notifier.py` — `Notifier` webhook client.
- `src/booker.py` — Playwright booking (`book(page, session, dry_run) -> BookResult`).
- `src/watcher.py` — poll loop, discovery, session guard, ramp helper.
- `src/main.py` — wiring, signals, backoff.
- `seed_session.py` — interactive session seeder (run on host).
- `tests/fixtures/sessions_fixture.html` — sanitized slot-list HTML (all 4 statuses).
- `tests/test_models.py`, `tests/test_parser.py`, `tests/test_config.py`, `tests/test_ranker.py`, `tests/test_notifier.py`, `tests/test_watcher.py`.
- `config.example.yaml`, `requirements.txt`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`.

---

## Task 1: Project setup + `Session` model

**Files:**
- Create: `requirements.txt`, `pytest.ini`, `src/__init__.py`, `src/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `Session` dataclass with fields `status: str, start: datetime, center: str, room: str, room_clean: str, attributes: str, available: int, capacity: int, reserve_button_name: str | None`; status constants `RESERVABLE, NO_SEATS, TIME_LIMIT, NOT_RESERVABLE`; `clean_room(raw: str) -> str`.

- [ ] **Step 1: Create dependency + pytest files**

`requirements.txt`:
```
playwright==1.55.0
beautifulsoup4==4.12.3
lxml==5.3.0
PyYAML==6.0.2
requests==2.32.3
```
`pytest.ini`:
```ini
[pytest]
pythonpath = .
testpaths = tests
```
`src/__init__.py`: (empty file)

- [ ] **Step 2: Write the failing test**

`tests/test_models.py`:
```python
from datetime import datetime
from src.models import Session, clean_room, RESERVABLE

def test_clean_room_strips_leading_emoji_and_space():
    assert clean_room("💥HENN 203") == "HENN 203"
    assert clean_room("🎯ICCS 014") == "ICCS 014"
    assert clean_room("  ⛳️ICCS 008 ") == "ICCS 008"
    assert clean_room("BUCH B101") == "BUCH B101"

def test_session_construction():
    s = Session(
        status=RESERVABLE,
        start=datetime(2026, 9, 29, 11, 0),
        center="ORCA", room="💥HENN 203", room_clean="HENN 203",
        attributes="1h 15min, In-person, CfA, 150% time",
        available=4, capacity=14,
        reserve_button_name="Reserve this session on Tue, Sep 29, 11am (PDT) in ORCA: 💥HENN 203",
    )
    assert s.available == 4 and s.room_clean == "HENN 203"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.models'`

- [ ] **Step 4: Write minimal implementation**

`src/models.py`:
```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add requirements.txt pytest.ini src/__init__.py src/models.py tests/test_models.py
git commit -m "feat: project setup and Session model"
```

---

## Task 2: HTML parser + fixture

**Files:**
- Create: `src/parser.py`, `tests/fixtures/sessions_fixture.html`
- Test: `tests/test_parser.py`

**Interfaces:**
- Consumes: `Session`, status constants, `clean_room` from `src.models`.
- Produces: `parse_sessions(html: str) -> list[Session]`. Reads each `li.list-group-item`; `RESERVABLE` iff it contains a `button.btn-success` whose text starts with "Reserve this session" (its `aria-label` becomes `reserve_button_name`); otherwise status is mapped from the row's visible label. `start` comes from the `<time datetime=...>` ISO attribute. Ignores `#deleteReservationModal`.

- [ ] **Step 1: Create the fixture**

`tests/fixtures/sessions_fixture.html`:
```html
<main>
  <ul class="list-group list-group-flush">
    <li class="list-group-item ">
      <div class="container p-0"><div class="row align-items-center">
        <div class="col-xxl-2 col-lg-4 col-sm-6 order-last order-sm-first">
          <form method="POST">
            <input type="hidden" name="__action" value="reserve">
            <input type="hidden" name="reservation_request_id" value="dummy">
            <input type="hidden" name="unsafe_session_id" value="dummy">
            <input type="hidden" name="__csrf_token" value="dummy">
            <button class="btn btn-success btn-sm" type="submit"
              aria-label="Reserve this session on Tue, Sep 29, 11am (PDT) in ORCA: 💥HENN 203">Reserve this session</button>
          </form>
        </div>
        <div class="col"><time class="js-format-date-friendly-live-update" datetime="2026-09-29T11:00:00-07:00">Tue, Sep 29, 11am (PDT)</time></div>
        <div class="col"><span>ORCA: <a href="https://learningspaces.ubc.ca/x">💥HENN 203</a></span></div>
        <div class="col">1h 15min, In-person, CfA, 150% time</div>
        <div class="col">Available: 4 / 14</div>
      </div></div>
    </li>
    <li class="list-group-item ">
      <div class="container p-0"><div class="row align-items-center">
        <div class="col"><span class="text-muted">No available seats</span></div>
        <div class="col"><time datetime="2026-09-29T10:00:00-07:00">Tue, Sep 29, 10am (PDT)</time></div>
        <div class="col"><span>ORCA: <a href="https://maps.ubc.ca/?code=ICCS">⛳️ICCS 008</a></span></div>
        <div class="col">1h 15min, In-person, 150% time</div>
        <div class="col">Available: 0 / 42</div>
      </div></div>
    </li>
    <li class="list-group-item ">
      <div class="container p-0"><div class="row align-items-center">
        <div class="col"><span class="text-muted">Time limit doesn't fit</span></div>
        <div class="col"><time datetime="2026-09-29T18:00:00-07:00">Tue, Sep 29, 6pm (PDT)</time></div>
        <div class="col"><span>ORCA: <a href="https://maps.ubc.ca/?code=ICCS">🎯ICCS 014</a></span></div>
        <div class="col">1h 15min, In-person, 150% time</div>
        <div class="col">Available: 0 / 0</div>
      </div></div>
    </li>
    <li class="list-group-item ">
      <div class="container p-0"><div class="row align-items-center">
        <div class="col"><span class="text-muted">Not reservable for this exam</span></div>
        <div class="col"><time datetime="2026-09-30T11:00:00-07:00">Wed, Sep 30, 11am (PDT)</time></div>
        <div class="col"><span>ORCA: <a href="https://maps.ubc.ca/?code=ICCS">🎯ICCS 014</a></span></div>
        <div class="col">1h 15min, In-person, 150% time</div>
        <div class="col">Available: 0 / 0</div>
      </div></div>
    </li>
    <li class="list-group-item ">
      <div class="container p-0"><div class="row align-items-center">
        <div class="col"><form method="POST">
            <button class="btn btn-success btn-sm" type="submit"
              aria-label="Reserve this session on Sat, Oct 3, 3pm (PDT) in ORCA: ⛳️ICCS 008">Reserve this session</button>
          </form></div>
        <div class="col"><time datetime="2026-10-03T15:00:00-07:00">Sat, Oct 3, 3pm (PDT)</time></div>
        <div class="col"><span>ORCA: <a href="https://maps.ubc.ca/?code=ICCS">⛳️ICCS 008</a></span></div>
        <div class="col">1h 15min, In-person, 150% time</div>
        <div class="col">Available: 33 / 44</div>
      </div></div>
    </li>
  </ul>
  <div class="modal fade" id="deleteReservationModal"><div class="modal-dialog"><div class="modal-content">
    <div class="modal-footer"><button class="btn btn-danger" type="submit" aria-label="Delete reservation">Delete reservation</button></div>
  </div></div></div>
</main>
```

- [ ] **Step 2: Write the failing test**

`tests/test_parser.py`:
```python
from pathlib import Path
from src.parser import parse_sessions
from src.models import RESERVABLE, NO_SEATS, TIME_LIMIT, NOT_RESERVABLE

HTML = (Path(__file__).parent / "fixtures" / "sessions_fixture.html").read_text(encoding="utf-8")

def test_parses_all_rows_ignoring_delete_modal():
    sessions = parse_sessions(HTML)
    assert len(sessions) == 5  # 5 li rows; modal excluded

def test_statuses_detected():
    s = parse_sessions(HTML)
    assert s[0].status == RESERVABLE
    assert s[1].status == NO_SEATS
    assert s[2].status == TIME_LIMIT
    assert s[3].status == NOT_RESERVABLE
    assert s[4].status == RESERVABLE

def test_reservable_fields():
    s = parse_sessions(HTML)[0]
    assert s.center == "ORCA"
    assert s.room_clean == "HENN 203"
    assert s.available == 4 and s.capacity == 14
    assert s.start.year == 2026 and s.start.hour == 11
    assert s.reserve_button_name.startswith("Reserve this session on Tue, Sep 29, 11am")
    assert "1h 15min" in s.attributes

def test_non_reservable_has_no_button_name():
    s = parse_sessions(HTML)[1]
    assert s.reserve_button_name is None

def test_never_returns_delete_button():
    for s in parse_sessions(HTML):
        assert s.reserve_button_name is None or "Delete" not in s.reserve_button_name
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.parser'`

- [ ] **Step 4: Write minimal implementation**

`src/parser.py`:
```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_parser.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add src/parser.py tests/test_parser.py tests/fixtures/sessions_fixture.html
git commit -m "feat: slot-list HTML parser with fixture"
```

---

## Task 3: Config loading + validation

**Files:**
- Create: `src/config.py`, `config.example.yaml`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing from other src modules.
- Produces:
  - `PreferenceRule(name: str, location: re.Pattern | None, time_start: time | None, time_end: time | None, date_start: date | None, date_end: date | None, weekdays: set[str] | None)`
  - `TargetExam(match: re.Pattern, exam_id: str | None, min_seats: int, tiebreak: str, preferences: list[PreferenceRule])`
  - `PollConfig(interval_seconds: int, jitter_seconds: int, open_time: datetime | None, ramp_interval_seconds: int)`
  - `NotifyConfig(webhook_url: str | None)`
  - `AppConfig(target_exams: list[TargetExam], poll: PollConfig, notify: NotifyConfig, dry_run: bool)`
  - `load_config(path: str) -> AppConfig` (expands `${ENV}` in strings; raises `ConfigError` on invalid input)
  - `ConfigError(Exception)`

- [ ] **Step 1: Create the example config**

`config.example.yaml`:
```yaml
target_exams:
  - match: "CPSC 313 Quiz 1"
    exam_id: null
    min_seats: 1
    tiebreak: earliest        # earliest | latest | most_seats
    preferences:
      - name: "ideal: ICCS afternoon"
        location: "ICCS"
        time_range: "15:00-17:00"
        date_range: "2026-10-01..2026-10-03"
      - name: "any ICCS 014/018"
        location: "ICCS 01[48]"
      - name: "HENN mornings"
        location: "HENN"
        time_range: "09:00-12:00"
      - name: "last resort: anything"
        location: ".*"
poll:
  interval_seconds: 15
  jitter_seconds: 5
  open_time: null
  ramp_interval_seconds: 2
notify:
  webhook_url: ${WEBHOOK_URL}
dry_run: true
```

- [ ] **Step 2: Write the failing test**

`tests/test_config.py`:
```python
import re
from datetime import time, date
import pytest
from src.config import load_config, ConfigError

def write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)

BASE = """
target_exams:
  - match: "CPSC 313 Quiz 1"
    min_seats: 2
    tiebreak: most_seats
    preferences:
      - name: "ideal"
        location: "ICCS 01[48]"
        time_range: "15:00-17:00"
        date_range: "2026-10-01..2026-10-03"
        weekdays: ["Sat", "Sun"]
      - name: "anything"
        location: ".*"
poll:
  interval_seconds: 15
  jitter_seconds: 5
  open_time: null
  ramp_interval_seconds: 2
notify:
  webhook_url: ${WEBHOOK_URL}
dry_run: true
"""

def test_loads_valid_config(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "https://hooks.example/abc")
    cfg = load_config(write(tmp_path, BASE))
    ex = cfg.target_exams[0]
    assert ex.min_seats == 2 and ex.tiebreak == "most_seats"
    assert ex.match.search("CPSC 313 Quiz 1")
    r0 = ex.preferences[0]
    assert r0.location.search("ICCS 014") and not r0.location.search("ICCS 008")
    assert r0.time_start == time(15, 0) and r0.time_end == time(17, 0)
    assert r0.date_start == date(2026, 10, 1) and r0.date_end == date(2026, 10, 3)
    assert r0.weekdays == {"Sat", "Sun"}
    assert cfg.notify.webhook_url == "https://hooks.example/abc"
    assert cfg.dry_run is True

def test_missing_env_leaves_placeholder_as_none(tmp_path, monkeypatch):
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    cfg = load_config(write(tmp_path, BASE))
    assert cfg.notify.webhook_url is None

def test_bad_regex_raises(tmp_path):
    bad = BASE.replace('location: ".*"', 'location: "ICCS[01"')
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, bad))

def test_bad_tiebreak_raises(tmp_path):
    bad = BASE.replace("tiebreak: most_seats", "tiebreak: soonest")
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, bad))

def test_bad_time_range_raises(tmp_path):
    bad = BASE.replace('time_range: "15:00-17:00"', 'time_range: "15-17"')
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, bad))
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.config'`

- [ ] **Step 4: Write minimal implementation**

`src/config.py`:
```python
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
class AppConfig:
    target_exams: list[TargetExam]
    poll: PollConfig
    notify: NotifyConfig
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
    return AppConfig(exams, poll, notify, bool(raw.get("dry_run", True)))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add src/config.py config.example.yaml tests/test_config.py
git commit -m "feat: config loading and validation"
```

---

## Task 4: Ranker (core selection logic)

**Files:**
- Create: `src/ranker.py`
- Test: `tests/test_ranker.py`

**Interfaces:**
- Consumes: `Session`, `RESERVABLE` from `src.models`; `PreferenceRule` from `src.config`.
- Produces: `choose_session(sessions: list[Session], rules: list[PreferenceRule], tiebreak: str, min_seats: int) -> Session | None`. Considers only `RESERVABLE` sessions with `available >= min_seats`. Walks `rules` in order; first rule with ≥1 matching session wins; ties broken by `tiebreak` (`earliest`/`latest`/`most_seats`). Returns `None` if nothing matches.

- [ ] **Step 1: Write the failing test**

`tests/test_ranker.py`:
```python
from datetime import datetime
import re
from src.models import Session, RESERVABLE, NO_SEATS
from src.config import PreferenceRule
from src.ranker import choose_session

def S(room_clean, start, available=5, status=RESERVABLE):
    return Session(status=status, start=start, center="ORCA", room=room_clean,
                   room_clean=room_clean, attributes="", available=available,
                   capacity=50, reserve_button_name=f"Reserve this session on X in ORCA: {room_clean}")

def rule(name, location=None, ts=None, te=None):
    return PreferenceRule(name, re.compile(location, re.I) if location else None,
                          ts, te, None, None, None)

def test_first_matching_rule_wins():
    sessions = [
        S("HENN 203", datetime(2026,10,3,9,0)),
        S("ICCS 014", datetime(2026,10,3,15,0)),
    ]
    rules = [rule("iccs", "ICCS"), rule("any", ".*")]
    chosen = choose_session(sessions, rules, "earliest", 1)
    assert chosen.room_clean == "ICCS 014"

def test_falls_through_to_next_rule():
    sessions = [S("HENN 203", datetime(2026,10,3,9,0))]
    rules = [rule("iccs", "ICCS"), rule("any", ".*")]
    chosen = choose_session(sessions, rules, "earliest", 1)
    assert chosen.room_clean == "HENN 203"

def test_time_range_filter():
    from datetime import time
    sessions = [
        S("ICCS 014", datetime(2026,10,3,9,0)),
        S("ICCS 008", datetime(2026,10,3,16,0)),
    ]
    rules = [PreferenceRule("aft", re.compile("ICCS", re.I), time(15,0), time(17,0), None, None, None)]
    chosen = choose_session(sessions, rules, "earliest", 1)
    assert chosen.room_clean == "ICCS 008"

def test_tiebreak_earliest_vs_latest():
    sessions = [
        S("ICCS 014", datetime(2026,10,3,15,0)),
        S("ICCS 008", datetime(2026,10,3,17,0)),
    ]
    rules = [rule("iccs", "ICCS")]
    assert choose_session(sessions, rules, "earliest", 1).start.hour == 15
    assert choose_session(sessions, rules, "latest", 1).start.hour == 17

def test_tiebreak_most_seats():
    sessions = [
        S("ICCS 014", datetime(2026,10,3,15,0), available=3),
        S("ICCS 008", datetime(2026,10,3,16,0), available=30),
    ]
    rules = [rule("iccs", "ICCS")]
    assert choose_session(sessions, rules, "most_seats", 1).room_clean == "ICCS 008"

def test_min_seats_excludes_low_availability():
    sessions = [S("ICCS 014", datetime(2026,10,3,15,0), available=1)]
    rules = [rule("iccs", "ICCS")]
    assert choose_session(sessions, rules, "earliest", 2) is None

def test_ignores_non_reservable():
    sessions = [S("ICCS 014", datetime(2026,10,3,15,0), status=NO_SEATS)]
    rules = [rule("any", ".*")]
    assert choose_session(sessions, rules, "earliest", 1) is None

def test_no_match_returns_none():
    sessions = [S("HENN 203", datetime(2026,10,3,9,0))]
    rules = [rule("iccs", "ICCS")]
    assert choose_session(sessions, rules, "earliest", 1) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ranker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.ranker'`

- [ ] **Step 3: Write minimal implementation**

`src/ranker.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ranker.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ranker.py tests/test_ranker.py
git commit -m "feat: ranked preference selection logic"
```

---

## Task 5: Notifier (webhook)

**Files:**
- Create: `src/notifier.py`
- Test: `tests/test_notifier.py`

**Interfaces:**
- Consumes: nothing from other src modules.
- Produces: `Notifier(webhook_url: str | None)` with `send(self, event: str, message: str) -> bool`. Discord URLs (`discord.com`/`discordapp.com`) send `{"content": ...}`; Slack (`hooks.slack.com`) send `{"text": ...}`; others default to `{"text": ...}`. No webhook → logs and returns `False`. Network errors are caught and logged, return `False`.

- [ ] **Step 1: Write the failing test**

`tests/test_notifier.py`:
```python
from src import notifier as N

class FakeResp:
    status_code = 204

def test_no_webhook_returns_false(caplog):
    n = N.Notifier(None)
    assert n.send("started", "hi") is False

def test_discord_payload(monkeypatch):
    captured = {}
    def fake_post(url, json, timeout):
        captured["url"] = url; captured["json"] = json; return FakeResp()
    monkeypatch.setattr(N.requests, "post", fake_post)
    n = N.Notifier("https://discord.com/api/webhooks/xyz")
    assert n.send("booked", "Got HENN 203") is True
    assert captured["json"] == {"content": "[booked] Got HENN 203"}

def test_slack_payload(monkeypatch):
    captured = {}
    def fake_post(url, json, timeout):
        captured["json"] = json; return FakeResp()
    monkeypatch.setattr(N.requests, "post", fake_post)
    n = N.Notifier("https://hooks.slack.com/services/xyz")
    assert n.send("booked", "Got HENN 203") is True
    assert captured["json"] == {"text": "[booked] Got HENN 203"}

def test_network_error_returns_false(monkeypatch):
    def boom(*a, **k):
        raise N.requests.RequestException("down")
    monkeypatch.setattr(N.requests, "post", boom)
    n = N.Notifier("https://hooks.slack.com/services/xyz")
    assert n.send("crash", "oops") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.notifier'`

- [ ] **Step 3: Write minimal implementation**

`src/notifier.py`:
```python
from __future__ import annotations
import logging
import requests

log = logging.getLogger(__name__)

class Notifier:
    def __init__(self, webhook_url: str | None):
        self.webhook_url = webhook_url

    def send(self, event: str, message: str) -> bool:
        text = f"[{event}] {message}"
        if not self.webhook_url:
            log.info("notify (no webhook): %s", text)
            return False
        if "discord.com" in self.webhook_url or "discordapp.com" in self.webhook_url:
            payload = {"content": text}
        else:
            payload = {"text": text}
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            ok = 200 <= resp.status_code < 300
            if not ok:
                log.warning("notify failed status=%s", resp.status_code)
            return ok
        except requests.RequestException as e:
            log.warning("notify error: %s", e)
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_notifier.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/notifier.py tests/test_notifier.py
git commit -m "feat: Discord/Slack webhook notifier"
```

---

## Task 6: Watcher helpers (discovery, ramp, session guard)

**Files:**
- Create: `src/watcher.py`
- Test: `tests/test_watcher.py`

**Interfaces:**
- Consumes: `AppConfig`, `TargetExam` from `src.config`.
- Produces (pure helpers, unit-tested here; the async `run_watch(...)` loop added in Task 8):
  - `exam_url(exam_id: str) -> str` → `https://us.prairietest.com/pt/student/exam/{id}`
  - `discover_exam_id(home_html: str, match: re.Pattern) -> str | None` → finds `/pt/student/exam/{id}` link whose text matches.
  - `is_logged_out(html: str, url: str) -> bool` → True if redirected to login / CWL markers present.
  - `next_interval(now: datetime, poll: PollConfig) -> float` → jittered base interval, or `ramp_interval_seconds` when within 60s before `open_time`.

- [ ] **Step 1: Write the failing test**

`tests/test_watcher.py`:
```python
import re
from datetime import datetime, timedelta
from src.config import PollConfig
from src import watcher as W

def test_exam_url():
    assert W.exam_url("87130") == "https://us.prairietest.com/pt/student/exam/87130"

def test_discover_exam_id():
    html = '''
    <a href="/pt/student/exam/999">CPSC 320 (2026W1): Midterm 1</a>
    <a href="/pt/student/exam/87130">CPSC 313 (2026W1): CPSC 313 Quiz 1</a>
    '''
    assert W.discover_exam_id(html, re.compile("CPSC 313 Quiz 1", re.I)) == "87130"

def test_discover_exam_id_none_when_absent():
    html = '<a href="/pt/student/exam/999">Other exam</a>'
    assert W.discover_exam_id(html, re.compile("CPSC 313 Quiz 1", re.I)) is None

def test_is_logged_out_by_url():
    assert W.is_logged_out("<html>anything</html>", "https://authentication.ubc.ca/login") is True

def test_is_logged_out_by_marker():
    assert W.is_logged_out("<h1>Sign in with your CWL</h1>", "https://us.prairietest.com/pt") is True

def test_is_logged_in():
    assert W.is_logged_out("<main>Exam reservation</main>", "https://us.prairietest.com/pt/student/exam/87130") is False

def test_next_interval_base(monkeypatch):
    poll = PollConfig(interval_seconds=15, jitter_seconds=0, open_time=None, ramp_interval_seconds=2)
    assert W.next_interval(datetime(2026,10,1,8,0,0), poll) == 15

def test_next_interval_ramps_near_open_time():
    ot = datetime(2026,10,1,9,0,0)
    poll = PollConfig(interval_seconds=15, jitter_seconds=0, open_time=ot, ramp_interval_seconds=2)
    assert W.next_interval(ot - timedelta(seconds=30), poll) == 2   # within ramp window
    assert W.next_interval(ot - timedelta(seconds=300), poll) == 15 # outside window
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_watcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.watcher'`

- [ ] **Step 3: Write minimal implementation**

`src/watcher.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_watcher.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add src/watcher.py tests/test_watcher.py
git commit -m "feat: watcher pure helpers (discovery, ramp, session guard)"
```

---

## Task 7: Booker (Playwright)

**Files:**
- Create: `src/booker.py`
- Test: `tests/test_booker.py`

**Interfaces:**
- Consumes: `Session` from `src.models`.
- Produces:
  - `BookResult` dataclass `{outcome: str, detail: str}` with outcome constants `BOOKED, DRY_RUN, TAKEN, FAILED`.
  - `async book(page, session: Session, dry_run: bool) -> BookResult`. Locates the reserve button by exact accessible name (`session.reserve_button_name`); refuses if the name doesn't start with "Reserve this session on" or is None (returns `FAILED`); in `dry_run` returns `DRY_RUN` without clicking; otherwise clicks, handles an optional confirm dialog, verifies success.
  - `_confirm_locator_names` constant list of accepted confirm-button names (defensive).

- [ ] **Step 1: Write the failing test (guard logic, no browser)**

`tests/test_booker.py`:
```python
import asyncio
from datetime import datetime
from src.models import Session, RESERVABLE
from src.booker import book, BookResult, FAILED, DRY_RUN

def mk(name):
    return Session(status=RESERVABLE, start=datetime(2026,10,3,15,0), center="ORCA",
                   room="ICCS 008", room_clean="ICCS 008", attributes="", available=5,
                   capacity=44, reserve_button_name=name)

class FakePage:
    """Minimal stand-in; book() must not touch it in the guarded/dry paths under test."""
    def get_by_role(self, *a, **k):
        raise AssertionError("should not be called in these tests")

def test_refuses_missing_button_name():
    res = asyncio.run(book(FakePage(), mk(None), dry_run=True))
    assert res.outcome == FAILED

def test_refuses_non_reserve_name():
    res = asyncio.run(book(FakePage(), mk("Delete reservation"), dry_run=True))
    assert res.outcome == FAILED

def test_dry_run_does_not_click():
    res = asyncio.run(book(FakePage(), mk("Reserve this session on Sat, Oct 3, 3pm (PDT) in ORCA: ⛳️ICCS 008"), dry_run=True))
    assert res.outcome == DRY_RUN
    assert "ICCS 008" in res.detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_booker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.booker'`

- [ ] **Step 3: Write minimal implementation**

`src/booker.py`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_booker.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/booker.py tests/test_booker.py
git commit -m "feat: booker with dry-run and safety guards"
```

---

## Task 8: Seeder + watcher loop + main entrypoint

**Files:**
- Create: `seed_session.py`, `src/main.py`
- Modify: `src/watcher.py` (add `async run_watch(...)` loop)

**Interfaces:**
- Consumes: `load_config`, `AppConfig` (`src.config`); `parse_sessions` (`src.parser`); `choose_session` (`src.ranker`); `Notifier` (`src.notifier`); `book` + result constants (`src.booker`); helpers `exam_url`, `discover_exam_id`, `is_logged_out`, `next_interval` (`src.watcher`).
- Produces:
  - `seed_session.py`: standalone script; launches headed Chromium, waits for manual login, saves `data/storageState.json`.
  - `src.watcher.run_watch(config, notifier, storage_state_path)`: async loop; per target exam, resolves the exam id (config or discovery), loads the exam page reusing storage state, guards for logout, parses + ranks + books; stops the exam on success; backs off on errors.
  - `src.main.main()`: sync entrypoint; loads config, builds notifier, runs `run_watch` with graceful shutdown on SIGINT/SIGTERM.

- [ ] **Step 1: Write the seeder**

`seed_session.py`:
```python
"""Interactive one-time session seeder. Run on your own machine (not in Docker).

    python seed_session.py

Opens a browser to PrairieTest. Log in with CWL + Duo, and check
'remember this device for 30 days'. Once you see your PrairieTest home page,
return here and press Enter. The session is saved to data/storageState.json.
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

HOME = "https://us.prairietest.com/pt"
OUT = Path("data/storageState.json")

async def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(HOME)
        print("Log in with CWL + Duo (check 'remember this device 30 days').")
        input("When your PrairieTest home page is loaded, press Enter here to save the session...")
        await context.storage_state(path=str(OUT))
        print(f"Saved session to {OUT.resolve()}")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Add the async loop to `src/watcher.py`**

Append to `src/watcher.py`:
```python
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
```

- [ ] **Step 3: Write the entrypoint**

`src/main.py`:
```python
from __future__ import annotations
import asyncio
import logging
import os
import signal
from src.config import load_config
from src.notifier import Notifier
from src.watcher import run_watch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

def main():
    config_path = os.environ.get("CONFIG_PATH", "config.yaml")
    storage_path = os.environ.get("STORAGE_STATE", "data/storageState.json")
    config = load_config(config_path)
    notifier = Notifier(config.notify.webhook_url)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(run_watch(config, notifier, storage_path))

    def _stop(*_):
        notifier.send("stopping", "received shutdown signal")
        task.cancel()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _stop())  # Windows fallback

    try:
        loop.run_until_complete(task)
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Verify the full suite still passes and modules import**

Run: `pytest -v && python -c "import src.main, src.watcher, seed_session"`
Expected: all tests PASS; imports succeed with no error.

- [ ] **Step 5: Commit**

```bash
git add seed_session.py src/main.py src/watcher.py
git commit -m "feat: session seeder, watch loop, and main entrypoint"
```

---

## Task 9: Docker packaging + README

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`

**Interfaces:**
- Consumes: `src.main:main` as the container command; `config.yaml`, `data/storageState.json`, `WEBHOOK_URL` mounted/passed at runtime.
- Produces: a runnable image and documented workflow.

- [ ] **Step 1: Write the Dockerfile**

`Dockerfile`:
```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY config.yaml ./config.yaml
ENV CONFIG_PATH=/app/config.yaml STORAGE_STATE=/app/data/storageState.json
CMD ["python", "-m", "src.main"]
```

- [ ] **Step 2: Write compose + env example**

`docker-compose.yml`:
```yaml
services:
  snipe:
    build: .
    environment:
      - WEBHOOK_URL=${WEBHOOK_URL}
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ./data:/app/data:ro
    restart: unless-stopped
```

`.env.example`:
```
WEBHOOK_URL=https://discord.com/api/webhooks/xxx/yyy
```

- [ ] **Step 3: Write the README**

`README.md`:
```markdown
# PrairieTest Snipe Bot

Headless bot that reuses your logged-in PrairieTest session, watches a target
exam, and books the best open seat by ranked preferences. One container per
account. See `docs/superpowers/specs/` for the full design.

## Setup
1. `pip install -r requirements.txt && playwright install chromium`
2. `cp config.example.yaml config.yaml` and edit your exam + preferences.
3. `cp .env.example .env` and set your Discord/Slack `WEBHOOK_URL`.
4. Seed your login (on your own machine, opens a browser):
   `python seed_session.py` → log in with CWL + Duo, check "remember this device
   30 days", press Enter. Produces `data/storageState.json`.

## Run locally
`WEBHOOK_URL=... python -m src.main`

## Run in Docker
`docker compose --env-file .env up --build`

`data/` (holding `storageState.json`) is mounted read-only into the container.

## Safety
- `dry_run: true` (default) detects + ranks + logs without booking. Set to
  `false` only when you're ready to book for real.
- The bot never stores your password, never automates Duo, and never deletes a
  reservation.

## Re-auth
When notified `session_invalid`, re-run `python seed_session.py` to refresh
`data/storageState.json` (Duo device trust lasts ~30 days).

## Tests
`pytest -v`
```

- [ ] **Step 4: Verify build config is coherent**

Run: `pytest -v`
Expected: full suite PASS (Docker build itself is validated during first real deployment).

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml .env.example README.md
git commit -m "feat: Docker packaging and README"
```

---

## Task 10: Live validation (dry-run) — resolves spec §9 open items

**Files:** none (operational task; may add a captured note under `docs/`).

**Interfaces:** uses the built bot end-to-end against the live site in `dry_run` mode.

- [ ] **Step 1: Seed a session**

Run: `python seed_session.py` and log in. Confirm `data/storageState.json` exists.

- [ ] **Step 2: Point config at the known exam**

Set `exam_id` in `config.yaml` to a currently-visible exam (e.g. the reference `87130`), keep `dry_run: true`, and set a permissive preference (`location: ".*"`).

- [ ] **Step 3: Run once and read logs**

Run: `WEBHOOK_URL=... python -m src.main`
Expected: logs show parsed sessions, a chosen candidate, and a `dry_run` "would reserve …" line — no reservation made. Confirm the webhook received the messages.

- [ ] **Step 4: Confirm the deferred unknowns**

Verify against the live run and record findings in `docs/superpowers/specs/reference-booking-page-dom.md`:
- Whether clicking reserve shows a confirmation step (adjust `_CONFIRM_NAMES` in `src/booker.py` if a real confirm button name differs).
- The exact logged-out DOM/URL (tighten `is_logged_out` markers in `src/watcher.py` if needed).
- How a brand-new (never-reserved) exam renders under "Exams available for reservations" (confirm `discover_exam_id` matches it).

- [ ] **Step 5: Commit any selector adjustments**

```bash
git add -A
git commit -m "chore: tune selectors from live dry-run validation"
```

---

## Self-Review Notes

- **Spec coverage:** seeder (§3.1.1 / Task 8), config incl. ranges+regex (§4 / Task 3), watcher+discovery+guard+ramp (§3.1.3/§6 / Tasks 6, 8), parser (§2 / Task 2), ranker (§3.1.5 / Task 4), booker with dry-run + delete-exclusion (§3.1.6 / Task 7), notifier events (§3.1.7 / Task 5), Docker/compose (§3.2 / Task 9), tests incl. fixture (§7 / Tasks 1-8), safety (§8 / Global Constraints + Task 7), open items (§9 / Task 10). All covered.
- **Types consistent:** `Session`, `PreferenceRule`, `AppConfig`, `BookResult` and helper signatures match across tasks.
- **No placeholders:** every code step contains complete code; live-tuning is isolated to Task 10 with explicit criteria.
