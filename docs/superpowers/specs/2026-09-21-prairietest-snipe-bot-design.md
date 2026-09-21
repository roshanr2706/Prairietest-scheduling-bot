# PrairieTest Snipe Bot — Design Spec

**Date:** 2026-09-21
**Status:** Approved for planning
**Author:** Roshan Ramchandani

## 1. Purpose

Automate reserving a PrairieTest exam seat the moment one becomes available
("snipe on open"). The bot runs headless and unattended in a Linux Docker
container, reusing a saved logged-in session, polls a target exam for open
seats, and books the best available seat according to ranked, range/regex-based
preferences. It notifies the user via a Discord/Slack webhook.

One container = one person's account. Others can run their own container with
their own config and their own saved session.

### Non-goals (v1)
- No automation of Duo 2FA (see §5).
- No multi-account orchestration in a single container (structure the code so
  it is an easy later add, but v1 ships one-container-per-account).
- No password storage. The bot never enters a CWL password or approves Duo.

## 2. Target site behavior (observed 2026-09-21)

Logged-in home page `https://us.prairietest.com/pt` shows:
- **Exams available for reservations** — exams the student can newly reserve;
  each links to `/pt/student/exam/{examId}`.
- **Exam reservations** — exams already reserved; each links to
  `/pt/student/reservation/{reservationId}`, which in turn links to
  `/pt/student/exam/{examId}` ("Change or delete this reservation").

The slot picker at `/pt/student/exam/{examId}` is the **same page** for a
first-time booking and for changing an existing reservation. It lists sessions,
one row each, with a consistent shape:

```
<status>  |  Tue, Sep 29, 11am (PDT)  |  ORCA: 💥HENN 203  |  1h 15min, In-person, CfA, 150% time  |  Available: 4 / 14
```

Fields per row:
- **status** — one of:
  - `Reserve this session` → bookable; rendered as `<button type="submit">`
    whose accessible name encodes the slot, e.g.
    `"Reserve this session on Tue, Sep 29, 1pm (PDT) in ORCA: 🎯ICCS 014"`.
  - `No available seats` → full (`0 / N`).
  - `Time limit doesn't fit` → accommodation time does not fit the window
    (`0 / 0`); never bookable.
  - `Not reservable for this exam` → excluded.
- **datetime** — e.g. `Tue, Sep 29, 11am (PDT)`.
- **location** — e.g. `ORCA: 💥HENN 203` (center `ORCA`, room `💥HENN 203`).
- **attributes** — duration, proctoring, accommodations
  (`1h 15min, In-person, CfA, 150% time`).
- **availability** — `available / capacity` (e.g. `4 / 14`).

Also present, and **hard-excluded from any click**: a
`Delete this reservation` button (`type="button"`). The bot must never match
or click it.

### Known micro-unknown
Whether clicking `Reserve this session` books instantly or shows a confirmation
step is not yet observed (we deliberately did not click). The booker handles a
possible confirmation defensively, and `dry_run` mode is used to observe the
exact final step safely the first time a real booking is attempted.

## 3. Architecture

Python 3.11+ with Playwright (async API). Components are small, single-purpose
modules with clear interfaces so each is testable in isolation.

```
seed_session.py   (interactive, run on host)  → storageState.json
                                                     │  (mounted into container)
                                                     ▼
main.py ── watcher.py ──> parser.py ──> ranker.py ──> booker.py ──> notifier.py
              │                                                        ▲
              └──────────── session_guard ────────────────────────────┘
```

### 3.1 Components

1. **`seed_session.py`** (interactive; run on the user's laptop, not in the
   container). Launches a *headed* Chromium. The user logs in via CWL + Duo and
   checks "remember this device for 30 days". On success it saves Playwright
   `storageState` (cookies + localStorage) to `data/storageState.json`. This
   file is mounted read-only into the container. Re-run ~monthly when the
   session/device-trust expires.

2. **`config.py`** — loads and validates `config.yaml` (see §4). Fails fast with
   clear messages on malformed config. Compiles location regexes once.

3. **`watcher.py`** — the poll loop. Loads `storageState.json`, opens a headless
   context, and on each tick loads the target exam page (by `exam_id` if given,
   else discovers it from the home page by matching the exam name). Interval +
   random jitter between ticks; optional `open_time` ramp (poll lazily until
   ~1 min before, then tighten). Hands the page HTML to the parser.

4. **`parser.py`** — pure function: HTML → `list[Session]`. No network, no
   Playwright. `Session` is a dataclass:
   `{status, start: datetime, center, room, room_clean, duration, proctoring,
   accommodations, available, capacity, reserve_button_name}`.
   `room_clean` has emoji/whitespace stripped for regex matching.

5. **`ranker.py`** — pure function: `(list[Session], list[PreferenceRule],
   tiebreak) → Session | None`. Filters to bookable sessions
   (`status == Reserve this session` and `available >= min_seats`), then walks
   preference rules top-to-bottom; the first rule with ≥1 matching session wins,
   broken by `tiebreak`. Returns the chosen session or `None`.

6. **`booker.py`** — given a chosen `Session`, clicks the reserve button matched
   by exact accessible name (`get_by_role("button", name=...)`), handles a
   possible confirmation step defensively, and verifies success by re-reading
   the page (reservation now reflects the chosen slot). Handles the race where
   the seat is taken between select and click → returns a `TAKEN` result so the
   caller can re-scan and try the next-best slot. Honors `dry_run` (does
   everything except the final confirm click; logs what it *would* click).

