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

    def _prefs_from_form(form) -> list[dict]:
        locs = form.getlist("pref_location")
        times = form.getlist("pref_time_range")
        dates = form.getlist("pref_date_range")
        days = form.getlist("pref_weekdays")
        prefs = []
        for i in range(max(len(locs), len(times), len(dates), len(days), 0)):
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
        targets = db.list_targets()
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "targets": targets,
            "prefs_by_target": {r["id"]: db.get_preferences(r["id"]) for r in targets},
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

    @app.on_event("startup")
    async def _start_engine():
        import asyncio
        if os.environ.get("DISABLE_ENGINE") == "1":
            return
        from src.notifier import Notifier
        from src.engine import run_engine
        notifier = Notifier(os.environ.get("WEBHOOK_URL"))
        storage = os.environ.get("STORAGE_STATE", "data/storageState.json")
        app.state._engine_task = asyncio.create_task(run_engine(app.state.db, notifier, storage))

    @app.on_event("shutdown")
    async def _stop_engine():
        task = getattr(app.state, "_engine_task", None)
        if task:
            task.cancel()

    return app

app = create_app()
