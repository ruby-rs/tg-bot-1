import calendar as calendar_module
import logging
import os
from collections import OrderedDict
from datetime import datetime

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import db
from .formatting import escape_md, sparkline, table
from .tips import NUTRITION_TIPS, WELCOME_TEXT

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

MD = ParseMode.MARKDOWN_V2

# Two persistent message "slots" per chat:
#  - PANEL_SLOT: the always-visible dashboard; only ever edited in place.
#  - AUX_SLOT: a transient message used for data-entry flows and secondary
#    views; deleted once the flow finishes.
PANEL_SLOT = "panel"
AUX_SLOT = "aux"

# Awaiting-input flags stored in user_data during multi-step flows.
AWAITING_TASK = "awaiting_task_category"
AWAITING_EXPENSE = "awaiting_expense_category"
AWAITING_WEIGHT = "awaiting_weight"
AWAITING_FUEL_STATION = "awaiting_fuel_station"
AWAITING_FUEL_LITERS = "awaiting_fuel_liters"
AWAITING_FUEL_AMOUNT = "awaiting_fuel_amount"
FUEL_DRAFT = "fuel_draft"
INPUT_FLAGS = (
    AWAITING_TASK,
    AWAITING_EXPENSE,
    AWAITING_WEIGHT,
    AWAITING_FUEL_STATION,
    AWAITING_FUEL_LITERS,
    AWAITING_FUEL_AMOUNT,
)

HABIT_STATUS_EMOJI = {"done": "✅", "skip": "❌", None: "➖"}

MONTHS_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def shift_month(year_month: str, delta: int) -> str:
    year, month = map(int, year_month.split("-"))
    month += delta
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return f"{year:04d}-{month:02d}"


# ---------------------------------------------------------------------------
# Message plumbing
# ---------------------------------------------------------------------------

