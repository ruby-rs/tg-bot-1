"""Mobile- and desktop-friendly web interface for the life tracker.

Reuses the same SQLite layer (``bot.db``) as the Telegram bot, so both share
one database. Single-user auth via a password + signed session cookie.
"""
import calendar as calendar_module
import os
import secrets
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from bot import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

APP_PASSWORD = os.environ.get("WEB_PASSWORD", "changeme")
SECRET_KEY = os.environ.get("WEB_SECRET_KEY", secrets.token_hex(32))
# The web user maps to the same DB record as the bot user when this is set to
# your Telegram id; otherwise it uses its own record.
WEB_USER_TG_ID = int(os.environ.get("WEB_USER_TG_ID", "0"))
WEB_USER_NAME = os.environ.get("WEB_USER_NAME", "Веб")

MONTHS_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
STATUS_CLASS = {"done": "done", "skip": "skip", None: "none"}
STATUS_LABEL = {"done": "✅", "skip": "❌", None: "➖"}
HABIT_TYPE_LABEL = {"bool": "Да/нет", "number": "Число", "note": "Заметка"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db(os.environ.get("DB_PATH", "life_tracker.db"))
    yield


app = FastAPI(title="Life Tracker", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=60 * 60 * 24 * 30)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.globals.update(
    MONTHS_RU=MONTHS_RU, STATUS_CLASS=STATUS_CLASS, STATUS_LABEL=STATUS_LABEL,
    HABIT_TYPE_LABEL=HABIT_TYPE_LABEL,
)


def current_user_id() -> int:
    return db.get_or_create_user(WEB_USER_TG_ID, WEB_USER_NAME)


def require_login(request: Request):
    if not request.session.get("authed"):
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return True


def today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def this_month() -> str:
    return today_str()[:7]


def shift_month(year_month: str, delta: int) -> str:
    year, month = map(int, year_month.split("-"))
    month += delta
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return f"{year:04d}-{month:02d}"


def group_by_category(rows):
    grouped = OrderedDict()
    for row in rows:
        grouped.setdefault(row["category"], []).append(row)
    return list(grouped.items())


def parse_number(raw):
    if raw is None or raw == "":
        return None
    try:
        return float(str(raw).replace(",", "."))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if request.session.get("authed"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, password: str = Form(...)):
    if secrets.compare_digest(password, APP_PASSWORD):
        request.session["authed"] = True
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": "Неверный пароль"}, status_code=401
    )


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------------------
# Events (anniversaries / special dates) — countdown helper
# ---------------------------------------------------------------------------

def event_display(ev, today: str = None):
    today = today or today_str()
    ty, tm, td = map(int, today.split("-"))
    today_date = date(ty, tm, td)
    ey, em, ed = map(int, ev["event_date"].split("-"))
    event_date = date(ey, em, ed)

    if ev["recurring"]:
        def safe_date(year, month, day):
            try:
                return date(year, month, day)
            except ValueError:
                return date(year, month, 28)  # Feb 29 fallback on non-leap years

        next_occ = safe_date(today_date.year, em, ed)
        if next_occ < today_date:
            next_occ = safe_date(today_date.year + 1, em, ed)
        return {
            "mode": "countdown",
            "days": (next_occ - today_date).days,
            "years": next_occ.year - event_date.year,
        }
    return {"mode": "countup", "days": (today_date - event_date).days}


def events_context(user_id: int):
    return [{"event": ev, "display": event_display(ev)} for ev in db.get_events(user_id)]


# ---------------------------------------------------------------------------
# Panel — today's checkers
# ---------------------------------------------------------------------------

def build_day_context(user_id: int, day: str):
    rows = db.get_habit_logs_for_date(user_id, day)
    done = sum(1 for r in rows if r["type"] == "bool" and r["status"] == "done")
    total_bool = sum(1 for r in rows if r["type"] == "bool")
    entries_by_habit = {
        r["id"]: db.get_habit_entries_for_date(r["id"], day) for r in rows if r["type"] == "note"
    }
    fuel = [e for e in db.get_expenses_for_date(user_id, day) if e["station"]]
    return {
        "day": day,
        "groups": group_by_category(rows),
        "entries_by_habit": entries_by_habit,
        "done": done,
        "total": total_bool,
        "fuel": fuel,
        "is_today": day == today_str(),
    }


