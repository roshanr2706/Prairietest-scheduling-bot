# PrairieTest Web UI + Multi-Exam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single-user, CWL-gated web UI that manages multiple set-and-forget exam watches, shows live status/logs, and controls a DB-driven multi-exam watcher.

**Architecture:** One FastAPI process serves the UI and runs the watcher as an asyncio background task. State lives in SQLite on the data volume. The pure engine (`models`, `parser`, `ranker`, `notifier`, `auth`, and the `config` dataclasses/helpers) is reused; a new `engine.py` reads targets from the DB and books every matching, not-yet-booked exam each cycle.

**Tech Stack:** Python 3.11+, FastAPI + uvicorn, Jinja2, SQLite (stdlib), Playwright, pytest + httpx (TestClient).

## Global Constraints

- Python 3.11+; reuse existing modules unchanged: `src/models.py`, `src/parser.py`, `src/ranker.py`, `src/notifier.py`, `src/auth.py`, and the `config` dataclasses `TargetExam`/`PreferenceRule` + helpers `_compile_regex`/`_parse_time_range`/`_parse_date_range`.
- Never store a new copy of the CWL password: UI login validates against env `CWL_USERNAME`/`CWL_PASSWORD` via `hmac.compare_digest`.
- Discord webhook fires ONLY for `booked` and hard failures (`booking_failed`, `auth_failed`). Duo/status/detection go to the UI + `events` table, never the webhook.
- Booking message format: `"<CWL_USERNAME> has had <exam name> booked at <slot start> in <room>"`.
- Never click Delete / `button.btn-danger` / `#deleteReservationModal`.
- Flat poll interval (default 300s) + jitter (default 15s); no `open_time`/ramp.
- `dry_run` is per-target; the watcher must not book (only log) when a target's `dry_run` is true, and must not book at all unless `session_state == "connected"`.
- Web entrypoint: `uvicorn src.webapp:app --host 0.0.0.0 --port 8000`. The legacy `src/main.py`/`config.yaml` CLI is left intact but is no longer the container entrypoint.
- Secrets from env/.env only: `CWL_USERNAME`, `CWL_PASSWORD`, `SESSION_SECRET`, `WEBHOOK_URL`.

---

## File Structure

- `src/db.py` — `Database` class: SQLite schema + typed CRUD for targets/preferences/bookings/events/kv, and `to_target_exam(id)` row→dataclass mapping.
- `src/engine.py` — pure `plan_decisions(...)` + async `run_engine(...)` loop (login state, multi-exam discovery, book/dry-run, record). Reuses `watcher` helpers + `auth`.
- `src/watcher.py` — MODIFY: add `matching_exams(home_html, pattern)` beside `discover_exam_id`. (Existing helpers + legacy loop stay.)
- `src/webapp.py` — FastAPI app: session auth, routes, templates, background-task lifecycle.
- `templates/base.html`, `login.html`, `dashboard.html`, `target_form.html`, `logs.html`.
- `static/app.css`, `static/app.js`.
- `tests/test_db.py`, `tests/test_engine.py`, `tests/test_webapp.py`; MODIFY `tests/test_watcher.py` (add `matching_exams` tests).
- MODIFY: `requirements.txt`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`.

---

## Task 1: Dependencies + Database (targets/preferences + mapping)

**Files:**
- Modify: `requirements.txt`
- Create: `src/db.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: `TargetExam`, `PreferenceRule` and helpers `_compile_regex`, `_parse_time_range`, `_parse_date_range` from `src.config`.
- Produces: `Database(path: str)` with:
  - `list_targets(enabled_only=False) -> list[sqlite3.Row]`
  - `get_target(target_id) -> sqlite3.Row | None`
  - `get_preferences(target_id) -> list[sqlite3.Row]`
  - `upsert_target(name, match, min_seats, tiebreak, enabled, dry_run, preferences, target_id=None) -> int` where `preferences` is a list of dicts with keys `location, time_range, date_range, weekdays` (any optional)
  - `delete_target(target_id)`
  - `set_target_flags(target_id, *, enabled=None, dry_run=None)`
  - `to_target_exam(target_id) -> TargetExam` (rows → dataclasses for the ranker)

- [ ] **Step 1: Add dependencies**

Append to `requirements.txt`:
```
fastapi==0.115.5
uvicorn[standard]==0.32.1
jinja2==3.1.4
python-multipart==0.0.12
itsdangerous==2.2.0
```
Install: `.venv/Scripts/python.exe -m pip install -r requirements.txt httpx`
Expected: installs succeed (httpx is for the FastAPI TestClient).

- [ ] **Step 2: Write the failing test**

`tests/test_db.py`:
```python
from datetime import time, date
from src.db import Database

def make(tmp_path):
    return Database(str(tmp_path / "state.db"))

def test_upsert_and_list_target(tmp_path):
    db = make(tmp_path)
    tid = db.upsert_target(
        name="313 Friday", match="CPSC 313", min_seats=1, tiebreak="earliest",
        enabled=True, dry_run=True,
        preferences=[{"location": "ICCS", "time_range": "15:00-17:00", "weekdays": ["Fri"]}],
    )
    rows = db.list_targets()
    assert len(rows) == 1 and rows[0]["name"] == "313 Friday"
    prefs = db.get_preferences(tid)
    assert len(prefs) == 1 and prefs[0]["location"] == "ICCS"

def test_update_replaces_preferences(tmp_path):
    db = make(tmp_path)
    tid = db.upsert_target(name="a", match="X", min_seats=1, tiebreak="earliest",
                           enabled=True, dry_run=True,
                           preferences=[{"location": "ICCS"}])
    db.upsert_target(name="a2", match="Y", min_seats=2, tiebreak="latest",
                     enabled=False, dry_run=False,
                     preferences=[{"location": "HENN"}, {"location": ".*"}],
                     target_id=tid)
    row = db.get_target(tid)
    assert row["name"] == "a2" and row["match"] == "Y" and row["min_seats"] == 2
    assert row["enabled"] == 0 and row["dry_run"] == 0
    assert len(db.get_preferences(tid)) == 2

def test_set_target_flags(tmp_path):
    db = make(tmp_path)
    tid = db.upsert_target(name="a", match="X", min_seats=1, tiebreak="earliest",
                           enabled=True, dry_run=True, preferences=[])
    db.set_target_flags(tid, enabled=False)
    assert db.get_target(tid)["enabled"] == 0
    db.set_target_flags(tid, dry_run=False)
    assert db.get_target(tid)["dry_run"] == 0

def test_delete_target_removes_prefs(tmp_path):
    db = make(tmp_path)
    tid = db.upsert_target(name="a", match="X", min_seats=1, tiebreak="earliest",
                           enabled=True, dry_run=True, preferences=[{"location": "ICCS"}])
    db.delete_target(tid)
    assert db.get_target(tid) is None
    assert db.get_preferences(tid) == []

def test_to_target_exam_maps_to_dataclasses(tmp_path):
    db = make(tmp_path)
    tid = db.upsert_target(
        name="313", match="CPSC 313", min_seats=2, tiebreak="most_seats",
        enabled=True, dry_run=False,
        preferences=[{"location": "ICCS 01[48]", "time_range": "15:00-17:00",
                      "date_range": "2026-10-01..2026-10-03", "weekdays": ["Fri"]}],
    )
    ex = db.to_target_exam(tid)
    assert ex.match.search("CPSC 313 Quiz 1") and ex.min_seats == 2 and ex.tiebreak == "most_seats"
    r = ex.preferences[0]
    assert r.location.search("ICCS 014") and not r.location.search("ICCS 008")
    assert r.time_start == time(15, 0) and r.time_end == time(17, 0)
    assert r.date_start == date(2026, 10, 1) and r.date_end == date(2026, 10, 3)
    assert r.weekdays == {"Fri"}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.db'`