async def show_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Renders / refreshes the persistent panel message in place."""
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    text, markup = build_panel_view(user_id, user.first_name)
    await _upsert_slot(update, context, PANEL_SLOT, text, markup, MD)


async def show_aux(update, context, text, reply_markup=None, parse_mode=MD):
    """Shows the transient auxiliary message (reusing it if one is open)."""
    await _upsert_slot(update, context, AUX_SLOT, text, reply_markup, parse_mode)


async def _upsert_slot(update, context, slot, text, reply_markup, parse_mode):
    """Edits the message stored for ``slot`` in place, or sends a new one.

    The message id is persisted in the database (not in-memory chat_data),
    so the panel/aux message is still found and reused after the bot process
    restarts (e.g. on every deploy) — otherwise each restart would orphan the
    old message and a fresh one would be sent, duplicating the panel.

    If the edit fails only because the content is identical ("message is not
    modified"), the existing message is kept — sending a fresh one there would
    also duplicate the panel/aux message in the chat.
    """
    chat_id = update.effective_chat.id
    message_id = db.get_slot_message(chat_id, slot)
    if message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text,
                parse_mode=parse_mode, reply_markup=reply_markup,
            )
            return
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return
    msg = await context.bot.send_message(
        chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup
    )
    db.set_slot_message(chat_id, slot, msg.message_id)


async def clear_aux(update, context):
    """Deletes the transient auxiliary message if one is open."""
    chat_id = update.effective_chat.id
    message_id = db.get_slot_message(chat_id, AUX_SLOT)
    if message_id:
        db.clear_slot_message(chat_id, AUX_SLOT)
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramError:
            pass


async def edit_aux(query, text, reply_markup=None, parse_mode=MD):
    """Edits the aux message the given callback button lives on."""
    try:
        await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except BadRequest:
        pass


async def delete_user_message(update):
    """Removes the user's own input message to keep the chat tidy (private chats allow this)."""
    try:
        await update.message.delete()
    except BadRequest:
        pass


def clear_flags(context):
    for flag in INPUT_FLAGS:
        context.user_data.pop(flag, None)
    context.user_data.pop(FUEL_DRAFT, None)


# ---------------------------------------------------------------------------
# Panel (persistent dashboard of today's checkers)
# ---------------------------------------------------------------------------

def build_panel_view(user_id: int, first_name: str):
    today = db.today()
    # The bot only supports the simple done/skip/none cycle; number- and
    # note-type checkers (created from the web UI) are edited there instead.
    rows = [r for r in db.get_habit_logs_for_date(user_id, today) if r["type"] == "bool"]
    done = sum(1 for r in rows if r["status"] == "done")
    total = len(rows)

    date_h = escape_md(datetime.now().strftime("%d.%m.%Y"))
    lines = [f"📊 *{escape_md(first_name)}* · {date_h}", ""]
    lines.append(f"Отмечено сегодня: *{done}/{total}*")

    weights = db.get_weights(user_id, limit=7)
    if weights:
        ordered = [w["weight"] for w in reversed(weights)]
        wline = f"⚖️ *{escape_md(ordered[-1])} кг* `{sparkline(ordered)}`"
        if len(ordered) >= 2:
            diff = round(ordered[-1] - ordered[-2], 1)
            sign = "+" if diff >= 0 else ""
            wline += f" \\({escape_md(sign + str(diff))}\\)"
        lines.append(wline)

    expenses = db.expense_totals_this_month(user_id)
    if expenses:
        total_exp = sum(e["total"] for e in expenses)
        lines.append(f"💸 Расходы за месяц: *{escape_md(f'{total_exp:.0f}')} ₽*")

    lines.append("")
    lines.append("_Нажимай, чтобы отметить за сегодня \\(✅ сделал · ❌ пропустил · ➖ нет\\):_")

    buttons = []
    row_buf = []
    for r in rows:
        emoji = HABIT_STATUS_EMOJI[r["status"]]
        row_buf.append(
            InlineKeyboardButton(f"{emoji} {r['title'][:16]}", callback_data=f"ptgl:{r['id']}")
        )
        if len(row_buf) == 2:
            buttons.append(row_buf)
            row_buf = []
    if row_buf:
        buttons.append(row_buf)

    buttons.append(
        [
            InlineKeyboardButton("💸 Расход", callback_data="quickexp"),
            InlineKeyboardButton("⚖️ Вес", callback_data="logweight"),
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton("📅 Календарь", callback_data="quicknav:calendar"),
            InlineKeyboardButton("📊 Статистика", callback_data="quicknav:habitstats"),
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton("📋 Задачи", callback_data="quicknav:tasks"),
            InlineKeyboardButton("🍗 Советы", callback_data="quicknav:tips"),
        ]
    )
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.get_or_create_user(user.id, user.full_name)
    await update.message.reply_text(WELCOME_TEXT, parse_mode=MD)
    await show_panel(update, context)


async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_panel(update, context)


async def panel_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles today's checker straight from the panel and refreshes it in place."""
    query = update.callback_query
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    habit_id = int(query.data.split(":")[1])
    habit = db.get_habit(user_id, habit_id)
    if habit is None or habit["type"] != "bool":
        await query.answer("Пункт не найден")
        return
    new_status = db.toggle_habit_log(habit_id, db.today())
    await query.answer(HABIT_STATUS_EMOJI[None if new_status == "none" else new_status])
    text, markup = build_panel_view(user_id, user.first_name)
    await edit_aux(query, text, reply_markup=markup)  # button lives on the panel message


# ---------------------------------------------------------------------------
# Weight entry (transient aux flow)
# ---------------------------------------------------------------------------

async def log_weight_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    clear_flags(context)
    context.user_data[AWAITING_WEIGHT] = True
    await show_aux(
        update, context,
        "⚖️ Напиши вес в кг, например: 61.5",
        reply_markup=cancel_keyboard(), parse_mode=None,
    )


async def weight_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    if not context.args:
        clear_flags(context)
        context.user_data[AWAITING_WEIGHT] = True
        await show_aux(
            update, context, "⚖️ Напиши вес в кг, например: 61.5",
            reply_markup=cancel_keyboard(), parse_mode=None,
        )
        return
    try:
        value = float(context.args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("Введи число, например: /weight 61.5")
        return
    if not 20 <= value <= 300:
        await update.message.reply_text("Вес должен быть в диапазоне 20-300 кг.")
        return
    db.log_weight(user_id, value)
    await delete_user_message(update)
    await show_panel(update, context)


# ---------------------------------------------------------------------------
# Expense entry (transient aux flow)
# ---------------------------------------------------------------------------

def category_keyboard(user_id: int, prefix: str) -> InlineKeyboardMarkup:
    cats = db.get_categories(user_id)
    buttons = [
        [InlineKeyboardButton(f"{c['emoji']} {c['title']}", callback_data=f"{prefix}:{c['id']}")]
        for c in cats
    ]
    buttons.append([InlineKeyboardButton("✖️ Закрыть", callback_data="auxclose")])
    return InlineKeyboardMarkup(buttons)


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✖️ Отмена", callback_data="auxclose")]])


def fuel_station_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(name, callback_data=f"fuelstation:{name}")] for name in db.FUEL_STATIONS
    ]
    buttons.append([InlineKeyboardButton("✏️ Другая заправка", callback_data="fuelstation:custom")])
    buttons.append([InlineKeyboardButton("✖️ Отмена", callback_data="auxclose")])
    return InlineKeyboardMarkup(buttons)


