# Reference: Booking page DOM (`/pt/student/exam/{examId}`)

Captured 2026-09-21 from `/pt/student/exam/87130` via structural introspection
(no token values were read; raw HTML is blocked by the browser tool because the
reserve forms carry CSRF/session tokens). This is reference data for building and
testing `parser.py` and `booker.py`.

## Session list structure

```
ul.list-group.list-group-flush
  li.list-group-item                      ← one per session (90 in this capture)
    div.container.p-0
      div.row.align-items-center
        div.col-…                         ← action column
          form[method=POST]               ← action set via JS (no static action attr)
            button.btn.btn-success.btn-sm  "Reserve this session"
              aria-label="Reserve this session on Tue, Sep 29, 11am (PDT) in ORCA: 💥HENN 203"
        div.col-…
          time.js-format-date-friendly-live-update  "Tue, Sep 29, 11am (PDT)"
        div.col-…
          span  "ORCA:"  a "💥HENN 203"    ← center + room (room may carry emoji)
        div.col-…  attributes text          "1h 15min, In-person, CfA, 150% time"
        div.col-…  availability text         "Available: 4 / 14"
```

For non-bookable sessions the action column shows a status label instead of a
`btn-success` button. Observed statuses (text):
- `Reserve this session`  → bookable (has `button.btn.btn-success.btn-sm`)
- `No available seats`     → full (`0 / N`)
- `Time limit doesn't fit` → accommodation window mismatch (`0 / 0`)
- `Not reservable for this exam`

## Reserve action (POST)

- Enclosing `<form method="POST">`, submitted by clicking the `btn-success`
  button; the action URL is wired via JS.
- Hidden field **names** (values not read): `__action`, `reservation_request_id`,
  `unsafe_session_id`, `__csrf_token`.
- Two viable booking strategies:
  1. **Click the button** by role + accessible name
     (`get_by_role("button", name="Reserve this session on <date> in <loc>")`).
     Simplest and robust; Playwright submits the form. **Default.**
  2. **Direct POST** with the four hidden fields (read fresh from the target
     row each time). Fastest for sniping, but more brittle to token/anti-CSRF
     handling. Optional optimization, only if click latency proves too slow.

## Delete action — NEVER trigger

- `button.btn.btn-danger` with text/aria "Delete reservation", inside
  `div.modal#deleteReservationModal`.
- The bot must only match `btn-success` "Reserve this session on …" buttons and
  must never open the delete modal or click `btn-danger`.

## Parser guidance

- Prefer robustness over CSS coupling: locate rows as `li.list-group-item`
  within `ul.list-group`, then extract by role/text within each row.
- For datetime, prefer a machine-readable attribute on the `<time>` element if
  present (check `datetime`/`data-*`); fall back to parsing the friendly string
  `"Tue, Sep 29, 11am (PDT)"` (note: timezone shown as PDT/PST).
- `room_clean`: strip leading emoji and whitespace from the room string before
  regex matching (e.g. `💥HENN 203` → `HENN 203`, `🎯ICCS 014` → `ICCS 014`).
- Availability: parse `"Available: X / Y"` → `available=X`, `capacity=Y`.
```
```

## Fixture TODO (implementation)

Raw HTML with tokens is blocked, so create a **sanitized** fixture during
implementation: reconstruct a small `sessions_fixture.html` from the structure
above with a handful of rows covering all four statuses (and emoji rooms), with
dummy hidden-field values. Parser tests run against that. **Done** —
`tests/fixtures/sessions_fixture.html`.

## Live validation (2026-09-21, exam 87130)

Parser selectors validated against all 90 live rows (read-only, no tokens read):
- 90/90 rows have `<time datetime=…>`; 90/90 have a room link; 90/90 have
  `Available: X / Y`.
- Status counts: 61 `No available seats`, 17 `Reserve this session`,
  11 `Time limit doesn't fit`, 1 `Not reservable for this exam`; 0 unrecognized.
- `Time limit doesn't fit` uses a plain ASCII apostrophe (matches parser).
- `datetime` attribute format is `2026-09-29T10:00:00.000-07:00` — full ISO
  **with year**, milliseconds, and offset; `datetime.fromisoformat` parses it.
- Non-reservable rows add `text-muted` to the `div.row` and place the status
  text directly in the first column div.
- Reserve `aria-label` confirmed exactly:
  `Reserve this session on Tue, Sep 29, 11am (PDT) in ORCA: 💥HENN 203`.

Still deferred to a real dry-run (needs a seeded login / a real reserve click):
- Whether clicking `Reserve this session` shows a confirmation step
  (tune `_CONFIRM_NAMES` in `src/booker.py` if so).
- Exact logged-out DOM/URL markers (tune `is_logged_out` in `src/watcher.py`).
- How a brand-new (never-reserved) exam renders under "Exams available for
  reservations" for the discovery path.
