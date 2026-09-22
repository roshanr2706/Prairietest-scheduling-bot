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
- The bot never stores your password, never automates Duo, and never deletes a
  reservation.

## Re-auth
When notified `session_invalid`, re-run `python seed_session.py` to refresh
`data/storageState.json` (Duo device trust lasts ~30 days).

## Tests
`pytest -v`