def payment_method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(name, callback_data=f"fuelpay:{name}") for name in db.PAYMENT_METHODS]]
    )


async def expense_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens the expense category chooser in the aux message."""
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    clear_flags(context)
    if update.callback_query:
        await update.callback_query.answer()
    await show_aux(
        update, context, "💸 Выбери категорию расхода:",
        reply_markup=category_keyboard(user_id, "expcat"), parse_mode=None,
    )


async def expense_category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    category_id = int(query.data.split(":")[1])
    category = db.get_category(user_id, category_id)
    if category is None:
        await edit_aux(query, "Категория не найдена.", reply_markup=cancel_keyboard(), parse_mode=None)
        return
    if category["slug"] == "car":
        context.user_data[FUEL_DRAFT] = {"category_id": category_id}
        await edit_aux(query, "⛽️ Выбери заправку:", reply_markup=fuel_station_keyboard(), parse_mode=None)
        return
    context.user_data[AWAITING_EXPENSE] = category_id
    await edit_aux(
        query, f"💸 {category['emoji']} {category['title']}\nНапиши сумму и комментарий, например: 1500 продукты",
        reply_markup=cancel_keyboard(), parse_mode=None,
    )


async def fuel_station_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if FUEL_DRAFT not in context.user_data:
        await edit_aux(query, "Сессия истекла.", reply_markup=cancel_keyboard(), parse_mode=None)
        return
    station = query.data.split(":", 1)[1]
    if station == "custom":
        context.user_data[AWAITING_FUEL_STATION] = True
        await edit_aux(query, "Напиши название заправки:", reply_markup=cancel_keyboard(), parse_mode=None)
        return
    context.user_data[FUEL_DRAFT]["station"] = station
    context.user_data[AWAITING_FUEL_LITERS] = True
    await edit_aux(query, f"⛽️ {station}\nСколько литров залил?", reply_markup=cancel_keyboard(), parse_mode=None)


async def fuel_payment_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    draft = context.user_data.pop(FUEL_DRAFT, None)
    if not draft or "amount" not in draft:
        await query.answer("Сессия истекла")
        await clear_aux(update, context)
        await show_panel(update, context)
        return
    category = db.get_category(user_id, draft["category_id"])
    if category is None:
        await query.answer("Категория не найдена")
        await clear_aux(update, context)
        await show_panel(update, context)
        return
    payment_method = query.data.split(":", 1)[1]
    log_date = draft.get("date")
    logged_at = f"{log_date}T12:00:00+00:00" if log_date else None
    db.add_expense(
        user_id, draft["category_id"], draft["amount"],
        note=f"{draft['station']}, {draft['liters']} л",
        liters=draft["liters"], station=draft["station"],
        payment_method=payment_method, logged_at=logged_at,
    )
    await query.answer("Записано!")
    clear_flags(context)
    if log_date:
        text, markup = build_day_view(user_id, log_date)
        await edit_aux(query, text, reply_markup=markup)
    else:
        await clear_aux(update, context)
    await show_panel(update, context)


# ---------------------------------------------------------------------------
# Tasks (secondary: free-text to-dos behind a button)
# ---------------------------------------------------------------------------

def build_task_list(user_id: int, category):
    tasks = db.get_tasks(user_id, category_id=category["id"])
    lines = [f"{category['emoji']} *{escape_md(category['title'])}*", ""]
    if not tasks:
        lines.append("_Задач пока нет._")
    buttons = []
    for t in tasks:
        mark = "✅" if t["status"] == "done" else "⬜️"
        lines.append(f"{mark} {escape_md(t['title'])}")
        if t["status"] != "done":
            buttons.append(
                [
                    InlineKeyboardButton(f"✅ {t['title'][:18]}", callback_data=f"done:{t['id']}"),
                    InlineKeyboardButton("🗑", callback_data=f"del:{t['id']}"),
                ]
            )
    buttons.append(
        [InlineKeyboardButton("➕ Добавить задачу", callback_data=f"taskadd:{category['id']}")]
    )
    buttons.append(
        [
            InlineKeyboardButton("◀️ Категории", callback_data="taskmenu"),
            InlineKeyboardButton("✖️ Закрыть", callback_data="auxclose"),
        ]
    )
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def tasks_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens the task category chooser in the aux message."""
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    clear_flags(context)
    if update.callback_query:
        await update.callback_query.answer()
    await show_aux(
        update, context, "📋 Выбери категорию задач:",
        reply_markup=category_keyboard(user_id, "viewcat"), parse_mode=None,
    )


