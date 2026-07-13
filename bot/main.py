"""Minimal Telegram bot — a secondary channel for the life tracker.

Full functionality (all checker types, calendar, expenses, stats, custom
checkers, events) lives in the web interface (see ``webapp/``). The bot only
covers the simplest, fastest actions: toggling today's yes/no checkers and
logging weight, so you can do that without opening a browser.
"""
import logging
import os

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
from .formatting import escape_md, sparkline
from .tips import WELCOME_TEXT

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

MD = ParseMode.MARKDOWN_V2

# Two persistent message "slots" per chat:
#  - PANEL_SLOT: the always-visible dashboard; only ever edited in place.
#  - AUX_SLOT: a transient message used for the weight-entry flow; deleted
#    once the flow finishes.
PANEL_SLOT = "panel"
AUX_SLOT = "aux"

AWAITING_WEIGHT = "awaiting_weight"

HABIT_STATUS_EMOJI = {"done": "✅", "skip": "❌", None: "➖"}


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


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✖️ Отмена", callback_data="auxclose")]])


async def delete_user_message(update):
    """Removes the user's own input message to keep the chat tidy (private chats allow this)."""
    try:
        await update.message.delete()
    except BadRequest:
        pass


def clear_flags(context):
    context.user_data.pop(AWAITING_WEIGHT, None)


# ---------------------------------------------------------------------------
# Panel (persistent dashboard of today's yes/no checkers)
# ---------------------------------------------------------------------------

def build_panel_view(user_id: int, first_name: str):
    today = db.today()
    # The bot only supports the simple done/skip/none cycle; other checker
    # types (number, note, interval, expense, fuel) are edited on the site.
    rows = [r for r in db.get_habit_logs_for_date(user_id, today) if r["type"] == "bool"]
    done = sum(1 for r in rows if r["status"] == "done")
    total = len(rows)

    lines = [f"📊 *{escape_md(first_name)}*", ""]
    if total:
        lines.append(f"Отмечено сегодня: *{done}/{total}*")
    else:
        lines.append("_Нет да/нет\\-чекеров \\— остальные пункты доступны на сайте\\._")

    weights = db.get_weights(user_id, limit=7)
    if weights:
        ordered = [w["weight"] for w in reversed(weights)]
        wline = f"⚖️ *{escape_md(ordered[-1])} кг* `{sparkline(ordered)}`"
        if len(ordered) >= 2:
            diff = round(ordered[-1] - ordered[-2], 1)
            sign = "+" if diff >= 0 else ""
            wline += f" \\({escape_md(sign + str(diff))}\\)"
        lines.append(wline)

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
    buttons.append([InlineKeyboardButton("⚖️ Записать вес", callback_data="logweight")])
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
    restrict_day = db.habit_config(habit).get("restrict_day")
    today = db.today()
    if restrict_day and int(today[8:10]) != restrict_day:
        await query.answer(f"Доступно только {restrict_day} числа")
        return
    new_status = db.toggle_habit_log(habit_id, today)
    await query.answer(HABIT_STATUS_EMOJI[None if new_status == "none" else new_status])
    text, markup = build_panel_view(user_id, user.first_name)
    await _edit_query_message(query, text, markup)


async def _edit_query_message(query, text, reply_markup=None):
    try:
        await query.edit_message_text(text, parse_mode=MD, reply_markup=reply_markup)
    except BadRequest:
        pass


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
# /clear, /cancel, error handling, free text
# ---------------------------------------------------------------------------

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


async def aux_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    clear_flags(context)
    await clear_aux(update, context)
    await show_panel(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_flags(context)
    await clear_aux(update, context)
    await delete_user_message(update)
    await show_panel(update, context)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT, parse_mode=MD)


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
    BotCommand("panel", "Панель: да/нет-чекеры за сегодня"),
    BotCommand("weight", "Записать вес"),
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
    app.add_handler(CommandHandler("weight", weight_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("help", help_cmd))

    app.add_handler(CallbackQueryHandler(panel_toggle, pattern=r"^ptgl:"))
    app.add_handler(CallbackQueryHandler(log_weight_start, pattern=r"^logweight$"))
    app.add_handler(CallbackQueryHandler(aux_close, pattern=r"^auxclose$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text))
    app.add_error_handler(error_handler)

    return app


def main():
    app = build_app()
    app.run_polling()


if __name__ == "__main__":
    main()
