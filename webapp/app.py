"""Mobile- and desktop-friendly web interface for the life tracker.

Reuses the same SQLite layer (``bot.db``) as the Telegram bot, so both share
one database. Single-user auth via a password + signed session cookie.
"""
import calendar as calendar_module
import os
import secrets
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

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
HABIT_TYPE_LABEL = {
    "bool": "Да/нет", "number": "Число", "note": "Заметка",
    "interval": "Интервал времени", "expense": "Список расходов", "fuel": "Заправка",
}


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
    HABIT_TYPE_LABEL=HABIT_TYPE_LABEL, WORKOUT_TYPES=db.WORKOUT_TYPES,
    SIDE_JOB_TYPES=db.SIDE_JOB_TYPES, DAY_PERIODS=db.DAY_PERIODS,
    db_payment_methods=db.PAYMENT_METHODS,
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


def shift_day(day: str, delta: int) -> str:
    y, m, d = map(int, day.split("-"))
    return (date(y, m, d) + timedelta(days=delta)).isoformat()


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


def interval_minutes(start: str, end: str) -> float:
    if not start or not end:
        return 0
    try:
        sh, sm = map(int, start.split(":"))
        eh, em = map(int, end.split(":"))
    except ValueError:
        return 0
    start_min, end_min = sh * 60 + sm, eh * 60 + em
    if end_min < start_min:
        end_min += 24 * 60
    return end_min - start_min


def parse_options(raw: str):
    return [line.strip() for line in raw.replace(",", "\n").splitlines() if line.strip()]


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
# Day view (the "Сегодня" page doubles as a view for any day, with prev/next)
# ---------------------------------------------------------------------------

def build_day_context(user_id: int, day: str):
    rows = db.get_habit_logs_for_date(user_id, day)
    done = sum(1 for r in rows if r["type"] == "bool" and r["status"] == "done")
    total_bool = sum(1 for r in rows if r["type"] == "bool")

    entries_by_habit = {}
    intervals_by_habit = {}
    expense_entries_by_habit = {}
    configs_by_habit = {}
    day_num = int(day.split("-")[2])

    day_expenses = db.get_expenses_for_date(user_id, day)

    for r in rows:
        cfg = db.habit_config(r)
        configs_by_habit[r["id"]] = cfg
        if r["type"] == "note":
            entries_by_habit[r["id"]] = db.get_habit_entries_for_date(r["id"], day)
        elif r["type"] == "interval":
            intervals_by_habit[r["id"]] = db.get_habit_intervals_for_date(r["id"], day)
        elif r["type"] in ("expense", "fuel"):
            cat_slug = cfg.get("category_slug")
            wants_fuel = r["type"] == "fuel"
            matching = [
                e for e in day_expenses
                if e["category_id"] and e["category_slug"] == cat_slug
                and bool(e["station"]) == wants_fuel
            ] if cat_slug else []
            expense_entries_by_habit[r["id"]] = matching

    return {
        "day": day,
        "prev_day": shift_day(day, -1),
        "next_day": shift_day(day, 1),
        "groups": group_by_category(rows),
        "entries_by_habit": entries_by_habit,
        "intervals_by_habit": intervals_by_habit,
        "expense_entries_by_habit": expense_entries_by_habit,
        "configs_by_habit": configs_by_habit,
        "fuel_stations": db.get_known_fuel_stations(user_id),
        "done": done,
        "total": total_bool,
        "day_num": day_num,
        "is_today": day == today_str(),
    }


@app.get("/", response_class=HTMLResponse)
def panel(request: Request, day: str = None, _=Depends(require_login)):
    user_id = current_user_id()
    day = day or today_str()
    ctx = build_day_context(user_id, day)
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


@app.get("/day/{day}", response_class=HTMLResponse)
def day_view_redirect(day: str):
    """Old calendar links point here; the day view now lives on the panel itself."""
    return RedirectResponse(f"/?day={day}", status_code=303)


def _checker_response(request, user_id, day):
    ctx = build_day_context(user_id, day)
    ctx["request"] = request
    return templates.TemplateResponse("partials/checkers.html", ctx)