- [ ] **Step 4: Write minimal implementation**

`src/db.py`:
```python
from __future__ import annotations
import sqlite3
from src.config import (
    TargetExam, PreferenceRule, _compile_regex, _parse_time_range, _parse_date_range,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, match TEXT NOT NULL,
  min_seats INTEGER NOT NULL DEFAULT 1,
  tiebreak TEXT NOT NULL DEFAULT 'earliest',
  enabled INTEGER NOT NULL DEFAULT 1,
  dry_run INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS preferences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
  position INTEGER NOT NULL DEFAULT 0,
  location TEXT, time_range TEXT, date_range TEXT, weekdays TEXT
);
CREATE TABLE IF NOT EXISTS bookings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target_id INTEGER, exam_id TEXT NOT NULL, exam_name TEXT,
  room TEXT, slot_start TEXT, cwl TEXT, dry_run INTEGER NOT NULL DEFAULT 0,
  booked_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now')),
  level TEXT NOT NULL, message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

class Database:
    def __init__(self, path: str):
        self.path = path
        con = self._connect()
        con.executescript(_SCHEMA)
        con.commit()
        con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    # ---- targets ----
    def list_targets(self, enabled_only: bool = False) -> list[sqlite3.Row]:
        con = self._connect()
        q = "SELECT * FROM targets"
        if enabled_only:
            q += " WHERE enabled = 1"
        q += " ORDER BY id"
        rows = con.execute(q).fetchall()
        con.close()
        return rows

    def get_target(self, target_id: int) -> sqlite3.Row | None:
        con = self._connect()
        row = con.execute("SELECT * FROM targets WHERE id = ?", (target_id,)).fetchone()
        con.close()
        return row

    def get_preferences(self, target_id: int) -> list[sqlite3.Row]:
        con = self._connect()
        rows = con.execute(
            "SELECT * FROM preferences WHERE target_id = ? ORDER BY position, id",
            (target_id,),
        ).fetchall()
        con.close()
        return rows

    def upsert_target(self, name, match, min_seats, tiebreak, enabled, dry_run,
                      preferences, target_id=None) -> int:
        con = self._connect()
        cur = con.cursor()
        if target_id is None:
            cur.execute(
                "INSERT INTO targets(name, match, min_seats, tiebreak, enabled, dry_run) "
                "VALUES (?,?,?,?,?,?)",
                (name, match, int(min_seats), tiebreak, int(bool(enabled)), int(bool(dry_run))),
            )
            target_id = cur.lastrowid
        else:
            cur.execute(
                "UPDATE targets SET name=?, match=?, min_seats=?, tiebreak=?, enabled=?, dry_run=? "
                "WHERE id=?",
                (name, match, int(min_seats), tiebreak, int(bool(enabled)), int(bool(dry_run)), target_id),
            )
            cur.execute("DELETE FROM preferences WHERE target_id=?", (target_id,))
        for pos, p in enumerate(preferences or []):
            wd = p.get("weekdays")
            cur.execute(
                "INSERT INTO preferences(target_id, position, location, time_range, date_range, weekdays) "
                "VALUES (?,?,?,?,?,?)",
                (target_id, pos, p.get("location"), p.get("time_range"),
                 p.get("date_range"), ",".join(wd) if wd else None),
            )
        con.commit()
        con.close()
        return target_id

    def delete_target(self, target_id: int) -> None:
        con = self._connect()
        con.execute("DELETE FROM preferences WHERE target_id=?", (target_id,))
        con.execute("DELETE FROM targets WHERE id=?", (target_id,))
        con.commit()
        con.close()

    def set_target_flags(self, target_id: int, *, enabled=None, dry_run=None) -> None:
        con = self._connect()
        if enabled is not None:
            con.execute("UPDATE targets SET enabled=? WHERE id=?", (int(bool(enabled)), target_id))
        if dry_run is not None:
            con.execute("UPDATE targets SET dry_run=? WHERE id=?", (int(bool(dry_run)), target_id))
        con.commit()
        con.close()

    def to_target_exam(self, target_id: int) -> TargetExam:
        row = self.get_target(target_id)
        prefs = []
        for p in self.get_preferences(target_id):
            loc = _compile_regex(p["location"]) if p["location"] else None
            ts = te = None
            if p["time_range"]:
                ts, te = _parse_time_range(p["time_range"])
            ds = de = None
            if p["date_range"]:
                ds, de = _parse_date_range(p["date_range"])
            wd = set(p["weekdays"].split(",")) if p["weekdays"] else None
            prefs.append(PreferenceRule(f"rule{p['position']}", loc, ts, te, ds, de, wd))
        return TargetExam(
            match=_compile_regex(row["match"]), exam_id=None,
            min_seats=row["min_seats"], tiebreak=row["tiebreak"], preferences=prefs,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add requirements.txt src/db.py tests/test_db.py
git commit -m "feat: SQLite database layer for targets and preferences"
```

---

## Task 2: Database — bookings, events, kv