@app.get("/", response_class=HTMLResponse)
def panel(request: Request, _=Depends(require_login)):
    user_id = current_user_id()
    ctx = build_day_context(user_id, today_str())
    weights = db.get_weights(user_id, limit=1)
    expenses = db.expense_totals_this_month(user_id)
    ctx.update(
        {
            "request": request,
            "active": "panel",
            "latest_weight": weights[0]["weight"] if weights else None,
            "expense_total": sum(e["total"] for e in expenses) if expenses else 0,
            "events": events_context(user_id)[:3],
        }
    )
    return templates.TemplateResponse("panel.html", ctx)


@app.post("/checker/{habit_id}/toggle", response_class=HTMLResponse)
def toggle_checker(request: Request, habit_id: int, day: str = Form(...), _=Depends(require_login)):
    user_id = current_user_id()
    habit = db.get_habit(user_id, habit_id)
    if habit is not None and habit["type"] == "bool":
        db.toggle_habit_log(habit_id, day)
    ctx = build_day_context(user_id, day)
    ctx["request"] = request
    return templates.TemplateResponse("partials/checkers.html", ctx)


@app.post("/checker/{habit_id}/value", response_class=HTMLResponse)
def set_checker_value(
    request: Request, habit_id: int, day: str = Form(...), value: str = Form(""),
    _=Depends(require_login),
):
    user_id = current_user_id()
    habit = db.get_habit(user_id, habit_id)
    if habit is not None and habit["type"] == "number":
        v = parse_number(value)
        if v is None:
            db.clear_habit_value(habit_id, day)
        else:
            db.set_habit_value(habit_id, day, v)
    ctx = build_day_context(user_id, day)
    ctx["request"] = request
    return templates.TemplateResponse("partials/checkers.html", ctx)


@app.post("/checker/{habit_id}/entry", response_class=HTMLResponse)
def add_checker_entry(
    request: Request, habit_id: int, day: str = Form(...), text: str = Form(""),
    _=Depends(require_login),
):
    user_id = current_user_id()
    habit = db.get_habit(user_id, habit_id)
    if habit is not None and habit["type"] == "note" and text.strip():
        db.add_habit_entry(habit_id, day, text.strip())
    ctx = build_day_context(user_id, day)
    ctx["request"] = request
    return templates.TemplateResponse("partials/checkers.html", ctx)


@app.post("/entry/{entry_id}/delete", response_class=HTMLResponse)
def delete_entry(request: Request, entry_id: int, day: str = Form(...), _=Depends(require_login)):
    user_id = current_user_id()
    db.delete_habit_entry(user_id, entry_id)
    ctx = build_day_context(user_id, day)
    ctx["request"] = request
    return templates.TemplateResponse("partials/checkers.html", ctx)


# ---------------------------------------------------------------------------
# Calendar + full month grid (spreadsheet-style overview)
# ---------------------------------------------------------------------------

def build_grid_context(user_id: int, ym: str):
    year, month = map(int, ym.split("-"))
    days_in_month = calendar_module.monthrange(year, month)[1]
    day_strs = [f"{year:04d}-{month:02d}-{d:02d}" for d in range(1, days_in_month + 1)]

    habits = db.get_habits(user_id)
    logs = db.get_habit_logs_for_month(user_id, ym)
    note_counts = db.get_habit_entry_counts_for_month(user_id, ym)

    grouped = OrderedDict()
    for h in habits:
        grouped.setdefault(h["category"], []).append(h)

    grid_groups = []
    for category, items in grouped.items():
        grid_habits = []
        for h in items:
            cells = []
            total = 0
            for d in day_strs:
                if h["type"] == "note":
                    cnt = note_counts.get((h["id"], d), 0)
                    cells.append({"text": str(cnt) if cnt else "—", "on": bool(cnt)})
                    total += cnt
                elif h["type"] == "number":
                    row = logs.get((h["id"], d))
                    val = row["value"] if row else None
                    cells.append({"text": f"{val:g}" if val is not None else "—", "on": val is not None})
                    total += val or 0
                else:
                    row = logs.get((h["id"], d))
                    status = row["status"] if row else None
                    cells.append({"text": STATUS_LABEL[status], "on": status == "done", "skip": status == "skip"})
                    total += 1 if status == "done" else 0
            if h["type"] == "bool":
                total_text = str(int(total))
            else:
                total_text = f"{total:g}" + (f" {h['unit']}" if h["unit"] else "")
            grid_habits.append({"habit": h, "cells": cells, "total": total_text})
        grid_groups.append((category, grid_habits))

    day_headers = [
        {"num": d.split("-")[2].lstrip("0") or "0", "date": d,
         "weekday": WEEKDAYS_RU[date(year, month, int(d.split('-')[2])).weekday()],
         "is_today": d == today_str()}
        for d in day_strs
    ]
    return {"grid_groups": grid_groups, "day_headers": day_headers}