async def tasks_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    category_id = int(query.data.split(":")[1])
    category = db.get_category(user_id, category_id)
    if category is None:
        await edit_aux(query, "Категория не найдена.", reply_markup=cancel_keyboard(), parse_mode=None)
        return
    text, markup = build_task_list(user_id, category)
    await edit_aux(query, text, reply_markup=markup)


async def task_add_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category_id = int(query.data.split(":")[1])
    context.user_data[AWAITING_TASK] = category_id
    await edit_aux(query, "✍️ Напиши текст задачи следующим сообщением.",
                   reply_markup=cancel_keyboard(), parse_mode=None)


async def task_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Готово! 🎉")
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    task_id = int(query.data.split(":")[1])
    task = db.get_task(user_id, task_id)
    db.complete_task(user_id, task_id)
    if task:
        category = db.get_category(user_id, task["category_id"])
        text, markup = build_task_list(user_id, category)
        await edit_aux(query, text, reply_markup=markup)


async def task_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Удалено")
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    task_id = int(query.data.split(":")[1])
    task = db.get_task(user_id, task_id)
    category = db.get_category(user_id, task["category_id"]) if task else None
    db.delete_task(user_id, task_id)
    if category:
        text, markup = build_task_list(user_id, category)
        await edit_aux(query, text, reply_markup=markup)


# ---------------------------------------------------------------------------
# Calendar (secondary view in aux)
# ---------------------------------------------------------------------------

def _group_by_category(rows):
    grouped = OrderedDict()
    for row in rows:
        grouped.setdefault(row["category"], []).append(row)
    return grouped