@app.post("/checker/{habit_id}/toggle", response_class=HTMLResponse)
def toggle_checker(request: Request, habit_id: int, day: str = Form(...), _=Depends(require_login)):
    user_id = current_user_id()
    habit = db.get_habit(user_id, habit_id)
    if habit is not None and habit["type"] == "bool":
        restrict_day = db.habit_config(habit).get("restrict_day")
        if not restrict_day or int(day.split("-")[2]) == restrict_day:
            db.toggle_habit_log(habit_id, day)
    return _checker_response(request, user_id, day)


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
    return _checker_response(request, user_id, day)


@app.post("/checker/{habit_id}/entry", response_class=HTMLResponse)
def add_checker_entry(
    request: Request, habit_id: int, day: str = Form(...), text: str = Form(""),
    _=Depends(require_login),
):
    user_id = current_user_id()
    habit = db.get_habit(user_id, habit_id)
    if habit is not None and habit["type"] == "note" and text.strip():
        db.add_habit_entry(habit_id, day, text.strip())
    return _checker_response(request, user_id, day)


@app.post("/entry/{entry_id}/delete", response_class=HTMLResponse)
def delete_entry(request: Request, entry_id: int, day: str = Form(...), _=Depends(require_login)):
    user_id = current_user_id()
    db.delete_habit_entry(user_id, entry_id)
    return _checker_response(request, user_id, day)


@app.post("/checker/{habit_id}/interval", response_class=HTMLResponse)
def add_checker_interval(
    request: Request, habit_id: int, day: str = Form(...),
    start_time: str = Form(""), end_time: str = Form(""),
    period: str = Form(""), subtype: str = Form(""), amount: str = Form(""),
    _=Depends(require_login),
):
    user_id = current_user_id()
    habit = db.get_habit(user_id, habit_id)
    if habit is not None and habit["type"] == "interval":
        db.add_habit_interval(
            habit_id, day,
            start_time=start_time or None, end_time=end_time or None,
            period=period or None, subtype=subtype.strip() or None,
            amount=parse_number(amount),
        )
    return _checker_response(request, user_id, day)


@app.post("/interval/{interval_id}/delete", response_class=HTMLResponse)
def delete_interval(request: Request, interval_id: int, day: str = Form(...), _=Depends(require_login)):
    user_id = current_user_id()
    db.delete_habit_interval(user_id, interval_id)
    return _checker_response(request, user_id, day)


@app.post("/checker/{habit_id}/expense", response_class=HTMLResponse)
def add_checker_expense(
    request: Request, habit_id: int, day: str = Form(...),
    name: str = Form(""), amount: str = Form(""),
    _=Depends(require_login),
):
    user_id = current_user_id()
    habit = db.get_habit(user_id, habit_id)
    amt = parse_number(amount)
    if habit is not None and habit["type"] == "expense" and amt is not None and amt > 0:
        cfg = db.habit_config(habit)
        category = db.get_category_by_slug(user_id, cfg.get("category_slug", ""))
        if category is not None:
            db.add_expense(
                user_id, category["id"], amt, note=name.strip(),
                logged_at=f"{day}T12:00:00+00:00",
            )
    return _checker_response(request, user_id, day)


@app.post("/checker/{habit_id}/fuel", response_class=HTMLResponse)
def add_checker_fuel(
    request: Request, habit_id: int, day: str = Form(...),
    station: str = Form(""), liters: str = Form(""),
    amount: str = Form(""), payment_method: str = Form(""),
    _=Depends(require_login),
):
    user_id = current_user_id()
    habit = db.get_habit(user_id, habit_id)
    amt = parse_number(amount)
    if habit is not None and habit["type"] == "fuel" and amt is not None and amt > 0:
        cfg = db.habit_config(habit)
        category = db.get_category_by_slug(user_id, cfg.get("category_slug", "car"))
        if category is not None:
            db.add_expense(
                user_id, category["id"], amt,
                note=f"{station}, {liters} л" if station else "",
                liters=parse_number(liters), station=station or None,
                payment_method=payment_method or None,
                logged_at=f"{day}T12:00:00+00:00",
            )
    return _checker_response(request, user_id, day)


