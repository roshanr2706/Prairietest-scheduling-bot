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