def build_calendar_view(user_id: int, year_month: str):
    year, month = map(int, year_month.split("-"))
    weeks = calendar_module.monthcalendar(year, month)
    summary = db.get_habit_days_summary(user_id, year_month)
    today_str = db.today()

    buttons = []
    for week in weeks:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="noop"))
                continue
            date_str = f"{year:04d}-{month:02d}-{day:02d}"
            label = str(day)
            if summary.get(date_str):
                label = f"{label}✅"
            if date_str == today_str:
                label = f"[{label}]"
            row.append(InlineKeyboardButton(label, callback_data=f"calday:{date_str}"))
        buttons.append(row)
    buttons.append(
        [
            InlineKeyboardButton("◀️", callback_data=f"calmonth:{shift_month(year_month, -1)}"),
            InlineKeyboardButton("▶️", callback_data=f"calmonth:{shift_month(year_month, 1)}"),
        ]
    )
    buttons.append([InlineKeyboardButton("✖️ Закрыть", callback_data="auxclose")])

    text = (
        f"📅 *{escape_md(MONTHS_RU[month - 1])} {year}*\n"
        "Выбери день, чтобы отметить пункты и заправки\\."
    )
    return text, InlineKeyboardMarkup(buttons)


def build_day_view(user_id: int, log_date: str):
    # Same restriction as build_panel_view: only bool-type checkers here.
    rows = [r for r in db.get_habit_logs_for_date(user_id, log_date) if r["type"] == "bool"]
    lines = [f"📅 *{escape_md(log_date)}*", ""]
    buttons = []
    for category, items in _group_by_category(rows).items():
        lines.append(f"— *{escape_md(category)}* —")
        for h in items:
            emoji = HABIT_STATUS_EMOJI[h["status"]]
            lines.append(f"{emoji} {escape_md(h['title'])}")
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"{emoji} {h['title'][:24]}", callback_data=f"habittgl:{log_date}:{h['id']}"
                    )
                ]
            )
        lines.append("")

    fuel_entries = [e for e in db.get_expenses_for_date(user_id, log_date) if e["station"]]
    if fuel_entries:
        lines.append("— *⛽️ Заправки* —")
        for e in fuel_entries:
            lines.append(
                f"{escape_md(e['station'])}: {escape_md(e['liters'])} л, "
                f"{escape_md(e['amount'])} ₽, {escape_md(e['payment_method'])}"
            )
        lines.append("")

    buttons.append([InlineKeyboardButton("⛽️ Добавить заправку", callback_data=f"calfuelstart:{log_date}")])
    buttons.append(
        [
            InlineKeyboardButton("◀️ Календарь", callback_data=f"calmonth:{log_date[:7]}"),
            InlineKeyboardButton("✖️ Закрыть", callback_data="auxclose"),
        ]
    )
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def calendar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    if update.callback_query:
        await update.callback_query.answer()
    text, markup = build_calendar_view(user_id, db.today()[:7])
    await show_aux(update, context, text, reply_markup=markup)


async def calendar_month_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    year_month = query.data.split(":", 1)[1]
    text, markup = build_calendar_view(user_id, year_month)
    await edit_aux(query, text, reply_markup=markup)


async def calendar_day_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    log_date = query.data.split(":", 1)[1]
    text, markup = build_day_view(user_id, log_date)
    await edit_aux(query, text, reply_markup=markup)


