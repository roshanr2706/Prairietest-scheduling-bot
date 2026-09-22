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
