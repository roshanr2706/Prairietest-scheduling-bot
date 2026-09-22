# PrairieTest Snipe Bot

A single-user web app that watches PrairieTest and books exam seats by your
ranked preferences — set-and-forget across many exams. One container per person
(one CWL, one URL). See `docs/superpowers/specs/` for the full design.

## Setup
1. `pip install -r requirements.txt && playwright install --with-deps chromium`
2. `cp .env.example .env` and set `WEBHOOK_URL`, `CWL_USERNAME`, `CWL_PASSWORD`,
   and a long random `SESSION_SECRET`.

## Run
`docker compose --env-file .env up --build` (serves the UI on port 8000).
Locally: `uvicorn src.webapp:app --host 0.0.0.0 --port 8000` with the env vars set.

`data/` is mounted **read-write** for SQLite state, the saved session, and debug
dumps.

## Use it
1. Open the site and **sign in with your CWL** (validated against the env creds).
2. On the dashboard click **Connect** — the bot logs into PrairieTest and sends a
   **Duo push**; approve it on your phone. State goes to `connected` and the
   session is saved (~30 days, restarts skip Duo). Duo/status live in the UI, not
   the webhook.
3. **Add watch targets** — a target is a regex on the exam name (e.g. `CPSC 313`)
   plus ranked preference rules. One target grabs **every** matching exam as it
   opens, once each, indefinitely (set-and-forget). Example: match `CPSC 313`,
   one rule with weekdays `Fri` → any 313 exam, booked into a Friday slot.
4. Leave `dry_run` on per target until you've confirmed a run. The Discord
   webhook fires only on real bookings: `"<cwl> has had <exam> booked at <time>"`.

### Preference rules
Ranked best-first; the first rule with any open matching slot wins, `tiebreak`
breaks ties within it. Each rule's filters (all optional) are ANDed:
- **Location** — regex vs the room (emoji stripped), e.g. `ICCS`, `ICCS 01[48]`, `HENN`.
- **Time range** — `HH:MM-HH:MM` on the slot start time.
- **Date range** — `YYYY-MM-DD..YYYY-MM-DD`.
- **Weekdays** — comma list, e.g. `Fri,Sat`.

## Public exposure
The site carries a CWL login, so put HTTPS in front (a reverse proxy such as
Caddy/nginx, or an SSH tunnel) and set a strong `SESSION_SECRET`.

## Polling
Flat interval (default 300s) + jitter. Tune `poll_interval`/`poll_jitter` in the
DB `kv` table if needed.

## Safety
- `dry_run` (per target) detects + ranks + logs without booking.
- Your CWL password lives only in the environment / uncommitted `.env`; it is
  never written to the repo. `SESSION_SECRET` signs the UI cookie.
- Duo is **not** bypassed — you approve the push on your phone.
- The bot never deletes a reservation (the delete control is hard-excluded).

## Re-auth
Automatic: when the session expires the bot re-logs in; if Duo device trust has
also lapsed (~30 days) approve another push. On failure it logs `auth_failed`
and writes a screenshot + HTML to `data/debug/`.

## Optional manual seeding
On a machine with a display, `python seed_session.py` logs in interactively and
produces `data/storageState.json`. Not needed for normal operation.

## Tests
`pytest -v`