async def habit_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle a checker for a specific day from the calendar day view (aux)."""
    query = update.callback_query
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    _, log_date, habit_id_str = query.data.split(":")
    habit_id = int(habit_id_str)
    habit = db.get_habit(user_id, habit_id)
    if habit is None or habit["type"] != "bool":
        await query.answer("Пункт не найден")
        return
    new_status = db.toggle_habit_log(habit_id, log_date)
    await query.answer(HABIT_STATUS_EMOJI[None if new_status == "none" else new_status])
    text, markup = build_day_view(user_id, log_date)
    await edit_aux(query, text, reply_markup=markup)
    if log_date == db.today():
        await show_panel(update, context)


async def cal_fuel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    log_date = query.data.split(":", 1)[1]
    car_category = next((c for c in db.get_categories(user_id) if c["slug"] == "car"), None)
    if car_category is None:
        await edit_aux(query, "Категория «Машина» не найдена.", reply_markup=cancel_keyboard(), parse_mode=None)
        return
    context.user_data[FUEL_DRAFT] = {"category_id": car_category["id"], "date": log_date}
    await edit_aux(query, "⛽️ Выбери заправку:", reply_markup=fuel_station_keyboard(), parse_mode=None)


# ---------------------------------------------------------------------------
# Read-only secondary views (aux)
# ---------------------------------------------------------------------------

async def weight_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    if update.callback_query:
        await update.callback_query.answer()
    weights = db.get_weights(user_id, limit=10)
    if not weights:
        await show_aux(update, context, "Пока нет записей веса. Используй кнопку ⚖️ Вес.",
                       reply_markup=cancel_keyboard(), parse_mode=None)
        return
    ordered = list(reversed(weights))
    values = [w["weight"] for w in ordered]
    lines = ["⚖️ *Динамика веса*", "", f"`{sparkline(values)}`", "```",
             table([(w["logged_at"][:10], f"{w['weight']} кг") for w in ordered]), "```"]
    change = round(values[-1] - values[0], 1)
    sign = "+" if change >= 0 else ""
    lines.append(f"Изменение за период: *{escape_md(sign + str(change))} кг*")
    await show_aux(update, context, "\n".join(lines), reply_markup=close_keyboard())


async def expenses_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    if update.callback_query:
        await update.callback_query.answer()
    totals = db.expense_totals_this_month(user_id)
    if not totals:
        await show_aux(update, context, "В этом месяце расходов ещё не записано.",
                       reply_markup=close_keyboard(), parse_mode=None)
        return
    rows = [(f"{e['emoji']} {e['title']}", f"{e['total']:.0f}") for e in totals]
    rows.append(("Итого", f"{sum(e['total'] for e in totals):.0f}"))
    lines = ["💸 *Расходы за месяц*", "```", table(rows), "```"]
    await show_aux(update, context, "\n".join(lines), reply_markup=close_keyboard())


async def habitstats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    if update.callback_query:
        await update.callback_query.answer()
    year_month = db.today()[:7]
    rows = [r for r in db.get_habit_month_stats(user_id, year_month) if r["type"] == "bool"]
    lines = [f"📊 *Статистика за {escape_md(year_month)}*", ""]
    for category, items in _group_by_category(rows).items():
        rows_table = [(h["title"], str(h["done_count"] or 0)) for h in items]
        lines.append(f"— *{escape_md(category)}* —")
        lines.append("```")
        lines.append(table(rows_table))
        lines.append("```")
    await show_aux(update, context, "\n".join(lines), reply_markup=close_keyboard())


async def tips_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
    await show_aux(update, context, NUTRITION_TIPS, reply_markup=close_keyboard())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT, parse_mode=MD)


async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes recent messages in the chat and re-shows a fresh panel.

    Telegram has no "clear history" API, so we walk back from the /clear
    message and try to delete each preceding message id. Deletion only works
    for messages sent within the last 48 hours (Telegram limit); older ones
    are skipped. Any Telegram error per message (not just BadRequest — also
    rate limits, forbidden, etc.) is swallowed so one bad id can't abort the
    whole loop, and the panel is always re-shown afterwards even if some
    deletions failed.
    """
    chat_id = update.effective_chat.id
    last_id = update.message.message_id
    # Forget tracked slots so a brand-new panel is created afterwards.
    db.clear_slot_message(chat_id, PANEL_SLOT)
    db.clear_slot_message(chat_id, AUX_SLOT)
    try:
        for message_id in range(last_id, max(last_id - 100, 0), -1):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            except TelegramError:
                pass
    finally:
        await show_panel(update, context)


def close_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✖️ Закрыть", callback_data="auxclose")]])


# ---------------------------------------------------------------------------
# Quick navigation from panel buttons + aux close
# ---------------------------------------------------------------------------

QUICK_NAV_HANDLERS = {
    "tasks": tasks_menu,
    "calendar": calendar_cmd,
    "habitstats": habitstats_cmd,
    "expenses": expenses_report,
    "weightstats": weight_stats,
    "tips": tips_cmd,
}


async def quick_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = update.callback_query.data.split(":", 1)[1]
    handler = QUICK_NAV_HANDLERS.get(action)
    if handler is not None:
        await handler(update, context)
    else:
        await update.callback_query.answer()


