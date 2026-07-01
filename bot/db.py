import sqlite3
from datetime import datetime, timezone
from contextlib import contextmanager

DB_PATH = "life_tracker.db"

DEFAULT_CATEGORIES = [
    ("work", "Работа", "💼"),
    ("gym", "Спорт", "💪"),
    ("nutrition", "Питание", "🍗"),
    ("flat", "Стройка: Квартира", "🏢"),
    ("house", "Стройка: Дом", "🏠"),
    ("car", "Машина", "🚗"),
    ("mortgage", "Ипотека", "🏦"),
    ("relationship", "Отношения", "❤️"),
    ("vacation", "Отпуск", "🏖"),
]

# (slug, title, category)
DEFAULT_HABITS = [
    ("water", "Вода 2+ литра", "Здоровье"),
    ("flat_work", "Работы на квартире", "Стройка"),
    ("house_work", "Работы на доме", "Стройка"),
    ("work_shift", "Рабочая смена", "Работа"),
    ("productive_day", "Продуктивный день", "Работа"),
    ("mortgage_paid", "Ипотека внесена", "Финансы"),
    ("budget_ok", "В рамках бюджета", "Финансы"),
    ("gf_meeting", "Встречи с девушкой", "Отношения и жизнь"),
    ("events", "Мероприятия", "Отношения и жизнь"),
    ("car_service", "Обслуживание", "Авто"),
]


def init_db(path: str = None):
    global DB_PATH
    if path:
        DB_PATH = path
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER UNIQUE NOT NULL,
                name TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                slug TEXT NOT NULL,
                title TEXT NOT NULL,
                emoji TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                UNIQUE(user_id, slug),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                done_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(category_id) REFERENCES categories(id)
            );

            CREATE TABLE IF NOT EXISTS weights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                weight REAL NOT NULL,
                logged_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                note TEXT,
                logged_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(category_id) REFERENCES categories(id)
            );

            CREATE TABLE IF NOT EXISTS habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                slug TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                UNIQUE(user_id, slug),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS habit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id INTEGER NOT NULL,
                log_date TEXT NOT NULL,
                status TEXT NOT NULL,
                UNIQUE(habit_id, log_date),
                FOREIGN KEY(habit_id) REFERENCES habits(id)
            );
            """
        )


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def get_or_create_user(tg_id: int, name: str) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO users (tg_id, name, created_at) VALUES (?, ?, ?)",
            (tg_id, name, now()),
        )
        user_id = cur.lastrowid
        for order, (slug, title, emoji) in enumerate(DEFAULT_CATEGORIES):
            conn.execute(
                "INSERT INTO categories (user_id, slug, title, emoji, sort_order) VALUES (?, ?, ?, ?, ?)",
                (user_id, slug, title, emoji, order),
            )
        for order, (slug, title, category) in enumerate(DEFAULT_HABITS):
            conn.execute(
                "INSERT INTO habits (user_id, slug, title, category, sort_order) VALUES (?, ?, ?, ?, ?)",
                (user_id, slug, title, category, order),
            )
        return user_id


def get_categories(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM categories WHERE user_id = ? ORDER BY sort_order", (user_id,)
        ).fetchall()


def get_category(user_id: int, category_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM categories WHERE user_id = ? AND id = ?", (user_id, category_id)
        ).fetchone()


def add_task(user_id: int, category_id: int, title: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (user_id, category_id, title, status, created_at) VALUES (?, ?, ?, 'active', ?)",
            (user_id, category_id, title, now()),
        )
        return cur.lastrowid


def get_tasks(user_id: int, category_id: int = None, status: str = None):
    query = "SELECT * FROM tasks WHERE user_id = ?"
    params = [user_id]
    if category_id is not None:
        query += " AND category_id = ?"
        params.append(category_id)
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"
    with get_conn() as conn:
        return conn.execute(query, params).fetchall()


def get_task(user_id: int, task_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM tasks WHERE user_id = ? AND id = ?", (user_id, task_id)
        ).fetchone()


def complete_task(user_id: int, task_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET status = 'done', done_at = ? WHERE user_id = ? AND id = ?",
            (now(), user_id, task_id),
        )


def delete_task(user_id: int, task_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM tasks WHERE user_id = ? AND id = ?", (user_id, task_id))


def category_stats(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT c.id, c.slug, c.title, c.emoji,
                   COUNT(t.id) AS total,
                   SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END) AS done
            FROM categories c
            LEFT JOIN tasks t ON t.category_id = c.id
            WHERE c.user_id = ?
            GROUP BY c.id
            ORDER BY c.sort_order
            """,
            (user_id,),
        ).fetchall()


def log_weight(user_id: int, weight: float):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO weights (user_id, weight, logged_at) VALUES (?, ?, ?)",
            (user_id, weight, now()),
        )


def get_weights(user_id: int, limit: int = 10):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM weights WHERE user_id = ? ORDER BY logged_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()


def add_expense(user_id: int, category_id: int, amount: float, note: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO expenses (user_id, category_id, amount, note, logged_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, category_id, amount, note, now()),
        )


def get_habits(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM habits WHERE user_id = ? ORDER BY sort_order", (user_id,)
        ).fetchall()


def get_habit(user_id: int, habit_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM habits WHERE user_id = ? AND id = ?", (user_id, habit_id)
        ).fetchone()


def get_habit_logs_for_date(user_id: int, log_date: str):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT h.id, h.title, h.category, h.sort_order, hl.status
            FROM habits h
            LEFT JOIN habit_logs hl ON hl.habit_id = h.id AND hl.log_date = ?
            WHERE h.user_id = ?
            ORDER BY h.sort_order
            """,
            (log_date, user_id),
        ).fetchall()


def toggle_habit_log(habit_id: int, log_date: str) -> str:
    """Cycles a habit's status for a given day: none -> done -> skip -> none."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM habit_logs WHERE habit_id = ? AND log_date = ?",
            (habit_id, log_date),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO habit_logs (habit_id, log_date, status) VALUES (?, ?, 'done')",
                (habit_id, log_date),
            )
            return "done"
        if row["status"] == "done":
            conn.execute(
                "UPDATE habit_logs SET status = 'skip' WHERE habit_id = ? AND log_date = ?",
                (habit_id, log_date),
            )
            return "skip"
        conn.execute(
            "DELETE FROM habit_logs WHERE habit_id = ? AND log_date = ?",
            (habit_id, log_date),
        )
        return "none"


def get_habit_month_stats(user_id: int, year_month: str):
    """year_month in 'YYYY-MM' format. Returns each habit with its done-count this month."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT h.id, h.title, h.category, h.sort_order,
                   SUM(CASE WHEN hl.status = 'done' THEN 1 ELSE 0 END) AS done_count
            FROM habits h
            LEFT JOIN habit_logs hl
                ON hl.habit_id = h.id AND strftime('%Y-%m', hl.log_date) = ?
            WHERE h.user_id = ?
            GROUP BY h.id
            ORDER BY h.sort_order
            """,
            (year_month, user_id),
        ).fetchall()


def expense_totals_this_month(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT c.title, c.emoji, SUM(e.amount) AS total
            FROM expenses e
            JOIN categories c ON c.id = e.category_id
            WHERE e.user_id = ? AND strftime('%Y-%m', e.logged_at) = strftime('%Y-%m', 'now')
            GROUP BY c.id
            ORDER BY total DESC
            """,
            (user_id,),
        ).fetchall()