**Files:**
- Modify: `src/db.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Produces on `Database`:
  - `already_booked(target_id, exam_id) -> bool`
  - `record_booking(target_id, exam_id, exam_name, room, slot_start, cwl, dry_run) -> None`
  - `recent_bookings(n=20) -> list[sqlite3.Row]`
  - `add_event(level, message) -> None` (trims to newest 500)
  - `recent_events(n=100) -> list[sqlite3.Row]`
  - `get_kv(key, default=None) -> str | None`; `set_kv(key, value) -> None`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py`:
```python
def test_bookings_dedupe(tmp_path):
    db = make(tmp_path)
    assert db.already_booked(1, "87130") is False
    db.record_booking(1, "87130", "CPSC 313 Quiz 1", "HENN 203",
                      "2026-10-02T13:00:00-07:00", "alice", dry_run=False)
    assert db.already_booked(1, "87130") is True
    assert db.already_booked(1, "999") is False
    assert len(db.recent_bookings()) == 1

def test_dry_run_booking_not_counted_as_booked(tmp_path):
    db = make(tmp_path)
    db.record_booking(1, "87130", "x", "r", "s", "alice", dry_run=True)
    assert db.already_booked(1, "87130") is False  # dry-run doesn't block real booking

def test_events_trim(tmp_path):
    db = make(tmp_path)
    for i in range(520):
        db.add_event("info", f"e{i}")
    ev = db.recent_events(1000)
    assert len(ev) == 500
    assert ev[0]["message"] == "e519"  # newest first

def test_kv(tmp_path):
    db = make(tmp_path)
    assert db.get_kv("session_state", "not_connected") == "not_connected"
    db.set_kv("session_state", "connected")
    assert db.get_kv("session_state") == "connected"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py -k "bookings or events or kv or dry_run" -v`
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'already_booked'`

- [ ] **Step 3: Write minimal implementation**

Append these methods to the `Database` class in `src/db.py`:
```python
    # ---- bookings ----
    def already_booked(self, target_id: int, exam_id: str) -> bool:
        con = self._connect()
        row = con.execute(
            "SELECT 1 FROM bookings WHERE target_id=? AND exam_id=? AND dry_run=0 LIMIT 1",
            (target_id, str(exam_id)),
        ).fetchone()
        con.close()
        return row is not None

    def record_booking(self, target_id, exam_id, exam_name, room, slot_start, cwl, dry_run) -> None:
        con = self._connect()
        con.execute(
            "INSERT INTO bookings(target_id, exam_id, exam_name, room, slot_start, cwl, dry_run) "
            "VALUES (?,?,?,?,?,?,?)",
            (target_id, str(exam_id), exam_name, room, slot_start, cwl, int(bool(dry_run))),
        )
        con.commit()
        con.close()

    def recent_bookings(self, n: int = 20) -> list[sqlite3.Row]:
        con = self._connect()
        rows = con.execute("SELECT * FROM bookings ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        con.close()
        return rows

    # ---- events ----
    def add_event(self, level: str, message: str) -> None:
        con = self._connect()
        con.execute("INSERT INTO events(level, message) VALUES (?,?)", (level, message))
        con.execute(
            "DELETE FROM events WHERE id NOT IN "
            "(SELECT id FROM events ORDER BY id DESC LIMIT 500)"
        )
        con.commit()
        con.close()

    def recent_events(self, n: int = 100) -> list[sqlite3.Row]:
        con = self._connect()
        rows = con.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        con.close()
        return rows

    # ---- kv ----
    def get_kv(self, key: str, default=None):
        con = self._connect()
        row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        con.close()
        return row["value"] if row else default

    def set_kv(self, key: str, value) -> None:
        con = self._connect()
        con.execute(
            "INSERT INTO kv(key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        con.commit()
        con.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add src/db.py tests/test_db.py
git commit -m "feat: bookings, events, and kv storage"
```

---

## Task 3: Engine selection logic (pure)

**Files:**
- Modify: `src/watcher.py` (add `matching_exams`)
- Create: `src/engine.py` (add `plan_decisions` + `Decision`)
- Modify: `tests/test_watcher.py`
- Create: `tests/test_engine.py`

**Interfaces:**
- Consumes: `discover_exam_id`'s regex; `parse_sessions` (`src.parser`); `choose_session` (`src.ranker`); `TargetExam` (`src.config`).
- Produces:
  - `watcher.matching_exams(home_html: str, pattern: re.Pattern) -> list[tuple[str, str]]` — all `(exam_id, exam_name)` whose link text matches, de-duped by exam_id, in document order.
  - `engine.Decision` dataclass `{target_id: int, exam_id: str, exam_name: str, session: Session}`.
  - `engine.plan_decisions(targets, discovered, page_html_by_id, already_booked, dry_run_by_target) -> list[Decision]` where `targets` is `list[tuple[int, TargetExam]]`, `discovered` is `dict[int, list[tuple[str,str]]]` (target_id → matched exams), `page_html_by_id` is `dict[str, str]`, `already_booked` is `callable(target_id, exam_id) -> bool`. Skips exams already booked; picks per target's preferences; returns one Decision per bookable (target, exam).

- [ ] **Step 1: Write the failing test for `matching_exams`**

Append to `tests/test_watcher.py`:
```python
def test_matching_exams_returns_all_matches():
    html = '''
    <a href="/pt/student/exam/1">CPSC 313 (2026W1): Quiz 1</a>
    <a href="/pt/student/exam/2">CPSC 313 (2026W1): Quiz 2</a>
    <a href="/pt/student/exam/3">CPSC 320 (2026W1): Midterm</a>
    <a href="/pt/student/exam/1">CPSC 313 (2026W1): Quiz 1</a>
    '''
    out = W.matching_exams(html, re.compile("CPSC 313", re.I))
    assert out == [("1", "CPSC 313 (2026W1): Quiz 1"), ("2", "CPSC 313 (2026W1): Quiz 2")]
```

- [ ] **Step 2: Run it, expect fail**

Run: `pytest tests/test_watcher.py::test_matching_exams_returns_all_matches -v`
Expected: FAIL with `AttributeError: module 'src.watcher' has no attribute 'matching_exams'`

- [ ] **Step 3: Implement `matching_exams` in `src/watcher.py`**

Add beside `discover_exam_id`:
```python
def matching_exams(home_html: str, pattern) -> list[tuple[str, str]]:
    soup = BeautifulSoup(home_html, "lxml")
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=_EXAM_HREF):
        text = a.get_text(" ", strip=True)
        if not pattern.search(text):
            continue
        exam_id = _EXAM_HREF.search(a["href"]).group(1)
        if exam_id in seen:
            continue
        seen.add(exam_id)
        out.append((exam_id, text))
    return out
```

- [ ] **Step 4: Run it, expect pass**

Run: `pytest tests/test_watcher.py -v`
Expected: PASS (all watcher tests, including the new one)

- [ ] **Step 5: Write the failing test for `plan_decisions`**

`tests/test_engine.py`:
```python
import re
from pathlib import Path
from src.config import TargetExam, PreferenceRule
from src.engine import plan_decisions, Decision

FIX = (Path(__file__).parent / "fixtures" / "sessions_fixture.html").read_text(encoding="utf-8")

def texam(match, prefs, tiebreak="earliest", min_seats=1):
    return TargetExam(match=re.compile(match, re.I), exam_id=None,
                      min_seats=min_seats, tiebreak=tiebreak, preferences=prefs)

def rule(location=None):
    return PreferenceRule("r", re.compile(location, re.I) if location else None,
                          None, None, None, None, None)

def test_plan_books_matching_unbooked_exam():
    targets = [(10, texam("CPSC 313", [rule("HENN")]))]
    discovered = {10: [("87130", "CPSC 313 Quiz 1")]}
    pages = {"87130": FIX}
    decisions = plan_decisions(targets, discovered, pages,
                               already_booked=lambda t, e: False,
                               dry_run_by_target={10: True})
    assert len(decisions) == 1
    d = decisions[0]
    assert d.target_id == 10 and d.exam_id == "87130"
    assert d.session.room_clean == "HENN 203"

def test_plan_skips_already_booked():
    targets = [(10, texam("CPSC 313", [rule(".*")]))]
    discovered = {10: [("87130", "CPSC 313 Quiz 1")]}
    decisions = plan_decisions(targets, discovered, {"87130": FIX},
                               already_booked=lambda t, e: True,
                               dry_run_by_target={10: False})
    assert decisions == []

def test_plan_skips_exam_with_no_matching_slot():
    targets = [(10, texam("CPSC 313", [rule("NONEXISTENT ROOM")]))]
    decisions = plan_decisions(targets, {10: [("87130", "x")]}, {"87130": FIX},
                               already_booked=lambda t, e: False,
                               dry_run_by_target={10: False})
    assert decisions == []

def test_plan_multiple_exams_one_target():
    targets = [(10, texam("CPSC 313", [rule("ICCS")]))]
    discovered = {10: [("87130", "Quiz 1"), ("87131", "Quiz 2")]}
    pages = {"87130": FIX, "87131": FIX}
    decisions = plan_decisions(targets, discovered, pages,
                               already_booked=lambda t, e: False,
                               dry_run_by_target={10: True})
    assert {d.exam_id for d in decisions} == {"87130", "87131"}
```

- [ ] **Step 6: Run it, expect fail**

Run: `pytest tests/test_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.engine'`

- [ ] **Step 7: Implement `plan_decisions` in `src/engine.py`**

`src/engine.py`:
```python
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
```

- [ ] **Step 8: Run it, expect pass**

Run: `pytest tests/test_engine.py -v`
Expected: PASS (4 passed)

- [ ] **Step 9: Commit**

```bash
git add src/watcher.py src/engine.py tests/test_watcher.py tests/test_engine.py
git commit -m "feat: multi-exam discovery and booking-decision logic"
```

---

## Task 4: Engine async loop

**Files:**
- Modify: `src/engine.py`

**Interfaces:**
- Consumes: `Database` (`src.db`); `Notifier` (`src.notifier`); `book` + outcome constants (`src.booker`); `exam_url`, `matching_exams`, `is_logged_out`, `HOME`, `_new_logged_in`, `_relogin` (`src.watcher`); `auth` env creds via `config.AuthConfig`.
- Produces:
  - `engine.auth_config_from_env() -> AuthConfig`
  - `engine.booking_message(cwl, exam_name, session) -> str` (pure; the webhook copy)
  - `async engine.run_engine(db, notifier, storage_state_path, stop_event=None)` — the background loop.

- [ ] **Step 1: Write the failing test (pure helper)**

Append to `tests/test_engine.py`:
```python
import os
from datetime import datetime
from src.models import Session, RESERVABLE
from src.engine import booking_message, auth_config_from_env

def test_booking_message_format():
    s = Session(status=RESERVABLE, start=datetime(2026,10,2,13,0), center="ORCA",
                room="💥HENN 203", room_clean="HENN 203", attributes="", available=4,
                capacity=14, reserve_button_name="Reserve this session on X in ORCA: HENN 203")
    msg = booking_message("alice", "CPSC 313 Quiz 1", s)
    assert msg == "alice has had CPSC 313 Quiz 1 booked at 2026-10-02 13:00 in HENN 203"

def test_auth_config_from_env(monkeypatch):
    monkeypatch.setenv("CWL_USERNAME", "bob")
    monkeypatch.setenv("CWL_PASSWORD", "pw")
    monkeypatch.setenv("DUO_WAIT_SECONDS", "90")
    cfg = auth_config_from_env()
    assert cfg.username == "bob" and cfg.password == "pw" and cfg.duo_wait_seconds == 90
```

- [ ] **Step 2: Run it, expect fail**

Run: `pytest tests/test_engine.py -k "booking_message or auth_config" -v`
Expected: FAIL with `ImportError: cannot import name 'booking_message'`

- [ ] **Step 3: Implement the loop + helpers in `src/engine.py`**

Append to `src/engine.py`:
```python
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
                targets = [(r["id"], db.to_target_exam(r["id"]))
                           for r in db.list_targets(enabled_only=True)]
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
                dry_by = {r["id"]: bool(r["dry_run"]) for r in db.list_targets(enabled_only=True)}
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

class _Cfg:
    """Adapts an AuthConfig to the .auth attribute expected by watcher helpers."""
    def __init__(self, auth):
        self.auth = auth
```

- [ ] **Step 4: Run it, expect pass**

Run: `pytest tests/test_engine.py -v && python -c "import src.engine"`
Expected: PASS (6 passed) and import succeeds.

- [ ] **Step 5: Commit**

```bash
git add src/engine.py tests/test_engine.py
git commit -m "feat: DB-driven multi-exam watch loop"
```

---

## Task 5: Web app auth

**Files:**
- Create: `src/webapp.py`, `templates/base.html`, `templates/login.html`, `static/app.css`
- Create: `tests/test_webapp.py`

**Interfaces:**
- Consumes: `Database` (`src.db`).
- Produces:
  - `webapp.create_app(db=None) -> FastAPI` (test-injectable DB; default builds one from `STATE_DB` env or `data/state.db`).
  - `webapp.check_cwl(username, password) -> bool` (constant-time vs env `CWL_USERNAME`/`CWL_PASSWORD`).
  - Routes: `GET /login`, `POST /login` (form `username`,`password`), `GET /logout`; a dependency that redirects unauthenticated users to `/login`.
  - `app = create_app()` module-level for uvicorn.

- [ ] **Step 1: Write the failing test**

`tests/test_webapp.py`:
```python
import pytest
from fastapi.testclient import TestClient
from src.db import Database
from src.webapp import create_app, check_cwl

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CWL_USERNAME", "alice")
    monkeypatch.setenv("CWL_PASSWORD", "pw")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    db = Database(str(tmp_path / "state.db"))
    app = create_app(db=db)
    return TestClient(app), db

def test_check_cwl(monkeypatch):
    monkeypatch.setenv("CWL_USERNAME", "alice")
    monkeypatch.setenv("CWL_PASSWORD", "pw")
    assert check_cwl("alice", "pw") is True
    assert check_cwl("alice", "wrong") is False
    assert check_cwl("bob", "pw") is False

def test_dashboard_requires_login(client):
    c, _ = client
    r = c.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"].endswith("/login")

def test_login_rejects_bad_creds(client):
    c, _ = client
    r = c.post("/login", data={"username": "alice", "password": "nope"}, follow_redirects=False)
    assert r.status_code == 200  # re-renders login with error
    assert "Invalid" in r.text

def test_login_then_dashboard(client):
    c, _ = client
    r = c.post("/login", data={"username": "alice", "password": "pw"}, follow_redirects=False)
    assert r.status_code in (302, 307)
    r2 = c.get("/")
    assert r2.status_code == 200
```

- [ ] **Step 2: Run it, expect fail**

Run: `pytest tests/test_webapp.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.webapp'`

- [ ] **Step 3: Implement auth + templates**

`templates/base.html`:
```html
<!doctype html>
<html lang="en"><head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Snipe Bot{% endblock %}</title>
  <link rel="stylesheet" href="/static/app.css">
</head><body>
  <header><strong>PrairieTest Snipe Bot</strong>
    {% if request.session.get('user') %}<nav>
      <a href="/">Dashboard</a> <a href="/logs">Logs</a> <a href="/logout">Logout</a>
    </nav>{% endif %}
  </header>
  <main>{% block body %}{% endblock %}</main>
  <script src="/static/app.js"></script>
</body></html>
```

`templates/login.html`:
```html
{% extends "base.html" %}{% block title %}Login{% endblock %}
{% block body %}
<h1>Sign in with CWL</h1>
{% if error %}<p class="error">{{ error }}</p>{% endif %}
<form method="post" action="/login">
  <label>CWL username <input name="username" autocomplete="username"></label>
  <label>CWL password <input name="password" type="password" autocomplete="current-password"></label>
  <button type="submit">Sign in</button>
</form>
{% endblock %}
```

`static/app.css`:
```css
:root { color-scheme: light dark; font-family: system-ui, sans-serif; }
body { max-width: 900px; margin: 0 auto; padding: 1rem; }
header { display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #8888; padding-bottom:.5rem; margin-bottom:1rem; }
nav a { margin-left: 1rem; }
label { display:block; margin:.5rem 0; }
input, select { padding:.3rem; }
.error { color:#c00; }
table { width:100%; border-collapse: collapse; }
th, td { text-align:left; padding:.3rem .5rem; border-bottom:1px solid #8884; }
.pill { padding:.1rem .5rem; border-radius:1rem; border:1px solid #8888; font-size:.8rem; }
```

`src/webapp.py`:
```python
from __future__ import annotations
import hmac
import os
from pathlib import Path
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from src.db import Database

BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE / "templates"))

def check_cwl(username: str, password: str) -> bool:
    u = os.environ.get("CWL_USERNAME", "")
    p = os.environ.get("CWL_PASSWORD", "")
    return bool(u) and bool(p) and hmac.compare_digest(username, u) and hmac.compare_digest(password, p)

def create_app(db: Database | None = None) -> FastAPI:
    app = FastAPI()
    app.state.db = db or Database(os.environ.get("STATE_DB", "data/state.db"))
    app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SESSION_SECRET", os.urandom(16).hex()))
    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

    def require_user(request: Request):
        return request.session.get("user")

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return templates.TemplateResponse("login.html", {"request": request})

    @app.post("/login")
    def login(request: Request, username: str = Form(...), password: str = Form(...)):
        if check_cwl(username, password):
            request.session["user"] = username
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid CWL credentials"})

    @app.get("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        if not require_user(request):
            return RedirectResponse("/login", status_code=303)
        return HTMLResponse("<p>ok</p>")  # replaced in Task 6

    return app

app = create_app()
```

- [ ] **Step 4: Run it, expect pass**

Run: `pytest tests/test_webapp.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/webapp.py templates/base.html templates/login.html static/app.css tests/test_webapp.py
git commit -m "feat: FastAPI app with CWL session login"
```

---

## Task 6: Targets CRUD + dashboard + controls

**Files:**
- Modify: `src/webapp.py`
- Create: `templates/dashboard.html`, `templates/target_form.html`, `templates/logs.html`, `static/app.js`
- Modify: `tests/test_webapp.py`

**Interfaces:**
- Consumes: `Database` methods from Tasks 1-2.
- Produces routes (all require login): `GET /` (dashboard), `GET /targets/new`, `POST /targets` (create), `GET /targets/{id}` (edit), `POST /targets/{id}` (update), `POST /targets/{id}/delete`, `POST /targets/{id}/toggle` (form `field`=`enabled|dry_run`), `POST /watch/{action}` (`start|stop`), `POST /connect`, `GET /logs`, `GET /api/status`, `GET /api/events`.
- Form fields for create/update: `name`, `match`, `min_seats`, `tiebreak`, `enabled` (checkbox), `dry_run` (checkbox), and repeated preference fields `pref_location`, `pref_time_range`, `pref_date_range`, `pref_weekdays` (parallel lists; blank rows ignored).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_webapp.py`:
```python
def login(c):
    c.post("/login", data={"username": "alice", "password": "pw"})

def test_create_and_list_target(client):
    c, db = client
    login(c)
    r = c.post("/targets", data={
        "name": "313 Friday", "match": "CPSC 313", "min_seats": "1",
        "tiebreak": "earliest", "enabled": "on", "dry_run": "on",
        "pref_location": ["ICCS", ""], "pref_time_range": ["15:00-17:00", ""],
        "pref_date_range": ["", ""], "pref_weekdays": ["Fri", ""],
    }, follow_redirects=False)
    assert r.status_code in (302, 303)
    rows = db.list_targets()
    assert len(rows) == 1 and rows[0]["name"] == "313 Friday"
    assert len(db.get_preferences(rows[0]["id"])) == 1  # blank row ignored
    body = c.get("/").text
    assert "313 Friday" in body

def test_toggle_and_delete(client):
    c, db = client
    login(c)
    tid = db.upsert_target(name="a", match="X", min_seats=1, tiebreak="earliest",
                           enabled=True, dry_run=True, preferences=[])
    c.post(f"/targets/{tid}/toggle", data={"field": "dry_run"})
    assert db.get_target(tid)["dry_run"] == 0
    c.post(f"/targets/{tid}/delete")
    assert db.get_target(tid) is None

def test_watch_start_stop_and_status(client):
    c, db = client
    login(c)
    c.post("/watch/stop")
    assert db.get_kv("watch_running") == "0"
    c.post("/watch/start")
    assert db.get_kv("watch_running") == "1"
    s = c.get("/api/status").json()
    assert s["watch_running"] is True and "session_state" in s

def test_events_api(client):
    c, db = client
    login(c)
    db.add_event("info", "hello world")
    data = c.get("/api/events").json()
    assert any(e["message"] == "hello world" for e in data["events"])
```

- [ ] **Step 2: Run it, expect fail**

Run: `pytest tests/test_webapp.py -k "create or toggle or watch or events_api" -v`
Expected: FAIL (routes 404 / not found)

- [ ] **Step 3: Implement routes + templates**

Replace the `dashboard` route in `src/webapp.py` and add the rest. In `create_app`, after the `logout` route, replace/insert:
```python
    def _prefs_from_form(form) -> list[dict]:
        locs = form.getlist("pref_location")
        times = form.getlist("pref_time_range")
        dates = form.getlist("pref_date_range")
        days = form.getlist("pref_weekdays")
        prefs = []
        for i in range(max(len(locs), len(times), len(dates), len(days))):
            loc = (locs[i] if i < len(locs) else "").strip()
            tr = (times[i] if i < len(times) else "").strip()
            dr = (dates[i] if i < len(dates) else "").strip()
            wd = (days[i] if i < len(days) else "").strip()
            if not any([loc, tr, dr, wd]):
                continue
            prefs.append({
                "location": loc or None, "time_range": tr or None,
                "date_range": dr or None,
                "weekdays": [d.strip() for d in wd.split(",") if d.strip()] or None,
            })
        return prefs

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        if not require_user(request):
            return RedirectResponse("/login", status_code=303)
        db = app.state.db
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "targets": db.list_targets(),
            "prefs_by_target": {r["id"]: db.get_preferences(r["id"]) for r in db.list_targets()},
            "bookings": db.recent_bookings(10),
            "session_state": db.get_kv("session_state", "not_connected"),
            "watch_running": db.get_kv("watch_running", "1") == "1",
            "poll_interval": db.get_kv("poll_interval", "300"),
        })

    @app.get("/targets/new", response_class=HTMLResponse)
    def target_new(request: Request):
        if not require_user(request):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse("target_form.html", {"request": request, "target": None, "prefs": []})

    @app.post("/targets")
    async def target_create(request: Request):
        if not require_user(request):
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        app.state.db.upsert_target(
            name=form.get("name", "target"), match=form.get("match", ".*"),
            min_seats=int(form.get("min_seats", "1")), tiebreak=form.get("tiebreak", "earliest"),
            enabled=form.get("enabled") == "on", dry_run=form.get("dry_run") == "on",
            preferences=_prefs_from_form(form),
        )
        return RedirectResponse("/", status_code=303)

    @app.get("/targets/{tid}", response_class=HTMLResponse)
    def target_edit(request: Request, tid: int):
        if not require_user(request):
            return RedirectResponse("/login", status_code=303)
        db = app.state.db
        return templates.TemplateResponse("target_form.html",
            {"request": request, "target": db.get_target(tid), "prefs": db.get_preferences(tid)})

    @app.post("/targets/{tid}")
    async def target_update(request: Request, tid: int):
        if not require_user(request):
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        app.state.db.upsert_target(
            name=form.get("name", "target"), match=form.get("match", ".*"),
            min_seats=int(form.get("min_seats", "1")), tiebreak=form.get("tiebreak", "earliest"),
            enabled=form.get("enabled") == "on", dry_run=form.get("dry_run") == "on",
            preferences=_prefs_from_form(form), target_id=tid,
        )
        return RedirectResponse("/", status_code=303)

    @app.post("/targets/{tid}/delete")
    def target_delete(request: Request, tid: int):
        if not require_user(request):
            return RedirectResponse("/login", status_code=303)
        app.state.db.delete_target(tid)
        return RedirectResponse("/", status_code=303)

    @app.post("/targets/{tid}/toggle")
    async def target_toggle(request: Request, tid: int):
        if not require_user(request):
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        field = form.get("field")
        row = app.state.db.get_target(tid)
        if row and field in ("enabled", "dry_run"):
            app.state.db.set_target_flags(tid, **{field: not bool(row[field])})
        return RedirectResponse("/", status_code=303)

    @app.post("/watch/{action}")
    def watch_control(request: Request, action: str):
        if not require_user(request):
            return RedirectResponse("/login", status_code=303)
        app.state.db.set_kv("watch_running", "1" if action == "start" else "0")
        return RedirectResponse("/", status_code=303)

    @app.post("/connect")
    def connect(request: Request):
        if not require_user(request):
            return RedirectResponse("/login", status_code=303)
        # The engine loop establishes the session; requesting a connect just
        # clears any failed state so the loop retries on its next pass.
        app.state.db.set_kv("session_state", "connecting")
        return RedirectResponse("/", status_code=303)

    @app.get("/logs", response_class=HTMLResponse)
    def logs(request: Request):
        if not require_user(request):
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse("logs.html", {"request": request})

    @app.get("/api/status")
    def api_status(request: Request):
        if not require_user(request):
            return RedirectResponse("/login", status_code=303)
        db = app.state.db
        return {"session_state": db.get_kv("session_state", "not_connected"),
                "watch_running": db.get_kv("watch_running", "1") == "1"}

    @app.get("/api/events")
    def api_events(request: Request):
        if not require_user(request):
            return RedirectResponse("/login", status_code=303)
        rows = app.state.db.recent_events(100)
        return {"events": [dict(r) for r in rows]}
