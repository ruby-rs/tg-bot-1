import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import db
from .formatting import escape_md, progress_bar, sparkline, table
from .tips import NUTRITION_TIPS, WELCOME_TEXT

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

AWAITING_TASK = "awaiting_task_category"
AWAITING_EXPENSE = "awaiting_expense_category"


def category_keyboard(user_id: int, prefix: str) -> InlineKeyboardMarkup:
    cats = db.get_categories(user_id)
    buttons = [
        [InlineKeyboardButton(f"{c['emoji']} {c['title']}", callback_data=f"{prefix}:{c['id']}")]
        for c in cats
    ]
    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.get_or_create_user(user.id, user.full_name)
    await update.message.reply_text(WELCOME_TEXT, parse_mode=ParseMode.MARKDOWN_V2)


async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    stats = db.category_stats(user_id)

    today = escape_md(datetime.now().strftime("%d.%m.%Y"))
    lines = [f"📊 *Панель прогресса* · {escape_md(user.first_name)}", f"_{today}_", ""]

    rows = []
    total_done = total_all = 0
    for row in stats:
        done = row["done"] or 0
        total = row["total"] or 0
        total_done += done
        total_all += total
        label = f"{row['emoji']} {row['title']}"
        rows.append((label, progress_bar(done, total, length=8), f"{done}/{total}"))

    lines.append(f">Всего задач выполнено: *{total_done}/{total_all}*")
    lines.append("```")
    lines.append(table(rows))
    lines.append("```")

    weights = db.get_weights(user_id, limit=7)
    if weights:
        ordered = [w["weight"] for w in reversed(weights)]
        latest = ordered[-1]
        line = f"⚖️ Вес: *{escape_md(latest)} кг* `{sparkline(ordered)}`"
        if len(ordered) >= 2:
            diff = round(ordered[-1] - ordered[-2], 1)
            sign = "+" if diff >= 0 else ""
            line += f"  \\({escape_md(sign + str(diff))}\\)"
        lines.append("")
        lines.append(line)

    expenses = db.expense_totals_this_month(user_id)
    if expenses:
        exp_rows = [(f"{e['emoji']} {e['title']}", f"{e['total']:.0f}") for e in expenses]
        exp_total = sum(e["total"] for e in expenses)
        exp_rows.append(("Итого", f"{exp_total:.0f}"))
        lines.append("")
        lines.append("💸 *Расходы в этом месяце*")
        lines.append("```")
        lines.append(table(exp_rows))
        lines.append("```")

    buttons = [
        [InlineKeyboardButton(f"{c['emoji']} {c['title']}", callback_data=f"viewcat:{c['id']}")]
        for c in db.get_categories(user_id)
    ]
    buttons.append(
        [
            InlineKeyboardButton("➕ Задача", callback_data="quickadd"),
            InlineKeyboardButton("💸 Расход", callback_data="quickexp"),
        ]
    )

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def quick_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    await query.message.reply_text(
        "Выбери категорию для новой задачи:", reply_markup=category_keyboard(user_id, "addcat")
    )


async def quick_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    await query.message.reply_text(
        "Выбери категорию расхода:", reply_markup=category_keyboard(user_id, "expcat")
    )


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    await update.message.reply_text(
        "Выбери категорию для новой задачи:", reply_markup=category_keyboard(user_id, "addcat")
    )


async def add_category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category_id = int(query.data.split(":")[1])
    context.user_data[AWAITING_TASK] = category_id
    await query.edit_message_text("Напиши текст задачи следующим сообщением.")


async def tasks_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    await update.message.reply_text(
        "Выбери категорию для просмотра задач:", reply_markup=category_keyboard(user_id, "viewcat")
    )


async def tasks_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    category_id = int(query.data.split(":")[1])
    category = db.get_category(user_id, category_id)
    if category is None:
        await query.edit_message_text("Категория не найдена.")
        return
    tasks = db.get_tasks(user_id, category_id=category_id)

    if not tasks:
        await query.edit_message_text(f"{category['emoji']} {category['title']}: задач пока нет.")
        return

    done_count = sum(1 for t in tasks if t["status"] == "done")
    text_lines = [
        f"{category['emoji']} *{escape_md(category['title'])}*",
        f"`{escape_md(progress_bar(done_count, len(tasks), length=12))}`  \\({done_count}/{len(tasks)}\\)",
        "",
    ]
    buttons = []
    for t in tasks:
        mark = "✅" if t["status"] == "done" else "⬜️"
        text_lines.append(f"{mark} {escape_md(t['title'])}")
        if t["status"] != "done":
            buttons.append(
                [
                    InlineKeyboardButton(f"✅ {t['title'][:20]}", callback_data=f"done:{t['id']}"),
                    InlineKeyboardButton("🗑", callback_data=f"del:{t['id']}"),
                ]
            )
    buttons.append([InlineKeyboardButton("◀️ К панели", callback_data="backpanel")])
    await query.edit_message_text(
        "\n".join(text_lines),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def back_to_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.delete()
    await panel(update, context)


async def task_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    task_id = int(query.data.split(":")[1])
    db.complete_task(user_id, task_id)
    await query.answer("Готово! 🎉")
    await query.edit_message_reply_markup(reply_markup=None)


async def task_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    task_id = int(query.data.split(":")[1])
    db.delete_task(user_id, task_id)
    await query.answer("Удалено")
    await query.edit_message_reply_markup(reply_markup=None)


async def weight_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    if not context.args:
        await update.message.reply_text("Использование: /weight 61.5")
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
    await update.message.reply_text(f"⚖️ Записал вес: {value} кг")


async def weight_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    weights = db.get_weights(user_id, limit=10)
    if not weights:
        await update.message.reply_text("Пока нет записей веса. Используй /weight 61.5")
        return
    ordered = list(reversed(weights))
    values = [w["weight"] for w in ordered]
    lines = ["⚖️ *Динамика веса*", ""]
    lines.append(f"`{sparkline(values)}`")
    lines.append("```")
    lines.append(table([(w["logged_at"][:10], f"{w['weight']} кг") for w in ordered]))
    lines.append("```")
    change = round(values[-1] - values[0], 1)
    sign = "+" if change >= 0 else ""
    lines.append(f"Изменение за период: *{escape_md(sign + str(change))} кг*")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)


