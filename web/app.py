import os
import re
from datetime import date, timedelta
from typing import Optional

from fastapi import Depends, FastAPI, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import database as db
from handlers import (
    PRIORITY_ICONS, PRIORITY_LABELS, STATUS_ICONS, SHARE_KINDS,
    parse_priority, parse_due_date, parse_recurring, infer_tag,
    xp_bar, fmt_due, trunc,
)
from web.auth import SESSION_COOKIE, create_session_value, read_session_chat_id

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="MiguQuest Web")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.globals.update(
    PRIORITY_ICONS=PRIORITY_ICONS,
    PRIORITY_LABELS=PRIORITY_LABELS,
    STATUS_ICONS=STATUS_ICONS,
    xp_bar=xp_bar,
    fmt_due=fmt_due,
    trunc=trunc,
)


@app.on_event("startup")
def _startup():
    db.init_db()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        return RedirectResponse("/")
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def current_chat_id(request: Request) -> Optional[int]:
    return read_session_chat_id(request.cookies.get(SESSION_COOKIE))


def require_chat_id(request: Request) -> int:
    chat_id = current_chat_id(request)
    if chat_id is None:
        raise HTTPException(status_code=401)
    return chat_id


def require_same_origin(request: Request) -> None:
    """Lightweight CSRF guard: state-changing requests must originate from this app."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin is None:
        return
    origin_host = origin.split("://", 1)[-1].split("/", 1)[0]
    if origin_host != request.url.netloc:
        raise HTTPException(status_code=403, detail="Cross-site request blocked")


def safe_local_path(path: str, default: str = "/app") -> str:
    """Only allow same-app relative redirect targets (blocks open redirects)."""
    if path and path.startswith("/") and not path.startswith("//"):
        return path
    return default


def board_snapshot(chat_id: int, tag_filter: str = None, mutate: bool = True) -> dict:
    if mutate:
        db.clear_old_pins(chat_id)
        db.ensure_daily_rollover(chat_id)
    player  = db.get_or_create_player(chat_id)
    title   = db.get_title(player["level"])
    goal_ids = db.get_daily_goals(chat_id)

    todo        = db.get_quests(chat_id, status="todo", tag=tag_filter)
    in_progress = db.get_quests(chat_id, status="in_progress", tag=tag_filter)
    done_today  = db.get_completed_today(chat_id)
    today_xp    = sum(q["xp_value"] for q in done_today)
    backlog_n   = len(db.get_quests(chat_id, status="backlog"))
    active_pomo = db.get_active_pomodoro(chat_id)

    return {
        "player": player,
        "title": title,
        "to_next": 200 - (player["total_xp"] % 200),
        "goal_ids": goal_ids,
        "todo": todo,
        "in_progress": in_progress,
        "done_today": done_today,
        "today_xp": today_xp,
        "tag_filter": tag_filter,
        "backlog_n": backlog_n,
        "active_pomo": active_pomo,
    }


def backlog_snapshot(chat_id: int) -> dict:
    db.ensure_daily_rollover(chat_id)
    return {"backlog": db.get_quests(chat_id, status="backlog")}


def week_snapshot(chat_id: int) -> dict:
    player  = db.get_or_create_player(chat_id)
    quests  = db.get_completed_this_week(chat_id)
    week_xp = sum(q["xp_value"] for q in quests)
    today   = date.today()
    monday  = today - timedelta(days=today.weekday())

    tag_counts: dict = {}
    for q in quests:
        tag_counts[q["tag"]] = tag_counts.get(q["tag"], 0) + 1

    return {
        "player": player,
        "title": db.get_title(player["level"]),
        "quests": quests,
        "week_xp": week_xp,
        "week_str": f"{monday.strftime('%-d %b')} – {today.strftime('%-d %b')}",
        "tag_counts": sorted(tag_counts.items(), key=lambda x: -x[1]),
        "crit": sum(1 for q in quests if q["priority"] == "critical"),
        "high": sum(1 for q in quests if q["priority"] == "high"),
    }


# ─── Auth ─────────────────────────────────────────────────────────────────────

@app.get("/")
def index(request: Request):
    if current_chat_id(request) is not None:
        return RedirectResponse("/app")
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/auth/{token}")
def auth(request: Request, token: str):
    chat_id = db.consume_login_token(token)
    if chat_id is None:
        return templates.TemplateResponse("expired.html", {"request": request,
                                                             "reason": "This login link is invalid or has expired."},
                                           status_code=400)
    resp = RedirectResponse("/app")
    resp.set_cookie(
        SESSION_COOKIE, create_session_value(chat_id),
        max_age=30 * 24 * 3600, httponly=True, samesite="lax",
        secure=(request.url.scheme == "https"),
    )
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/")
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ─── Authenticated dashboard ──────────────────────────────────────────────────

@app.get("/app", response_class=HTMLResponse)
def dashboard(request: Request, tag: str = None, shared: str = None):
    chat_id = require_chat_id(request)
    ctx = board_snapshot(chat_id, tag_filter=tag)
    ctx["request"] = request
    if shared:
        ctx["share_link"] = str(request.url_for("public_share", token=shared))
    return templates.TemplateResponse("dashboard.html", ctx)


@app.post("/app/quest", dependencies=[Depends(require_same_origin)])
def add_quest(request: Request, text: str = Form(...)):
    chat_id = require_chat_id(request)
    priority, clean = parse_priority(text)
    clean, due_date = parse_due_date(clean)
    clean, recurring = parse_recurring(clean)
    tag = infer_tag(text)
    clean = re.sub(r"#\w+", "", clean).strip()
    db.add_quest(chat_id, clean, priority=priority, tag=tag, source="typed",
                 due_date=due_date, recurring=recurring)
    return RedirectResponse("/app", status_code=303)


@app.post("/app/quest/{quest_id}/done", dependencies=[Depends(require_same_origin)])
def done_quest(request: Request, quest_id: int):
    chat_id = require_chat_id(request)
    db.complete_quest(chat_id, quest_id)
    return RedirectResponse("/app", status_code=303)


@app.post("/app/quest/{quest_id}/begin", dependencies=[Depends(require_same_origin)])
def begin_quest(request: Request, quest_id: int):
    require_chat_id(request)
    db.update_quest_status(quest_id, "in_progress")
    return RedirectResponse("/app", status_code=303)


@app.post("/app/quest/{quest_id}/drop", dependencies=[Depends(require_same_origin)])
def drop_quest(request: Request, quest_id: int):
    require_chat_id(request)
    db.update_quest_status(quest_id, "dropped")
    return RedirectResponse("/app", status_code=303)


@app.post("/app/quest/{quest_id}/note", dependencies=[Depends(require_same_origin)])
def note_quest(request: Request, quest_id: int, note: str = Form(...)):
    require_chat_id(request)
    db.append_note(quest_id, note)
    return RedirectResponse("/app", status_code=303)


@app.get("/app/week", response_class=HTMLResponse)
def week_page(request: Request, shared: str = None):
    chat_id = require_chat_id(request)
    ctx = week_snapshot(chat_id)
    ctx["request"] = request
    if shared:
        ctx["share_link"] = str(request.url_for("public_share", token=shared))
    return templates.TemplateResponse("week.html", ctx)


@app.get("/app/backlog", response_class=HTMLResponse)
def backlog_page(request: Request):
    chat_id = require_chat_id(request)
    ctx = backlog_snapshot(chat_id)
    ctx["request"] = request
    return templates.TemplateResponse("backlog.html", ctx)


@app.post("/app/quest/{quest_id}/pull-backlog", dependencies=[Depends(require_same_origin)])
def pull_backlog(request: Request, quest_id: int):
    chat_id = require_chat_id(request)
    db.pull_from_backlog(chat_id, quest_id)
    return RedirectResponse("/app/backlog", status_code=303)


@app.post("/app/pomo/start", dependencies=[Depends(require_same_origin)])
def start_pomo(request: Request, quest_id: str = Form(None), duration: int = Form(db.POMO_DEFAULT_MINUTES)):
    chat_id = require_chat_id(request)
    qid = int(quest_id) if quest_id else None
    db.start_pomodoro(chat_id, quest_id=qid, duration_minutes=duration)
    return RedirectResponse("/app", status_code=303)


@app.post("/app/pomo/{session_id}/cancel", dependencies=[Depends(require_same_origin)])
def cancel_pomo(request: Request, session_id: int):
    chat_id = require_chat_id(request)
    db.cancel_pomodoro(chat_id, session_id)
    return RedirectResponse("/app", status_code=303)


@app.post("/app/share", dependencies=[Depends(require_same_origin)])
def make_share(request: Request, kind: str = Form(...), quest_id: str = Form(None),
               return_to: str = Form("/app")):
    chat_id = require_chat_id(request)
    if kind not in SHARE_KINDS and kind != "quest":
        raise HTTPException(status_code=400, detail="Invalid share kind")
    qid = int(quest_id) if quest_id else None
    token = db.create_share(chat_id, kind if kind != "quest" else "quest", quest_id=qid)
    return_to = safe_local_path(return_to)
    sep = "&" if "?" in return_to else "?"
    return RedirectResponse(f"{return_to}{sep}shared={token}", status_code=303)


# ─── Public share links ───────────────────────────────────────────────────────

@app.get("/s/{token}", response_class=HTMLResponse, name="public_share")
def public_share(request: Request, token: str):
    share = db.get_share(token)
    if not share:
        return templates.TemplateResponse("expired.html", {"request": request,
                                                             "reason": "This share link is invalid, expired, or revoked."},
                                           status_code=404)

    chat_id = share["chat_id"]
    kind    = share["kind"]

    if kind == "quest":
        quest = db.get_quest(share["quest_id"])
        if not quest:
            return templates.TemplateResponse("expired.html", {"request": request,
                                                                 "reason": "This quest no longer exists."},
                                               status_code=404)
        return templates.TemplateResponse("share_quest.html", {"request": request, "quest": quest})

    if kind == "week":
        ctx = week_snapshot(chat_id)
        ctx["request"] = request
        return templates.TemplateResponse("share_week.html", ctx)

    if kind == "stats":
        player = db.get_or_create_player(chat_id)
        ctx = {
            "request": request,
            "player": player,
            "title": db.get_title(player["level"]),
            "to_next": 200 - (player["total_xp"] % 200),
            "done_today": db.get_completed_today(chat_id),
        }
        ctx["today_xp"] = sum(q["xp_value"] for q in ctx["done_today"])
        return templates.TemplateResponse("share_stats.html", ctx)

    # board / today
    ctx = board_snapshot(chat_id, mutate=False)
    ctx["request"] = request
    return templates.TemplateResponse("share_board.html", ctx)