@app.post("/expense-entry/{expense_id}/delete", response_class=HTMLResponse)
def delete_expense_entry(request: Request, expense_id: int, day: str = Form(...), _=Depends(require_login)):
    user_id = current_user_id()
    db.delete_expense(user_id, expense_id)
    return _checker_response(request, user_id, day)


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
    intervals = db.get_habit_intervals_for_month(user_id, ym)
    expenses = db.get_expenses_for_month(user_id, ym)

    interval_hours_by_day = {}
    interval_amount_by_day = {}
    for iv in intervals:
        key = (iv["habit_id"], iv["log_date"])
        interval_hours_by_day[key] = interval_hours_by_day.get(key, 0) + interval_minutes(
            iv["start_time"], iv["end_time"]
        ) / 60
        if iv["amount"]:
            interval_amount_by_day[key] = interval_amount_by_day.get(key, 0) + iv["amount"]

    expense_amount_by_day = {}
    for e in expenses:
        expense_amount_by_day.setdefault(e["category_slug"], {}).setdefault(bool(e["station"]), {})
        d = e["logged_at"][:10]
        bucket = expense_amount_by_day[e["category_slug"]][bool(e["station"])]
        bucket[d] = bucket.get(d, 0) + e["amount"]

    grouped = OrderedDict()
    for h in habits:
        grouped.setdefault(h["category"], []).append(h)

    grid_groups = []
    for category, items in grouped.items():
        grid_habits = []
        for h in items:
            cfg = db.habit_config(h)
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
                elif h["type"] == "interval":
                    hours = interval_hours_by_day.get((h["id"], d), 0)
                    cells.append({"text": f"{hours:g}ч" if hours else "—", "on": bool(hours)})
                    total += hours
                elif h["type"] in ("expense", "fuel"):
                    bucket = expense_amount_by_day.get(cfg.get("category_slug"), {}).get(h["type"] == "fuel", {})
                    amt = bucket.get(d, 0)
                    cells.append({"text": f"{amt:g}" if amt else "—", "on": bool(amt)})
                    total += amt
                else:
                    row = logs.get((h["id"], d))
                    status = row["status"] if row else None
                    cells.append({"text": STATUS_LABEL[status], "on": status == "done", "skip": status == "skip"})
                    total += 1 if status == "done" else 0
            if h["type"] == "bool":
                total_text = str(int(total))
            elif h["type"] == "interval":
                total_text = f"{total:g}ч"
            elif h["type"] in ("expense", "fuel"):
                total_text = f"{total:g}₽"
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
# Expenses — grouped by category with shares, like a personal-finance app
# ---------------------------------------------------------------------------