7. **`notifier.py`** — posts to a Discord/Slack incoming webhook. Events:
   `started`, `exam_detected`, `booking_attempt`, `booked` (with slot details),
   `booking_failed`, `session_invalid`, `crash`. No-ops gracefully if no webhook
   configured (logs only).

8. **`session_guard`** (in `watcher.py`) — on every load, detects a redirect to
   the CWL/login page or a missing logged-in marker. On detection: emit
   `session_invalid`, stop attempting to book, back off, and keep the process
   alive so the user can drop in a fresh `storageState.json` (or exit with a
   distinct code — configurable). Never enters credentials.

9. **`main.py`** — wires components, handles SIGTERM/SIGINT for graceful
   shutdown, and applies error backoff.

### 3.2 Deployment

- **`Dockerfile`** — based on `mcr.microsoft.com/playwright/python:v1.x-jammy`
  (browsers preinstalled). Copies source, installs deps, runs `main.py`.
- **`docker-compose.yml`** — one service; mounts `./config.yaml` and
  `./data/storageState.json` (read-only), passes `WEBHOOK_URL` via env, sets
  restart policy.
- Secrets (webhook URL) come from the environment, never committed.

## 4. Configuration

`config.yaml` (one per account):

```yaml
target_exams:
  - match: "CPSC 313 Quiz 1"        # regex on exam name
    exam_id: null                    # optional: skip home-page discovery if known
    min_seats: 1                     # minimum available seats to consider bookable
    tiebreak: earliest               # earliest | latest | most_seats
    preferences:                     # ranked, best first; first rule with a match wins
      - name: "ideal: ICCS afternoon"
        location: "ICCS"             # regex, case-insensitive, vs cleaned room string
        time_range: "15:00-17:00"    # slot START within window (inclusive)
        date_range: "2026-10-01..2026-10-03"   # optional; or `date: "2026-10-02"`
        weekdays: ["Sat", "Sun"]     # optional
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
  open_time: null                    # optional ISO datetime; ramp polling near it
  ramp_interval_seconds: 2           # tight interval used within the ramp window

notify:
  webhook_url: ${WEBHOOK_URL}        # from env; Discord or Slack incoming webhook

dry_run: true                        # detect + rank + log, but DON'T click final confirm
```

### Preference filter semantics
- Any filter omitted = "don't care." An empty rule `{}` matches anything.
- **`location`** — regex, case-insensitive, matched against the emoji-stripped
  room string (`ICCS 014`, `HENN 203`, `BUCH B101`).
- **`time_range`** — `"HH:MM-HH:MM"`, filters on the slot's **start** time.
- **`date_range`** — `"YYYY-MM-DD..YYYY-MM-DD"`, or single `date:`.
- **`weekdays`** — optional list of 3-letter day names.
- **`tiebreak`** — when the winning rule matches several open slots, which to
  grab: `earliest` (default), `latest`, or `most_seats`.

## 5. Login / Duo

- The bot **reuses a saved session** and never handles credentials or Duo.
- `seed_session.py` produces `storageState.json` via a one-time interactive
  login with Duo "remember this device 30 days".
- When the session expires, `session_guard` detects it, notifies
  `session_invalid`, and stops booking attempts. The user re-runs the seeder
  and drops in a fresh `storageState.json`.

## 6. Error handling

- **Network / timeout** — retry the tick with exponential backoff (capped);
  keep looping.
- **Session expired** — see §5; notify and stop booking (no credential entry).
- **Selector/parse failure (UI changed)** — capture a screenshot + page HTML to
  `data/debug/`, emit `crash`/`booking_failed` with context, do not silently
  continue as if nothing is bookable.
- **Seat taken mid-book** — `booker` returns `TAKEN`; watcher re-scans and tries
  the next-best slot; if none, resumes polling.
- **Never** click `Delete this reservation` or any control not matched to the
  chosen slot's reserve button name.

## 7. Testing

- **`parser.py`** — unit tests against saved real HTML fixtures captured
  2026-09-21 from `/pt/student/exam/87130` (all four statuses represented).
  Zero live access needed.
- **`ranker.py`** — unit tests: ranges, regex locations, weekday/date filters,
  tiebreaks, `min_seats`, empty-rule fallback, and "no match → None".
- **`config.py`** — validation tests (bad regex, bad time_range, missing keys).
- **`notifier.py`** — tests with a mocked HTTP endpoint.
- **`dry_run` end-to-end** — safe live validation: detects, ranks, and logs the
  exact button it would click without booking. Used to resolve the §2
  confirmation-step micro-unknown before enabling real booking.

## 8. Safety & responsibility

- It is the user's own account; use complies with the user's responsibilities
  under PrairieTest/UBC terms.
- Polling stays humane: sane interval, random jitter, a single session, ramp
  only near a known open time.
- No password storage, no Duo automation, no touching Delete.

## 9. Open items to resolve during implementation

1. Confirm the exact reserve→confirm flow via `dry_run` on the next real
   booking, and finalize `booker.py` accordingly.
2. Confirm the logged-out/expired-session DOM marker for `session_guard`.
3. Confirm how a brand-new (never-reserved) exam renders on `/pt` under
   "Exams available for reservations" for the discovery path (capture when one
   is available).
