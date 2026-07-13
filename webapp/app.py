"""Mobile-first web interface for the life tracker.

Reuses the same SQLite layer (``bot.db``) as the Telegram bot, so both share
one database. Single-user auth via a password + signed session cookie.
"""
import calendar as calendar_module
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone

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
STATUS_CLASS = {"done": "done", "skip": "skip", None: "none"}
STATUS_LABEL = {"done": "✅", "skip": "❌", None: "➖"}

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db(os.environ.get("DB_PATH", "life_tracker.db"))
    yield


app = FastAPI(title="Life Tracker", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=60 * 60 * 24 * 30)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.globals.update(
    MONTHS_RU=MONTHS_RU, STATUS_CLASS=STATUS_CLASS, STATUS_LABEL=STATUS_LABEL
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
    grouped = {}
    order = []
    for row in rows:
        if row["category"] not in grouped:
            grouped[row["category"]] = []
            order.append(row["category"])
        grouped[row["category"]].append(row)
    return [(cat, grouped[cat]) for cat in order]


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
# Panel — today's checkers
# ---------------------------------------------------------------------------

def build_day_context(user_id: int, day: str):
    rows = db.get_habit_logs_for_date(user_id, day)
    done = sum(1 for r in rows if r["status"] == "done")
    fuel = [e for e in db.get_expenses_for_date(user_id, day) if e["station"]]
    return {
        "day": day,
        "groups": group_by_category(rows),
        "done": done,
        "total": len(rows),
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
        }
    )
    return templates.TemplateResponse("panel.html", ctx)


@app.post("/checker/{habit_id}/toggle", response_class=HTMLResponse)
def toggle_checker(request: Request, habit_id: int, day: str = Form(...), _=Depends(require_login)):
    user_id = current_user_id()
    if db.get_habit(user_id, habit_id) is not None:
        db.toggle_habit_log(habit_id, day)
    ctx = build_day_context(user_id, day)
    ctx["request"] = request
    return templates.TemplateResponse("partials/checkers.html", ctx)


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

@app.get("/calendar", response_class=HTMLResponse)
def calendar_view(request: Request, ym: str = None, _=Depends(require_login)):
    user_id = current_user_id()
    ym = ym or this_month()
    year, month = map(int, ym.split("-"))
    weeks = calendar_module.monthcalendar(year, month)
    summary = db.get_habit_days_summary(user_id, ym)
    return templates.TemplateResponse(
        "calendar.html",
        {
            "request": request, "active": "calendar",
            "ym": ym, "year": year, "month": month, "weeks": weeks,
            "summary": summary, "today": today_str(),
            "prev": shift_month(ym, -1), "next": shift_month(ym, 1),
        },
    )


@app.get("/day/{day}", response_class=HTMLResponse)
def day_view(request: Request, day: str, _=Depends(require_login)):
    user_id = current_user_id()
    ctx = build_day_context(user_id, day)
    ctx.update({"request": request, "active": "calendar"})
    return templates.TemplateResponse("day.html", ctx)


# ---------------------------------------------------------------------------
# Weight
# ---------------------------------------------------------------------------

@app.get("/weight", response_class=HTMLResponse)
def weight_page(request: Request, _=Depends(require_login)):
    user_id = current_user_id()
    weights = db.get_weights(user_id, limit=30)
    return templates.TemplateResponse(
        "weight.html", {"request": request, "active": "weight", "weights": weights, "error": None}
    )


@app.post("/weight")
def weight_add(request: Request, value: str = Form(...), _=Depends(require_login)):
    user_id = current_user_id()
    try:
        v = float(value.replace(",", "."))
    except ValueError:
        v = None
    if v is None or not 20 <= v <= 300:
        weights = db.get_weights(user_id, limit=30)
        return templates.TemplateResponse(
            "weight.html",
            {"request": request, "active": "weight", "weights": weights,
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
    try:
        amt = float(amount.replace(",", "."))
    except (ValueError, AttributeError):
        amt = None
    if category is None or amt is None or amt <= 0:
        return _expenses_error(request, user_id, "Проверь категорию и сумму (> 0).")

    lit = None
    if liters:
        try:
            lit = float(liters.replace(",", "."))
        except ValueError:
            lit = None
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
    stats_rows = [
        {"title": r["title"], "category": r["category"], "done": r["done_count"] or 0,
         "total": days_in_month}
        for r in rows
    ]
    return templates.TemplateResponse(
        "stats.html",
        {
            "request": request, "active": "stats", "ym": ym,
            "groups": _group_stats(stats_rows),
            "prev": shift_month(ym, -1), "next": shift_month(ym, 1),
        },
    )


def _group_stats(stats_rows):
    grouped = {}
    order = []
    for r in stats_rows:
        if r["category"] not in grouped:
            grouped[r["category"]] = []
            order.append(r["category"])
        grouped[r["category"]].append(r)
    return [(cat, grouped[cat]) for cat in order]


@app.get("/healthz")
def healthz():
    return {"ok": True}