@app.get("/expenses", response_class=HTMLResponse)
def expenses_page(request: Request, ym: str = None, category_id: str = None, _=Depends(require_login)):
    user_id = current_user_id()
    ym = ym or this_month()
    entries = db.get_expenses_for_month(user_id, ym)
    if category_id:
        entries = [e for e in entries if str(e["category_id"]) == category_id]
    total = sum(e["amount"] for e in entries)

    by_category = OrderedDict()
    for e in db.get_expenses_for_month(user_id, ym):
        key = (e["category_id"], e["category_title"], e["category_emoji"])
        by_category[key] = by_category.get(key, 0) + e["amount"]
    grand_total = sum(by_category.values()) or 1
    breakdown = sorted(
        [
            {"id": cid, "title": title, "emoji": emoji, "amount": amt,
             "pct": round(100 * amt / grand_total)}
            for (cid, title, emoji), amt in by_category.items()
        ],
        key=lambda r: -r["amount"],
    )

    return templates.TemplateResponse(
        "expenses.html",
        {
            "request": request, "active": "expenses",
            "ym": ym, "entries": entries, "total": total, "breakdown": breakdown,
            "selected_category": int(category_id) if category_id else None,
            "categories": db.get_categories(user_id),
            "stations": db.get_known_fuel_stations(user_id), "payments": db.PAYMENT_METHODS,
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
            "total": sum(e["amount"] for e in entries), "breakdown": [], "selected_category": None,
            "categories": db.get_categories(user_id),
            "stations": db.get_known_fuel_stations(user_id), "payments": db.PAYMENT_METHODS,
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

    intervals = db.get_habit_intervals_for_month(user_id, ym)
    hours_by_habit, amount_by_habit = {}, {}
    for iv in intervals:
        hours_by_habit[iv["habit_id"]] = hours_by_habit.get(iv["habit_id"], 0) + interval_minutes(
            iv["start_time"], iv["end_time"]
        ) / 60
        if iv["amount"]:
            amount_by_habit[iv["habit_id"]] = amount_by_habit.get(iv["habit_id"], 0) + iv["amount"]

    expenses = db.get_expenses_for_month(user_id, ym)
    expense_amount_by_habit = {}
    habits_by_id = {h["id"]: h for h in db.get_habits(user_id)}
    for h in habits_by_id.values():
        if h["type"] not in ("expense", "fuel"):
            continue
        cfg = db.habit_config(h)
        total = sum(
            e["amount"] for e in expenses
            if e["category_slug"] == cfg.get("category_slug") and bool(e["station"]) == (h["type"] == "fuel")
        )
        expense_amount_by_habit[h["id"]] = total

    for r in rows:
        r["hours"] = hours_by_habit.get(r["id"], 0)
        r["interval_amount"] = amount_by_habit.get(r["id"], 0)
        r["expense_amount"] = expense_amount_by_habit.get(r["id"], 0)

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
# Manage checkers (create / edit / delete custom items) — the "constructor"
# ---------------------------------------------------------------------------

def _checkers_ctx(user_id, error=None):
    habits = db.get_habits(user_id)
    grouped = OrderedDict()
    for h in habits:
        grouped.setdefault(h["category"], []).append(h)
    return {
        "groups": list(grouped.items()),
        "categories": sorted({h["category"] for h in habits}),
        "expense_categories": db.get_categories(user_id),
        "error": error,
    }


@app.get("/checkers", response_class=HTMLResponse)
def checkers_page(request: Request, _=Depends(require_login)):
    user_id = current_user_id()
    ctx = _checkers_ctx(user_id)
    ctx.update({"request": request, "active": "more"})
    return templates.TemplateResponse("checkers.html", ctx)


@app.post("/checkers")
def checkers_add(
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    habit_type: str = Form("bool"),
    unit: str = Form(""),
    target: str = Form(""),
    has_period: str = Form(None),
    has_amount: str = Form(None),
    amount_label: str = Form(""),
    free_subtype: str = Form(None),
    subtype_label: str = Form(""),
    subtype_options: str = Form(""),
    expense_category_id: str = Form(""),
    _=Depends(require_login),
):
    user_id = current_user_id()
    title = title.strip()
    category = category.strip()
    if not title or not category:
        ctx = _checkers_ctx(user_id, "Название и категория обязательны.")
        ctx.update({"request": request, "active": "more"})
        return templates.TemplateResponse("checkers.html", ctx, status_code=400)

    config = {}
    if habit_type == "interval":
        if has_period:
            config["has_period"] = True
        if has_amount:
            config["has_amount"] = True
            config["amount_label"] = amount_label.strip() or "Сумма"
        if free_subtype:
            config["free_subtype"] = True
            config["subtype_label"] = subtype_label.strip() or "Название"
        elif subtype_options.strip():
            config["subtype_label"] = subtype_label.strip() or "Тип"
            config["subtype_options"] = parse_options(subtype_options)
    elif habit_type in ("expense", "fuel"):
        exp_cat = db.get_category(user_id, int(expense_category_id)) if expense_category_id else None
        config["category_slug"] = exp_cat["slug"] if exp_cat else "purchases"

    db.add_habit(
        user_id, title, category, habit_type=habit_type,
        unit=unit.strip() or None, target=parse_number(target), config=config,
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