@app.get("/calendar", response_class=HTMLResponse)
def calendar_view(request: Request, ym: str = None, _=Depends(require_login)):
    user_id = current_user_id()
    ym = ym or this_month()
    year, month = map(int, ym.split("-"))
    weeks = calendar_module.monthcalendar(year, month)
    summary = db.get_habit_days_summary(user_id, ym)
    ctx = {
        "request": request, "active": "calendar",
        "ym": ym, "year": year, "month": month, "weeks": weeks,
        "summary": summary, "today": today_str(),
        "prev": shift_month(ym, -1), "next": shift_month(ym, 1),
    }
    ctx.update(build_grid_context(user_id, ym))
    return templates.TemplateResponse("calendar.html", ctx)


@app.get("/day/{day}", response_class=HTMLResponse)
def day_view(request: Request, day: str, _=Depends(require_login)):
    user_id = current_user_id()
    ctx = build_day_context(user_id, day)
    ctx.update({"request": request, "active": "calendar"})
    return templates.TemplateResponse("day.html", ctx)


# ---------------------------------------------------------------------------
# "More" hub — links to secondary pages, keeps the mobile bottom nav short
# ---------------------------------------------------------------------------

@app.get("/more", response_class=HTMLResponse)
def more_page(request: Request, _=Depends(require_login)):
    return templates.TemplateResponse("more.html", {"request": request, "active": "more"})


# ---------------------------------------------------------------------------
# Weight
# ---------------------------------------------------------------------------

@app.get("/weight", response_class=HTMLResponse)
def weight_page(request: Request, _=Depends(require_login)):
    user_id = current_user_id()
    weights = db.get_weights(user_id, limit=30)
    return templates.TemplateResponse(
        "weight.html", {"request": request, "active": "more", "weights": weights, "error": None}
    )


@app.post("/weight")
def weight_add(request: Request, value: str = Form(...), _=Depends(require_login)):
    user_id = current_user_id()
    v = parse_number(value)
    if v is None or not 20 <= v <= 300:
        weights = db.get_weights(user_id, limit=30)
        return templates.TemplateResponse(
            "weight.html",
            {"request": request, "active": "more", "weights": weights,
             "error": "Вес должен быть числом в диапазоне 20-300 кг."},
            status_code=400,
        )
    db.log_weight(user_id, v)
    return RedirectResponse("/weight", status_code=303)


@app.post("/weight/{weight_id}/delete")
def weight_delete(weight_id: int, _=Depends(require_login)):
    db.delete_weight(current_user_id(), weight_id)
    return RedirectResponse("/weight", status_code=303)


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------

@app.get("/expenses", response_class=HTMLResponse)
def expenses_page(request: Request, ym: str = None, _=Depends(require_login)):
    user_id = current_user_id()
    ym = ym or this_month()
    entries = db.get_expenses_for_month(user_id, ym)
    total = sum(e["amount"] for e in entries)
    return templates.TemplateResponse(
        "expenses.html",
        {
            "request": request, "active": "expenses",
            "ym": ym, "entries": entries, "total": total,
            "categories": db.get_categories(user_id),
            "stations": db.FUEL_STATIONS, "payments": db.PAYMENT_METHODS,
            "prev": shift_month(ym, -1), "next": shift_month(ym, 1),
            "today": today_str(), "error": None,
        },
    )


@app.post("/expenses")
def expense_add(
    request: Request,
    category_id: int = Form(...),
    amount: str = Form(...),
    note: str = Form(""),
    day: str = Form(None),
    liters: str = Form(None),
    station: str = Form(None),
    payment_method: str = Form(None),
    _=Depends(require_login),
):
    user_id = current_user_id()
    category = db.get_category(user_id, category_id)
    amt = parse_number(amount)
    if category is None or amt is None or amt <= 0:
        return _expenses_error(request, user_id, "Проверь категорию и сумму (> 0).")

    lit = parse_number(liters)
    logged_at = f"{day}T12:00:00+00:00" if day else None
    db.add_expense(
        user_id, category_id, amt, note=note or "",
        liters=lit, station=station or None,
        payment_method=payment_method or None, logged_at=logged_at,
    )
    return RedirectResponse("/expenses", status_code=303)


def _expenses_error(request, user_id, message):
    ym = this_month()
    entries = db.get_expenses_for_month(user_id, ym)
    return templates.TemplateResponse(
        "expenses.html",
        {
            "request": request, "active": "expenses", "ym": ym, "entries": entries,
            "total": sum(e["amount"] for e in entries),
            "categories": db.get_categories(user_id),
            "stations": db.FUEL_STATIONS, "payments": db.PAYMENT_METHODS,
            "prev": shift_month(ym, -1), "next": shift_month(ym, 1),
            "today": today_str(), "error": message,
        },
        status_code=400,
    )