```

`templates/dashboard.html`:
```html
{% extends "base.html" %}{% block title %}Dashboard{% endblock %}
{% block body %}
<section>
  <h2>Session</h2>
  <p>State: <span class="pill">{{ session_state }}</span>
     Watching: <span class="pill">{{ "on" if watch_running else "off" }}</span>
     Poll: {{ poll_interval }}s</p>
  <form method="post" action="/connect" style="display:inline"><button>Connect</button></form>
  <form method="post" action="/watch/{{ 'stop' if watch_running else 'start' }}" style="display:inline">
    <button>{{ "Stop" if watch_running else "Start" }} watching</button></form>
</section>
<section>
  <h2>Watch targets <a href="/targets/new">+ add</a></h2>
  <table><tr><th>Name</th><th>Match</th><th>Prefs</th><th>Enabled</th><th>Dry-run</th><th></th></tr>
  {% for t in targets %}
    <tr>
      <td><a href="/targets/{{ t['id'] }}">{{ t['name'] }}</a></td>
      <td><code>{{ t['match'] }}</code></td>
      <td>{{ prefs_by_target[t['id']]|length }}</td>
      <td><form method="post" action="/targets/{{ t['id'] }}/toggle"><input type="hidden" name="field" value="enabled"><button>{{ "yes" if t['enabled'] else "no" }}</button></form></td>
      <td><form method="post" action="/targets/{{ t['id'] }}/toggle"><input type="hidden" name="field" value="dry_run"><button>{{ "yes" if t['dry_run'] else "no" }}</button></form></td>
      <td><form method="post" action="/targets/{{ t['id'] }}/delete" onsubmit="return confirm('Delete?')"><button>delete</button></form></td>
    </tr>
  {% endfor %}
  </table>
