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