async def expense_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    await update.message.reply_text(
        "Выбери категорию расхода:", reply_markup=category_keyboard(user_id, "expcat")
    )


async def expense_category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category_id = int(query.data.split(":")[1])
    context.user_data[AWAITING_EXPENSE] = category_id
    await query.edit_message_text("Напиши сумму и комментарий, например: 1500 бензин")


async def expenses_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)
    totals = db.expense_totals_this_month(user_id)
    if not totals:
        await update.message.reply_text("В этом месяце расходов ещё не записано. Используй /expense")
        return
    rows = [(f"{e['emoji']} {e['title']}", f"{e['total']:.0f}") for e in totals]
    rows.append(("Итого", f"{sum(e['total'] for e in totals):.0f}"))
    lines = ["💸 *Расходы за месяц*", "```", table(rows), "```"]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)


async def tips_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(NUTRITION_TIPS, parse_mode=ParseMode.MARKDOWN_V2)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT, parse_mode=ParseMode.MARKDOWN_V2)


async def free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = db.get_or_create_user(user.id, user.full_name)

    if AWAITING_TASK in context.user_data:
        category_id = context.user_data.pop(AWAITING_TASK)
        title = update.message.text.strip()
        if not title:
            await update.message.reply_text("Текст задачи не может быть пустым. Используй /add ещё раз.")
            return
        category = db.get_category(user_id, category_id)
        if category is None:
            await update.message.reply_text("Категория не найдена. Используй /add ещё раз.")
            return
        db.add_task(user_id, category_id, title)
        await update.message.reply_text(f"✅ Добавлено в {category['emoji']} {category['title']}: {title}")
        return

    if AWAITING_EXPENSE in context.user_data:
        category_id = context.user_data.pop(AWAITING_EXPENSE)
        parts = update.message.text.strip().split(maxsplit=1)
        try:
            amount = float(parts[0].replace(",", "."))
        except (ValueError, IndexError):
            await update.message.reply_text("Не понял сумму. Формат: 1500 бензин")
            return
        if amount <= 0:
            await update.message.reply_text("Сумма должна быть больше нуля.")
            return
        category = db.get_category(user_id, category_id)
        if category is None:
            await update.message.reply_text("Категория не найдена. Используй /expense ещё раз.")
            return
        note = parts[1] if len(parts) > 1 else ""
        db.add_expense(user_id, category_id, amount, note)
        await update.message.reply_text(f"💸 Записал {amount} в {category['emoji']} {category['title']}")
        return

    await update.message.reply_text("Не понял. Список команд: /start")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop(AWAITING_TASK, None)
    context.user_data.pop(AWAITING_EXPENSE, None)
    await update.message.reply_text("Отменено.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception while processing update: %s", update, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("Произошла ошибка. Попробуй ещё раз.")


def build_app() -> Application:
    load_dotenv()
    token = os.environ["BOT_TOKEN"]
    db.init_db(os.environ.get("DB_PATH", "life_tracker.db"))

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CommandHandler("add", add_start))
    app.add_handler(CommandHandler("tasks", tasks_start))
    app.add_handler(CommandHandler("weight", weight_cmd))
    app.add_handler(CommandHandler("weightstats", weight_stats))
    app.add_handler(CommandHandler("expense", expense_start))
    app.add_handler(CommandHandler("expenses", expenses_report))
    app.add_handler(CommandHandler("tips", tips_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("help", help_cmd))

    app.add_handler(CallbackQueryHandler(add_category_chosen, pattern=r"^addcat:"))
    app.add_handler(CallbackQueryHandler(tasks_list, pattern=r"^viewcat:"))
    app.add_handler(CallbackQueryHandler(task_done, pattern=r"^done:"))
    app.add_handler(CallbackQueryHandler(task_del, pattern=r"^del:"))
    app.add_handler(CallbackQueryHandler(expense_category_chosen, pattern=r"^expcat:"))
    app.add_handler(CallbackQueryHandler(quick_add, pattern=r"^quickadd$"))
    app.add_handler(CallbackQueryHandler(quick_expense, pattern=r"^quickexp$"))
    app.add_handler(CallbackQueryHandler(back_to_panel, pattern=r"^backpanel$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text))
    app.add_error_handler(error_handler)

    return app


def main():
    app = build_app()
    app.run_polling()


if __name__ == "__main__":
    main()