</section>
<section>
  <h2>Recent bookings</h2>
  <table><tr><th>When</th><th>Exam</th><th>Room</th><th>Slot</th><th>Dry-run</th></tr>
  {% for b in bookings %}
    <tr><td>{{ b['booked_at'] }}</td><td>{{ b['exam_name'] }}</td><td>{{ b['room'] }}</td><td>{{ b['slot_start'] }}</td><td>{{ "yes" if b['dry_run'] else "no" }}</td></tr>
  {% endfor %}
  </table>
</section>
{% endblock %}
```

`templates/target_form.html`:
```html
{% extends "base.html" %}{% block title %}Target{% endblock %}
{% block body %}
<h1>{{ "Edit" if target else "New" }} target</h1>
<form method="post" action="{{ ('/targets/' ~ target['id']) if target else '/targets' }}">
  <label>Name <input name="name" value="{{ target['name'] if target else '' }}"></label>
  <label>Match (regex on exam name) <input name="match" value="{{ target['match'] if target else '' }}"></label>
  <label>Min seats <input name="min_seats" type="number" value="{{ target['min_seats'] if target else 1 }}"></label>
  <label>Tiebreak
    <select name="tiebreak">
      {% for tb in ['earliest','latest','most_seats'] %}
      <option value="{{ tb }}" {{ 'selected' if target and target['tiebreak']==tb else '' }}>{{ tb }}</option>
      {% endfor %}
    </select></label>
  <label><input type="checkbox" name="enabled" {{ 'checked' if (not target) or target['enabled'] else '' }}> Enabled</label>
  <label><input type="checkbox" name="dry_run" {{ 'checked' if (not target) or target['dry_run'] else '' }}> Dry-run</label>
  <h3>Preferences (ranked; leave a row blank to ignore)</h3>
  <div id="prefs">
    {% set rows = prefs if prefs else [None, None, None] %}
    {% for p in rows %}
    <fieldset>
      <label>Location regex <input name="pref_location" value="{{ p['location'] if p and p['location'] else '' }}"></label>
      <label>Time range HH:MM-HH:MM <input name="pref_time_range" value="{{ p['time_range'] if p and p['time_range'] else '' }}"></label>
      <label>Date range YYYY-MM-DD..YYYY-MM-DD <input name="pref_date_range" value="{{ p['date_range'] if p and p['date_range'] else '' }}"></label>
      <label>Weekdays (comma, e.g. Fri,Sat) <input name="pref_weekdays" value="{{ p['weekdays'] if p and p['weekdays'] else '' }}"></label>
    </fieldset>
    {% endfor %}
  </div>
  <button type="submit">Save</button>