async def aux_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    clear_flags(context)
    await clear_aux(update, context)
    await show_panel(update, context)


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


# ---------------------------------------------------------------------------
# Free text (multi-step input) — always tidies up: deletes user's message,
# clears the aux prompt, refreshes the panel.
# ---------------------------------------------------------------------------

async def free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)

    if AWAITING_WEIGHT in context.user_data:
        try:
            value = float(update.message.text.strip().replace(",", "."))
        except ValueError:
            await update.message.reply_text("Введи число, например: 61.5")
            return
        if not 20 <= value <= 300:
            await update.message.reply_text("Вес должен быть в диапазоне 20-300 кг.")
            return
        context.user_data.pop(AWAITING_WEIGHT)
        db.log_weight(user_id, value)
        await delete_user_message(update)
        await clear_aux(update, context)
        await show_panel(update, context)
        return

    if AWAITING_TASK in context.user_data:
        category_id = context.user_data.pop(AWAITING_TASK)
        title = update.message.text.strip()
        if not title:
            await update.message.reply_text("Текст задачи не может быть пустым.")
            return
        category = db.get_category(user_id, category_id)
        if category is not None:
            db.add_task(user_id, category_id, title)
        await delete_user_message(update)
        if category is not None:
            text, markup = build_task_list(user_id, category)
            await show_aux(update, context, text, reply_markup=markup)
        return

    if AWAITING_FUEL_STATION in context.user_data:
        station = update.message.text.strip()
        if not station:
            await update.message.reply_text("Название заправки не может быть пустым.")
            return
        context.user_data.pop(AWAITING_FUEL_STATION)
        context.user_data[FUEL_DRAFT]["station"] = station
        context.user_data[AWAITING_FUEL_LITERS] = True
        await delete_user_message(update)
        await show_aux(update, context, f"⛽️ {station}\nСколько литров залил?",
                       reply_markup=cancel_keyboard(), parse_mode=None)
        return

    if AWAITING_FUEL_LITERS in context.user_data:
        try:
            liters = float(update.message.text.strip().replace(",", "."))
        except ValueError:
            await update.message.reply_text("Введи число литров, например: 35.5")
            return
        if not 0 < liters <= 200:
            await update.message.reply_text("Литры должны быть в диапазоне 0-200.")
            return
        context.user_data.pop(AWAITING_FUEL_LITERS)
        context.user_data[FUEL_DRAFT]["liters"] = liters
        context.user_data[AWAITING_FUEL_AMOUNT] = True
        await delete_user_message(update)
        await show_aux(update, context, "Сколько это стоило (₽)?",
                       reply_markup=cancel_keyboard(), parse_mode=None)
        return

    if AWAITING_FUEL_AMOUNT in context.user_data:
        try:
            amount = float(update.message.text.strip().replace(",", "."))
        except ValueError:
            await update.message.reply_text("Введи сумму числом, например: 2500")
            return
        if amount <= 0:
            await update.message.reply_text("Сумма должна быть больше нуля.")
            return
        context.user_data.pop(AWAITING_FUEL_AMOUNT)
        context.user_data[FUEL_DRAFT]["amount"] = amount
        await delete_user_message(update)
        await show_aux(update, context, "Как оплатил?", reply_markup=payment_method_keyboard(), parse_mode=None)
        return

    if AWAITING_EXPENSE in context.user_data:
        category_id = context.user_data.pop(AWAITING_EXPENSE)
        parts = update.message.text.strip().split(maxsplit=1)
        try:
            amount = float(parts[0].replace(",", "."))
        except (ValueError, IndexError):
            await update.message.reply_text("Не понял сумму. Формат: 1500 продукты")
            return
        if amount <= 0:
            await update.message.reply_text("Сумма должна быть больше нуля.")
            return
        category = db.get_category(user_id, category_id)
        if category is not None:
            note = parts[1] if len(parts) > 1 else ""
            db.add_expense(user_id, category_id, amount, note)
        await delete_user_message(update)
        await clear_aux(update, context)
        await show_panel(update, context)
        return

    await delete_user_message(update)
    await show_panel(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_flags(context)
    await clear_aux(update, context)
    await delete_user_message(update)
    await show_panel(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception while processing update: %s", update, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("Произошла ошибка. Попробуй ещё раз.")
        except BadRequest:
            pass


BOT_COMMANDS = [
    BotCommand("panel", "Панель: отметить пункты за сегодня"),
    BotCommand("calendar", "Календарь по дням"),
    BotCommand("expense", "Записать расход"),
    BotCommand("weight", "Записать вес"),
    BotCommand("stats", "Статистика за месяц"),
    BotCommand("tasks", "Задачи (список дел)"),
    BotCommand("tips", "Советы по питанию"),
    BotCommand("clear", "Очистить сообщения и обновить панель"),
    BotCommand("cancel", "Отменить текущее действие"),
    BotCommand("help", "Справка"),
]


async def post_init(app: Application):
    await app.bot.set_my_commands(BOT_COMMANDS)


def build_app() -> Application:
    load_dotenv()
    token = os.environ["BOT_TOKEN"]
    db.init_db(os.environ.get("DB_PATH", "life_tracker.db"))

    app = Application.builder().token(token).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CommandHandler("habits", panel))
    app.add_handler(CommandHandler("calendar", calendar_cmd))
    app.add_handler(CommandHandler("expense", expense_start))
    app.add_handler(CommandHandler("weight", weight_cmd))
    app.add_handler(CommandHandler("stats", habitstats_cmd))
    app.add_handler(CommandHandler("habitstats", habitstats_cmd))
    app.add_handler(CommandHandler("expenses", expenses_report))
    app.add_handler(CommandHandler("weightstats", weight_stats))
    app.add_handler(CommandHandler("tasks", tasks_menu))
    app.add_handler(CommandHandler("add", tasks_menu))
    app.add_handler(CommandHandler("tips", tips_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("help", help_cmd))

    app.add_handler(CallbackQueryHandler(panel_toggle, pattern=r"^ptgl:"))
    app.add_handler(CallbackQueryHandler(log_weight_start, pattern=r"^logweight$"))
    app.add_handler(CallbackQueryHandler(expense_start, pattern=r"^quickexp$"))
    app.add_handler(CallbackQueryHandler(expense_category_chosen, pattern=r"^expcat:"))
    app.add_handler(CallbackQueryHandler(fuel_station_chosen, pattern=r"^fuelstation:"))
    app.add_handler(CallbackQueryHandler(fuel_payment_chosen, pattern=r"^fuelpay:"))
    app.add_handler(CallbackQueryHandler(tasks_menu, pattern=r"^taskmenu$"))
    app.add_handler(CallbackQueryHandler(tasks_view, pattern=r"^viewcat:"))
    app.add_handler(CallbackQueryHandler(task_add_prompt, pattern=r"^taskadd:"))
    app.add_handler(CallbackQueryHandler(task_done, pattern=r"^done:"))
    app.add_handler(CallbackQueryHandler(task_del, pattern=r"^del:"))
    app.add_handler(CallbackQueryHandler(calendar_month_nav, pattern=r"^calmonth:"))
    app.add_handler(CallbackQueryHandler(calendar_day_view, pattern=r"^calday:"))
    app.add_handler(CallbackQueryHandler(habit_toggle, pattern=r"^habittgl:"))
    app.add_handler(CallbackQueryHandler(cal_fuel_start, pattern=r"^calfuelstart:"))
    app.add_handler(CallbackQueryHandler(quick_nav, pattern=r"^quicknav:"))
    app.add_handler(CallbackQueryHandler(aux_close, pattern=r"^auxclose$"))
    app.add_handler(CallbackQueryHandler(noop, pattern=r"^noop$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text))
    app.add_error_handler(error_handler)

    return app


def main():
    app = build_app()
    app.run_polling()


if __name__ == "__main__":
    main()
