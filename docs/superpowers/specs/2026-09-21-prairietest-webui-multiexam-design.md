# PrairieTest Snipe Bot v2 — Web UI + Multi-Exam Design Spec

**Date:** 2026-09-21
**Status:** Approved for planning
**Author:** Roshan Ramchandani
**Supersedes:** parts of `2026-09-21-prairietest-snipe-bot-design.md` (the
`config.yaml`/CLI entrypoint and single-exam watch model). The pure engine
(`parser`, `ranker`, `models`, `notifier`, `auth`) is reused.

## 1. Purpose

Turn the headless snipe bot into a single-user web app: a public-URL site,
gated by CWL login, where the user manages multiple set-and-forget exam watches,
sees live status/logs, and controls the watcher — all in the browser. One
container = one person (one CWL, one exposed URL); running it for someone else
means another container at another URL.

### Non-goals (v1)
- No multi-tenancy inside a container (no storing other people's credentials).
- No Duo automation/bypass (push approve-once, surfaced in the UI).
- No ramp/`open_time` polling — a flat interval only.

## 2. Architecture

One process, one container: a **FastAPI** app served by **uvicorn**, with the
**watcher running as an asyncio background task** on the same event loop. Shared
state is a **SQLite** database (stdlib `sqlite3`) on the data volume. The UI and
watcher share the DB and a single Playwright browser context.

```
Browser ──HTTP──> FastAPI (login, dashboard, targets CRUD, logs, controls)
                     │            ▲
                     ▼            │ reads targets / writes events + bookings
                  SQLite  <───────┤
                     ▲            │
                     │            │
             Watcher background task (flat 5-min loop) ──Playwright──> PrairieTest
```

### Reused unchanged
`src/models.py`, `src/parser.py`, `src/ranker.py`, `src/notifier.py`,
`src/auth.py` (login/Duo flow + selectors).

### New / changed
- `src/db.py` — SQLite schema + typed data-access functions.
- `src/webapp.py` — FastAPI app: routes, auth, templates, background task
  lifecycle.
- `src/engine.py` — refactored watcher: reads targets from the DB, handles
  multiple matched exams per target, records bookings/events. (Replaces the
  DB-agnostic loop previously in `watcher.py`; the pure helpers `exam_url`,
  `discover_exam_id`, `is_logged_out`, plus `_new_logged_in`/`_relogin` are
  retained/moved here.)
- `templates/` + `static/` — server-rendered pages (Jinja2) + light vanilla JS.
- Entry point becomes the web app (`uvicorn src.webapp:app`); the old
  `config.yaml`/`src/main.py` CLI path is retired. `seed_session.py` stays as an
  optional manual-seeding helper.

## 3. Auth & Duo

### UI login (CWL, validated against env)
- The login page takes CWL **username + password**.
- The backend compares them (constant-time, `hmac.compare_digest`) to the
  container env `CWL_USERNAME` / `CWL_PASSWORD`. Match → set a signed session
  cookie (`itsdangerous` / Starlette `SessionMiddleware`, secret from
  `SESSION_SECRET` env, random default per boot). No new credential is stored;
  env remains the single source.
- All routes except `/login` and `/static/*` require the cookie.

### PrairieTest login + Duo (in the UI, never on the webhook)
- Dashboard shows session state from the DB `kv` table: `not_connected`,
  `connecting`, `duo_pending`, `connected`, `auth_failed`.
- A **Connect** action triggers `auth.auto_login` in the background using the
  env CWL creds. While Duo is pending the UI shows **"Duo push sent — approve on
  your phone."** On success the state becomes `connected`, `storageState.json`
  is written, and restarts reuse it (skip Duo ~30 days). The watcher will not
  book while state is not `connected`; a mid-run logout flips state back and
  re-runs login (device trust usually skips Duo).
- **Discord webhook is reserved for bookings and hard failures only**:
  `"<CWL_USERNAME> has had <exam name> booked at <slot start> in <room>"`, and
  `booking_failed`/`auth_failed`. No `started`/`duo`/`detected` noise.

### Security notes
- `CWL_PASSWORD`, `SESSION_SECRET`, `WEBHOOK_URL` come from env / uncommitted
  `.env`. The site is public: the CWL gate is the only barrier, so it must be
  served over HTTPS in front (reverse proxy / tunnel) — documented in README.
- Operating a credentialed bot is the user's responsibility under
  PrairieTest/UBC/Duo terms.

## 4. Multi-exam set-and-forget

A **watch target**:
```
{ id, name, match (regex on exam name), min_seats, tiebreak,
  enabled (bool), dry_run (bool), preferences: [rule, ...] }
```
A preference **rule** (all filters optional, ANDed; unset = don't care):
`{ location (regex), time_start, time_end, date_start, date_end, weekdays }`.

Each cycle, for every **enabled** target:
1. From the PrairieTest home page, discover **all** exams whose name matches the
   target regex and that link to `/pt/student/exam/{id}`.
2. For each matched exam **not already in `bookings`** for this target, load its
   slot page, parse sessions, and `choose_session` by the target's preferences.
3. If a slot is chosen: book it (or dry-run). On real success, insert a
   `bookings` row (so it is never rebooked) and fire the webhook + an event.
4. The target **stays active** — it keeps catching newly-opened matching exams
   (e.g. `match: "CPSC 313"` grabs Quiz 1, then Quiz 2 when it opens) until the
   user disables or deletes it.

"Set all 313 exams to Friday" = one target `match: "CPSC 313"` with a single
preference `{ weekdays: ["Fri"] }` (optionally a fallback `{}` rule to take any
slot if no Friday seat is bookable).

## 5. Polling

Flat interval, default **300s**, with small random jitter (± up to
`poll_jitter_seconds`, default 15s) to avoid a fixed cadence. No `open_time`,
no ramp. Interval + jitter are editable in the UI (stored in `kv`).

## 6. UI pages

- **`/login`** — CWL username/password form; errors on bad creds.
- **`/` (dashboard)** — session/Duo state + **Connect** button; global
  **Start/Stop** (pause the watch loop); table of targets with inline
  **enabled** and **dry_run** toggles and edit/delete; recent bookings; current
  poll interval.
- **`/targets/new`, `/targets/{id}`** — create/edit a target and its ordered
  preference rules (add/remove/reorder rules; each rule: weekdays, time range,
  location regex, date range).
- **`/logs`** — activity feed (most-recent events), auto-refreshing via periodic
  fetch of `/api/events`.
- Small JSON endpoints back the live bits: `/api/status`, `/api/events`.

## 7. Data model (SQLite)

- `targets(id, name, match, min_seats, tiebreak, enabled, dry_run, created_at)`
- `preferences(id, target_id, position, location, time_start, time_end,
  date_start, date_end, weekdays)` — `weekdays` stored as CSV.
- `bookings(id, target_id, exam_id, exam_name, room, slot_start, cwl,
  booked_at, dry_run)`
- `events(id, ts, level, message)` — rolling activity log (capped/trimmed).
- `kv(key, value)` — `session_state`, `watch_running` (bool), `poll_interval`,
  `poll_jitter`.

A thin `src/db.py` exposes typed helpers (e.g. `list_targets()`,
`upsert_target()`, `delete_target()`, `already_booked(target_id, exam_id)`,
`record_booking(...)`, `add_event(level, msg)`, `recent_events(n)`,
`get_kv/set_kv`). `targets`+`preferences` rows convert to the existing
`TargetExam`/`PreferenceRule` dataclasses so `ranker.choose_session` is reused
verbatim.

## 8. Error handling

- Watcher tick errors: log + `add_event("error", …)`, exponential backoff
  (capped 120s), keep looping.
- Not connected / logged out: set `session_state`, skip booking, attempt
  re-login; never enter creds anywhere but the CWL/Duo flow.
- Selector/parse failure: screenshot + HTML to `data/debug/`, event logged.
- Booking race (seat gone between select and click): caught, retried next cycle.
- Never click Delete / `btn-danger` / `#deleteReservationModal`.

## 9. Testing

- Reuse existing `parser`/`ranker`/`models`/`notifier`/`config`(dataclasses)
  tests.
- `db.py`: CRUD round-trips, `already_booked`, event trim, row→dataclass mapping.
- `engine.py`: pure selection — given a home-page HTML + a set of targets +
  existing bookings, assert which (exam_id, target) pairs are chosen (using
  fixtures; no live site).
- `webapp.py` via FastAPI `TestClient`: login rejects bad CWL, accepts good;
  protected routes 302/401 without cookie; target create/edit/delete round-trips
  through the DB; toggles update rows.
- Live dry-run still validates the real login/booking selectors.

## 10. Deployment

- `Dockerfile` unchanged base; `CMD` becomes `uvicorn src.webapp:app --host
  0.0.0.0 --port 8000`.
- `docker-compose.yml`: expose `8000`, env `CWL_USERNAME`, `CWL_PASSWORD`,
  `SESSION_SECRET`, `WEBHOOK_URL`; `./data` mounted read-write (SQLite +
  storageState + debug).
- README: put HTTPS in front (reverse proxy or SSH tunnel) since the site is
  public and carries a CWL login.

## 11. Open items (resolved during implementation / first live run)

1. CWL/Duo selectors (from v1) confirmed on first real Connect.
2. Reserve→confirm click flow confirmed via dry-run.
3. Home-page rendering of newly-opened, never-reserved exams confirmed for the
   discovery path.