</form>
{% endblock %}
```

`templates/logs.html`:
```html
{% extends "base.html" %}{% block title %}Logs{% endblock %}
{% block body %}
<h1>Activity</h1>
<table id="events"><tr><th>Time</th><th>Level</th><th>Message</th></tr></table>
<script>window.__LOGS__ = true;</script>
{% endblock %}
```

`static/app.js`:
```javascript
async function refreshLogs() {
  const table = document.getElementById("events");
  if (!table) return;
  try {
    const r = await fetch("/api/events");
    const data = await r.json();
    table.innerHTML = "<tr><th>Time</th><th>Level</th><th>Message</th></tr>" +
      data.events.map(e => `<tr><td>${e.ts}</td><td>${e.level}</td><td>${e.message}</td></tr>`).join("");
  } catch (e) { /* ignore transient errors */ }
}
if (window.__LOGS__ || document.getElementById("events")) {
  refreshLogs();
  setInterval(refreshLogs, 5000);
}
```

- [ ] **Step 4: Run it, expect pass**

Run: `pytest tests/test_webapp.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add src/webapp.py templates/ static/app.js tests/test_webapp.py
git commit -m "feat: targets CRUD, dashboard, controls, logs"
```

---

## Task 7: Background engine lifecycle + deployment

**Files:**
- Modify: `src/webapp.py` (startup/shutdown launches `run_engine`)
- Modify: `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`

**Interfaces:**
- Consumes: `run_engine` (`src.engine`), `Notifier` (`src.notifier`).
- Produces: on FastAPI startup, a background `asyncio.Task` running `run_engine(db, notifier, STORAGE_STATE)`; cancelled on shutdown. Disabled when env `DISABLE_ENGINE=1` (so tests/TestClient don't launch a browser).

- [ ] **Step 1: Add lifecycle to `src/webapp.py`**

Add near the top of `create_app`, before `return app`:
```python
    import asyncio, os as _os
    from src.notifier import Notifier
    from src.engine import run_engine

    @app.on_event("startup")
    async def _start_engine():
        if _os.environ.get("DISABLE_ENGINE") == "1":
            return
        notifier = Notifier(_os.environ.get("WEBHOOK_URL"))
        storage = _os.environ.get("STORAGE_STATE", "data/storageState.json")
        app.state._engine_task = asyncio.create_task(run_engine(app.state.db, notifier, storage))

    @app.on_event("shutdown")
    async def _stop_engine():
        task = getattr(app.state, "_engine_task", None)
        if task:
            task.cancel()