@app.post("/expenses/{expense_id}/delete")
def expense_delete(expense_id: int, _=Depends(require_login)):
    db.delete_expense(current_user_id(), expense_id)
    return RedirectResponse("/expenses", status_code=303)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@app.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request, ym: str = None, _=Depends(require_login)):
    user_id = current_user_id()
    ym = ym or this_month()
    year, month = map(int, ym.split("-"))
    days_in_month = calendar_module.monthrange(year, month)[1]
    rows = db.get_habit_month_stats(user_id, ym)
    grouped = OrderedDict()
    for r in rows:
        grouped.setdefault(r["category"], []).append(r)
    return templates.TemplateResponse(
        "stats.html",
        {
            "request": request, "active": "more", "ym": ym,
            "groups": list(grouped.items()), "days_in_month": days_in_month,
            "prev": shift_month(ym, -1), "next": shift_month(ym, 1),
        },
    )


# ---------------------------------------------------------------------------
# Manage checkers (create / edit / delete custom items)
# ---------------------------------------------------------------------------

@app.get("/checkers", response_class=HTMLResponse)
def checkers_page(request: Request, _=Depends(require_login)):
    user_id = current_user_id()
    habits = db.get_habits(user_id)
    grouped = OrderedDict()
    for h in habits:
        grouped.setdefault(h["category"], []).append(h)
    categories = sorted({h["category"] for h in habits})
    return templates.TemplateResponse(
        "checkers.html",
        {"request": request, "active": "more", "groups": list(grouped.items()),
         "categories": categories, "error": None},
    )


@app.post("/checkers")
def checkers_add(
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    habit_type: str = Form("bool"),
    unit: str = Form(""),
    target: str = Form(""),
    _=Depends(require_login),
):
    user_id = current_user_id()
    title = title.strip()
    category = category.strip()
    if not title or not category:
        habits = db.get_habits(user_id)
        grouped = OrderedDict()
        for h in habits:
            grouped.setdefault(h["category"], []).append(h)
        return templates.TemplateResponse(
            "checkers.html",
            {"request": request, "active": "more", "groups": list(grouped.items()),
             "categories": sorted({h["category"] for h in habits}),
             "error": "Название и категория обязательны."},
            status_code=400,
        )
    db.add_habit(
        user_id, title, category, habit_type=habit_type,
        unit=unit.strip() or None, target=parse_number(target),
    )
    return RedirectResponse("/checkers", status_code=303)


@app.post("/checkers/{habit_id}/update")
def checkers_update(
    habit_id: int,
    title: str = Form(...),
    category: str = Form(...),
    unit: str = Form(""),
    target: str = Form(""),
    _=Depends(require_login),
):
    user_id = current_user_id()
    db.update_habit(
        user_id, habit_id, title=title.strip(), category=category.strip(),
        unit=unit.strip() or None, target=parse_number(target),
    )
    return RedirectResponse("/checkers", status_code=303)


@app.post("/checkers/{habit_id}/delete")
def checkers_delete(habit_id: int, _=Depends(require_login)):
    db.delete_habit(current_user_id(), habit_id)
    return RedirectResponse("/checkers", status_code=303)


# ---------------------------------------------------------------------------
# Events (anniversaries / special dates)
# ---------------------------------------------------------------------------

@app.get("/events", response_class=HTMLResponse)
def events_page(request: Request, _=Depends(require_login)):
    user_id = current_user_id()
    return templates.TemplateResponse(
        "events.html",
        {"request": request, "active": "more", "events": events_context(user_id), "error": None},
    )


@app.post("/events")
def events_add(
    request: Request,
    title: str = Form(...),
    event_date: str = Form(...),
    recurring: str = Form(None),
    emoji: str = Form("🎉"),
    _=Depends(require_login),
):
    user_id = current_user_id()
    title = title.strip()
    if not title or not event_date:
        return templates.TemplateResponse(
            "events.html",
            {"request": request, "active": "more", "events": events_context(user_id),
             "error": "Название и дата обязательны."},
            status_code=400,
        )
    db.add_event(user_id, title, event_date, recurring=bool(recurring), emoji=emoji.strip() or "🎉")
    return RedirectResponse("/events", status_code=303)


@app.post("/events/{event_id}/delete")
def events_delete(event_id: int, _=Depends(require_login)):
    db.delete_event(current_user_id(), event_id)
    return RedirectResponse("/events", status_code=303)


@app.get("/healthz")
def healthz():
    return {"ok": True}
