# PrairieTest Snipe Bot

Headless bot that reuses your logged-in PrairieTest session, watches a target
exam, and books the best open seat by ranked preferences. One container per
account. See `docs/superpowers/specs/` for the full design.

## Setup
1. `pip install -r requirements.txt && playwright install chromium`
2. `cp config.example.yaml config.yaml` and edit your exam + preferences.
3. `cp .env.example .env` and set `WEBHOOK_URL`, `CWL_USERNAME`, `CWL_PASSWORD`.

No manual login step is required — the bot logs in itself (headless).

## Login (headless, first launch)
On its first run (or whenever the saved session expires) the bot:
1. Fills your CWL username/password from the environment.
2. Ticks "trust this browser 30 days" and triggers a **Duo push**.
3. Sends you a `duo_approve` notification — **approve the push on your phone
   once**. It then saves `data/storageState.json` and reuses it for ~30 days, so
   later restarts skip Duo entirely.

## Run locally
`python -m src.main`  (with `WEBHOOK_URL`, `CWL_USERNAME`, `CWL_PASSWORD` set)

## Run in Docker
`docker compose --env-file .env up --build`

`data/` is mounted **read-write** so the bot can persist the logged-in session.

## Optional manual seeding
On a machine with a display you can instead run `python seed_session.py` to log
in interactively and produce the same `data/storageState.json`. Not needed for
headless operation.

## Configuration
`config.yaml` (per account). Preferences are ranked, best first; the first rule
with any open matching slot wins, and `tiebreak` breaks ties within it. Each
rule's filters (all optional) are ANDed:
- `location` — regex, case-insensitive, matched against the room (emoji stripped),
  e.g. `ICCS`, `ICCS 01[48]`, `HENN`.
- `time_range` — `"HH:MM-HH:MM"` on the slot start time.
- `date_range` — `"YYYY-MM-DD..YYYY-MM-DD"` (or `date:` for a single day).
- `weekdays` — e.g. `["Sat", "Sun"]`.

## Safety
- `dry_run: true` (default) detects + ranks + logs without booking. Set to
  `false` only when you're ready to book for real.
- Your CWL password lives only in the environment / uncommitted `.env`
  (git-ignored); it is never written to the repo or to `data/`.
- Duo is **not** bypassed — you approve each first-launch push on your phone.
- The bot never deletes a reservation (the delete control is hard-excluded).

## Re-auth
Handled automatically. When the session expires the bot re-logs in; if Duo
device trust has also lapsed (~30 days) you'll get another `duo_approve`
notification to tap. If auto-login fails, it emits `auth_failed` and writes a
screenshot + HTML to `data/debug/` for troubleshooting.

## Tests
`pytest -v`