```

- [ ] **Step 2: Verify TestClient still works with engine disabled**

Ensure the test fixture sets `DISABLE_ENGINE=1`. Update the `client` fixture in `tests/test_webapp.py`:
```python
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CWL_USERNAME", "alice")
    monkeypatch.setenv("CWL_PASSWORD", "pw")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("DISABLE_ENGINE", "1")
    db = Database(str(tmp_path / "state.db"))
    app = create_app(db=db)
    return TestClient(app), db
```

Run: `pytest tests/test_webapp.py -v`
Expected: PASS (8 passed) — no browser launched.

- [ ] **Step 3: Update Dockerfile**

Replace the `COPY src ./src` / `CMD` tail of `Dockerfile` with:
```dockerfile
COPY src ./src
COPY templates ./templates
COPY static ./static
ENV STORAGE_STATE=/app/data/storageState.json STATE_DB=/app/data/state.db
EXPOSE 8000
CMD ["uvicorn", "src.webapp:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 4: Update docker-compose.yml**

```yaml
services:
  snipe:
    build: .
    ports:
      - "8000:8000"
    environment:
      - WEBHOOK_URL=${WEBHOOK_URL}
      - CWL_USERNAME=${CWL_USERNAME}
      - CWL_PASSWORD=${CWL_PASSWORD}
      - SESSION_SECRET=${SESSION_SECRET}
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

- [ ] **Step 5: Update .env.example**

```
WEBHOOK_URL=https://discord.com/api/webhooks/xxx/yyy
CWL_USERNAME=your_cwl_username
CWL_PASSWORD=your_cwl_password
SESSION_SECRET=change-me-to-a-long-random-string
```

- [ ] **Step 6: Rewrite the Setup/Run sections of README.md**

Replace the "Setup"/"Login"/"Run" sections with:
```markdown
## Setup
1. `pip install -r requirements.txt && playwright install --with-deps chromium`
2. `cp .env.example .env` and set `WEBHOOK_URL`, `CWL_USERNAME`, `CWL_PASSWORD`,
   and a long random `SESSION_SECRET`.

## Run
`docker compose --env-file .env up --build` (serves the UI on port 8000).
Locally: `uvicorn src.webapp:app --host 0.0.0.0 --port 8000` with the env vars set.

## Use it
1. Open the site and **sign in with your CWL** (validated against the env creds).
2. On the dashboard click **Connect** — the bot logs into PrairieTest and sends a
   **Duo push**; approve it on your phone. State goes to `connected` and the
   session is saved (~30 days, restarts skip Duo).
3. **Add watch targets** (e.g. match `CPSC 313`, preference weekdays `Fri`). One
   target grabs every matching exam as it opens, once each.
4. Leave `dry_run` on per target until you've confirmed a run; the Discord
   webhook fires only on real bookings: `"<cwl> has had <exam> booked at <time>"`.

## Public exposure
The site carries a CWL login, so put HTTPS in front (reverse proxy such as
Caddy/nginx, or an SSH tunnel). Set a strong `SESSION_SECRET`.
```

- [ ] **Step 7: Run full suite**

Run: `pytest -q`
Expected: all tests PASS (existing + new db/engine/webapp).

- [ ] **Step 8: Commit**

```bash
git add src/webapp.py Dockerfile docker-compose.yml .env.example README.md tests/test_webapp.py
git commit -m "feat: background engine lifecycle and web deployment"
```

---

## Task 8: Live validation (dry-run) — resolves open items

**Files:** none (operational).

- [ ] **Step 1:** `docker compose --env-file .env up --build`; open the site; sign in with CWL.
- [ ] **Step 2:** Click **Connect**; approve the Duo push; confirm state → `connected` and `data/storageState.json` written.
- [ ] **Step 3:** Add a target matching a currently-open exam with `dry_run` on; wait one cycle; confirm the activity log shows a `[dry_run] would book …` line and no real reservation.
- [ ] **Step 4:** If login/booking selectors misbehave, read `data/debug/` dumps; adjust `src/auth.py` / `src/booker.py`; re-run.
- [ ] **Step 5:** Commit any selector fixes: `git commit -am "chore: tune selectors from live dry-run"`.

---

## Self-Review Notes

- **Spec coverage:** architecture/background task (Task 4, 7), SQLite state (Tasks 1-2), CWL-env auth + session cookie (Task 5), Duo-in-UI via session_state + Connect (Tasks 4, 6), webhook bookings-only + message format (Task 4), multi-exam set-and-forget (Tasks 3, 4), flat poll + jitter (Task 4), UI pages login/dashboard/target/logs (Tasks 5-6), data model (Tasks 1-2), error handling/backoff/debug dumps (Task 4, reused booker/auth), testing incl. TestClient (Tasks 1-7), deployment uvicorn + ports + rw volume + HTTPS note (Task 7), open items (Task 8). Covered.
- **Type consistency:** `Database` method names match across tasks; `Decision`, `plan_decisions`, `run_engine`, `booking_message`, `auth_config_from_env`, `check_cwl`, `create_app` used consistently; `to_target_exam` feeds `choose_session` via existing dataclasses.
- **Placeholder scan:** all code steps contain complete code; live selector tuning isolated to Task 8. The Task 5 dashboard returns a stub that Task 6 replaces (explicitly noted).
- **Deviation from spec:** legacy `src/main.py`/`config.yaml`/`watcher.run_watch` are retained (not deleted) since they're tested and harmless; the container entrypoint is the web app, satisfying "web entrypoint" without removing working code.
