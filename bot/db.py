import json
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
    ("purchases", "Покупки", "🛒"),
    ("gifts", "Подарки", "🎁"),
]

WORKOUT_TYPES = [
    "Разминка", "Силовая", "Уличная (турники)", "Бег", "Велосипед",
    "Плавание", "Йога", "Кроссфит", "Бокс/единоборства", "Футбол", "Растяжка",
]
SIDE_JOB_TYPES = ["Водитель", "Стройка", "Сетевой инженер"]
DAY_PERIODS = ["Утро", "День", "Вечер", "Ночь"]

# (slug, title, category, type, config-dict)
DEFAULT_HABITS = [
    ("water", "Вода", "Здоровье", "number", {"unit": "л", "target": 2}),
    ("workout", "Тренировка", "Здоровье", "interval",
     {"has_period": True, "subtype_label": "Вид тренировки", "subtype_options": WORKOUT_TYPES}),
    ("flat_work", "Работы на квартире", "Стройка", "interval", {"aggregate": "hours"}),
    ("house_work", "Работы на доме", "Стройка", "interval", {"aggregate": "hours"}),
    ("work_shift", "Смена", "Работа", "interval",
     {"aggregate": "hours", "presets": ["09:00-18:00"]}),
    ("side_job", "Подработка", "Работа", "interval",
     {"has_amount": True, "amount_label": "Заработок, ₽",
      "subtype_label": "Вид подработки", "subtype_options": SIDE_JOB_TYPES}),
    ("mortgage_paid", "Ипотека внесена", "Финансы", "bool", {"restrict_day": 16}),
    ("purchases", "Покупки", "Финансы", "expense", {"category_slug": "purchases"}),
    ("gift", "Подарок", "Финансы", "expense", {"category_slug": "gifts"}),
    ("gf_meeting", "Встреча с девушкой", "Отношения и жизнь", "interval", {}),
    ("events", "Мероприятие", "Отношения и жизнь", "interval",
     {"free_subtype": True, "subtype_label": "Название"}),
    ("fuel", "Заправка", "Авто", "fuel", {"category_slug": "car"}),
    ("car_service", "Обслуживание", "Авто", "expense",
     {"category_slug": "car", "label": "Работы"}),
]

FUEL_STATIONS = ["Лукойл", "Роснефть", "Нефтьмагистраль", "Тбойл"]
PAYMENT_METHODS = ["Карта", "Деньги"]
HABIT_TYPES = ("bool", "number", "note", "interval", "expense", "fuel")


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
                liters REAL,
                station TEXT,
                payment_method TEXT,
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
                type TEXT NOT NULL DEFAULT 'bool',
                unit TEXT,
                target REAL,
                config TEXT,
                UNIQUE(user_id, slug),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS habit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id INTEGER NOT NULL,
                log_date TEXT NOT NULL,
                status TEXT NOT NULL,
                value REAL,
                UNIQUE(habit_id, log_date),
                FOREIGN KEY(habit_id) REFERENCES habits(id)
            );

            CREATE TABLE IF NOT EXISTS habit_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id INTEGER NOT NULL,
                log_date TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(habit_id) REFERENCES habits(id)
            );

            CREATE TABLE IF NOT EXISTS habit_intervals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id INTEGER NOT NULL,
                log_date TEXT NOT NULL,
                start_time TEXT,
                end_time TEXT,
                period TEXT,
                subtype TEXT,
                amount REAL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(habit_id) REFERENCES habits(id)
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                event_date TEXT NOT NULL,
                recurring INTEGER NOT NULL DEFAULT 0,
                emoji TEXT NOT NULL DEFAULT '🎉',
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                chat_id INTEGER NOT NULL,
                slot TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                PRIMARY KEY (chat_id, slot)
            );
            """
        )
        _migrate(conn)


def _migrate(conn):
    """Adds columns that may be missing on databases created before they existed."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(expenses)").fetchall()}
    for column in ("liters", "station", "payment_method"):
        if column not in existing:
            column_type = "REAL" if column == "liters" else "TEXT"
            conn.execute(f"ALTER TABLE expenses ADD COLUMN {column} {column_type}")

    habit_columns = {row["name"] for row in conn.execute("PRAGMA table_info(habits)").fetchall()}
    if "type" not in habit_columns:
        conn.execute("ALTER TABLE habits ADD COLUMN type TEXT NOT NULL DEFAULT 'bool'")
    if "unit" not in habit_columns:
        conn.execute("ALTER TABLE habits ADD COLUMN unit TEXT")
    if "target" not in habit_columns:
        conn.execute("ALTER TABLE habits ADD COLUMN target REAL")
    if "config" not in habit_columns:
        conn.execute("ALTER TABLE habits ADD COLUMN config TEXT")

    log_columns = {row["name"] for row in conn.execute("PRAGMA table_info(habit_logs)").fetchall()}
    if "value" not in log_columns:
        conn.execute("ALTER TABLE habit_logs ADD COLUMN value REAL")

    _migrate_habit_definitions(conn)


# Slugs replaced by newer built-in checkers (see DEFAULT_HABITS below).
_REMOVED_HABIT_SLUGS = ("productive_day", "budget_ok")


def _migrate_habit_definitions(conn):
    """Brings already-registered users' built-in checkers up to the current
    DEFAULT_HABITS/DEFAULT_CATEGORIES spec (new types, configs, categories).
    Idempotent: safe to run on every startup. Only touches built-in slugs —
    a user's own custom checkers (added via the "Чекеры" page) are untouched.
    """
    users = conn.execute("SELECT id FROM users").fetchall()
    if not users:
        return

    existing_cat_slugs_by_user = {}
    for row in conn.execute("SELECT user_id, slug FROM categories").fetchall():
        existing_cat_slugs_by_user.setdefault(row["user_id"], set()).add(row["slug"])

    for user in users:
        user_id = user["id"]
        cat_slugs = existing_cat_slugs_by_user.get(user_id, set())
        max_cat_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM categories WHERE user_id = ?", (user_id,)
        ).fetchone()["m"]
        for slug, title, emoji in DEFAULT_CATEGORIES:
            if slug not in cat_slugs:
                max_cat_order += 1
                conn.execute(
                    "INSERT INTO categories (user_id, slug, title, emoji, sort_order) VALUES (?, ?, ?, ?, ?)",
                    (user_id, slug, title, emoji, max_cat_order),
                )

        for removed_slug in _REMOVED_HABIT_SLUGS:
            removed = conn.execute(
                "SELECT id FROM habits WHERE user_id = ? AND slug = ?", (user_id, removed_slug)
            ).fetchone()
            if removed is None:
                continue
            conn.execute("DELETE FROM habit_entries WHERE habit_id = ?", (removed["id"],))
            conn.execute("DELETE FROM habit_intervals WHERE habit_id = ?", (removed["id"],))
            conn.execute("DELETE FROM habit_logs WHERE habit_id = ?", (removed["id"],))
            conn.execute("DELETE FROM habits WHERE id = ?", (removed["id"],))

        habit_slugs = {
            row["slug"] for row in
            conn.execute("SELECT slug FROM habits WHERE user_id = ?", (user_id,)).fetchall()
        }
        max_habit_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM habits WHERE user_id = ?", (user_id,)
        ).fetchone()["m"]
        for slug, title, category, habit_type, config in DEFAULT_HABITS:
            unit = config.get("unit")
            target = config.get("target")
            config_json = json.dumps(config) if config else None
            if slug in habit_slugs:
                conn.execute(
                    "UPDATE habits SET type = ?, unit = ?, target = ?, config = ? WHERE user_id = ? AND slug = ?",
                    (habit_type, unit, target, config_json, user_id, slug),
                )
            else:
                max_habit_order += 1
                conn.execute(
                    """
                    INSERT INTO habits (user_id, slug, title, category, sort_order, type, unit, target, config)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, slug, title, category, max_habit_order, habit_type, unit, target, config_json),
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
        for order, (slug, title, category, habit_type, config) in enumerate(DEFAULT_HABITS):
            conn.execute(
                """
                INSERT INTO habits (user_id, slug, title, category, sort_order, type, unit, target, config)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, slug, title, category, order, habit_type,
                    config.get("unit"), config.get("target"),
                    json.dumps(config) if config else None,
                ),
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


def add_expense(
    user_id: int,
    category_id: int,
    amount: float,
    note: str = "",
    liters: float = None,
    station: str = None,
    payment_method: str = None,
    logged_at: str = None,
):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO expenses (user_id, category_id, amount, note, liters, station, payment_method, logged_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, category_id, amount, note, liters, station, payment_method, logged_at or now()),
        )


def get_expenses_for_month(user_id: int, year_month: str):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT e.*, c.title AS category_title, c.emoji AS category_emoji, c.slug AS category_slug
            FROM expenses e
            JOIN categories c ON c.id = e.category_id
            WHERE e.user_id = ? AND strftime('%Y-%m', e.logged_at) = ?
            ORDER BY e.logged_at DESC
            """,
            (user_id, year_month),
        ).fetchall()


def get_known_fuel_stations(user_id: int):
    """Preset stations plus any custom names the user has already typed in,
    most-recently-used first, so a one-off custom name gets suggested again."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT station, MAX(logged_at) AS last_used
            FROM expenses
            WHERE user_id = ? AND station IS NOT NULL AND station != ''
            GROUP BY station
            ORDER BY last_used DESC
            """,
            (user_id,),
        ).fetchall()
    used = [row["station"] for row in rows]
    return used + [s for s in FUEL_STATIONS if s not in used]


def delete_expense(user_id: int, expense_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM expenses WHERE user_id = ? AND id = ?", (user_id, expense_id))


def delete_weight(user_id: int, weight_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM weights WHERE user_id = ? AND id = ?", (user_id, weight_id))


def get_expenses_for_date(user_id: int, log_date: str):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT e.*, c.title AS category_title, c.emoji AS category_emoji, c.slug AS category_slug
            FROM expenses e
            JOIN categories c ON c.id = e.category_id
            WHERE e.user_id = ? AND date(e.logged_at) = ?
            ORDER BY e.logged_at
            """,
            (user_id, log_date),
        ).fetchall()


def _slugify(title: str, existing_slugs) -> str:
    base = "".join(c.lower() if c.isalnum() else "-" for c in title).strip("-") or "item"
    slug = base
    i = 2
    while slug in existing_slugs:
        slug = f"{base}-{i}"
        i += 1
    return slug


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


def add_habit(
    user_id: int, title: str, category: str, habit_type: str = "bool",
    unit: str = None, target: float = None, config: dict = None,
) -> int:
    if habit_type not in HABIT_TYPES:
        habit_type = "bool"
    with get_conn() as conn:
        existing = {
            row["slug"] for row in conn.execute(
                "SELECT slug FROM habits WHERE user_id = ?", (user_id,)
            ).fetchall()
        }
        slug = _slugify(title, existing)
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM habits WHERE user_id = ?", (user_id,)
        ).fetchone()["m"]
        cur = conn.execute(
            """
            INSERT INTO habits (user_id, slug, title, category, sort_order, type, unit, target, config)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, slug, title, category, max_order + 1, habit_type, unit, target,
                json.dumps(config) if config else None,
            ),
        )
        return cur.lastrowid


def update_habit(
    user_id: int, habit_id: int, title: str = None, category: str = None,
    unit: str = None, target: float = None, config: dict = None,
):
    fields, params = [], []
    if title is not None:
        fields.append("title = ?")
        params.append(title)
    if category is not None:
        fields.append("category = ?")
        params.append(category)
    if unit is not None:
        fields.append("unit = ?")
        params.append(unit or None)
    if target is not None:
        fields.append("target = ?")
        params.append(target)
    if config is not None:
        fields.append("config = ?")
        params.append(json.dumps(config) if config else None)
    if not fields:
        return
    params.extend([user_id, habit_id])
    with get_conn() as conn:
        conn.execute(f"UPDATE habits SET {', '.join(fields)} WHERE user_id = ? AND id = ?", params)


def delete_habit(user_id: int, habit_id: int):
    with get_conn() as conn:
        habit = conn.execute(
            "SELECT id FROM habits WHERE user_id = ? AND id = ?", (user_id, habit_id)
        ).fetchone()
        if habit is None:
            return
        conn.execute("DELETE FROM habit_entries WHERE habit_id = ?", (habit_id,))
        conn.execute("DELETE FROM habit_intervals WHERE habit_id = ?", (habit_id,))
        conn.execute("DELETE FROM habit_logs WHERE habit_id = ?", (habit_id,))
        conn.execute("DELETE FROM habits WHERE id = ?", (habit_id,))


def get_habit_logs_for_date(user_id: int, log_date: str):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT h.id, h.title, h.category, h.sort_order, h.type, h.unit, h.target, h.config,
                   hl.status, hl.value
            FROM habits h
            LEFT JOIN habit_logs hl ON hl.habit_id = h.id AND hl.log_date = ?
            WHERE h.user_id = ?
            ORDER BY h.sort_order
            """,
            (log_date, user_id),
        ).fetchall()


def set_habit_value(habit_id: int, log_date: str, value: float):
    """Sets the numeric value for a number-type checker on a given day."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM habit_logs WHERE habit_id = ? AND log_date = ?", (habit_id, log_date)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO habit_logs (habit_id, log_date, status, value) VALUES (?, ?, 'done', ?)",
                (habit_id, log_date, value),
            )
        else:
            conn.execute(
                "UPDATE habit_logs SET status = 'done', value = ? WHERE habit_id = ? AND log_date = ?",
                (value, habit_id, log_date),
            )


def clear_habit_value(habit_id: int, log_date: str):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM habit_logs WHERE habit_id = ? AND log_date = ?", (habit_id, log_date)
        )


def add_habit_entry(habit_id: int, log_date: str, text: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO habit_entries (habit_id, log_date, text, created_at) VALUES (?, ?, ?, ?)",
            (habit_id, log_date, text, now()),
        )
        return cur.lastrowid


def get_habit_entries_for_date(habit_id: int, log_date: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM habit_entries WHERE habit_id = ? AND log_date = ? ORDER BY created_at",
            (habit_id, log_date),
        ).fetchall()


def delete_habit_entry(user_id: int, entry_id: int):
    with get_conn() as conn:
        conn.execute(
            """
            DELETE FROM habit_entries WHERE id = ? AND habit_id IN (
                SELECT id FROM habits WHERE user_id = ?
            )
            """,
            (entry_id, user_id),
        )


def get_habit_entry_counts_for_month(user_id: int, year_month: str):
    """Returns {(habit_id, log_date): count} of note entries for the month."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT he.habit_id, he.log_date, COUNT(*) AS cnt
            FROM habit_entries he
            JOIN habits h ON h.id = he.habit_id
            WHERE h.user_id = ? AND strftime('%Y-%m', he.log_date) = ?
            GROUP BY he.habit_id, he.log_date
            """,
            (user_id, year_month),
        ).fetchall()
        return {(row["habit_id"], row["log_date"]): row["cnt"] for row in rows}


def get_habit_logs_for_month(user_id: int, year_month: str):
    """Returns {(habit_id, log_date): row} of bool/number logs for the month."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT hl.habit_id, hl.log_date, hl.status, hl.value
            FROM habit_logs hl
            JOIN habits h ON h.id = hl.habit_id
            WHERE h.user_id = ? AND strftime('%Y-%m', hl.log_date) = ?
            """,
            (user_id, year_month),
        ).fetchall()
        return {(row["habit_id"], row["log_date"]): row for row in rows}


def habit_config(habit) -> dict:
    """Parses a habit row's ``config`` JSON column into a dict (empty dict if unset/invalid)."""
    raw = habit["config"] if habit is not None else None
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def get_category_by_slug(user_id: int, slug: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM categories WHERE user_id = ? AND slug = ?", (user_id, slug)
        ).fetchone()


# ---------------------------------------------------------------------------
# Interval-type checkers (time ranges, optionally with subtype/amount) —
# used for construction work, shifts, side jobs, workouts, meetings, events.
# ---------------------------------------------------------------------------

def add_habit_interval(
    habit_id: int, log_date: str, start_time: str = None, end_time: str = None,
    period: str = None, subtype: str = None, amount: float = None,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO habit_intervals (habit_id, log_date, start_time, end_time, period, subtype, amount, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (habit_id, log_date, start_time, end_time, period, subtype, amount, now()),
        )
        return cur.lastrowid


def get_habit_intervals_for_date(habit_id: int, log_date: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM habit_intervals WHERE habit_id = ? AND log_date = ? ORDER BY created_at",
            (habit_id, log_date),
        ).fetchall()


def delete_habit_interval(user_id: int, interval_id: int):
    with get_conn() as conn:
        conn.execute(
            """
            DELETE FROM habit_intervals WHERE id = ? AND habit_id IN (
                SELECT id FROM habits WHERE user_id = ?
            )
            """,
            (interval_id, user_id),
        )


def get_habit_intervals_for_month(user_id: int, year_month: str):
    """Returns all interval rows for the month, for duration/earnings aggregation in Python."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT hi.*
            FROM habit_intervals hi
            JOIN habits h ON h.id = hi.habit_id
            WHERE h.user_id = ? AND strftime('%Y-%m', hi.log_date) = ?
            ORDER BY hi.log_date
            """,
            (user_id, year_month),
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
    """year_month in 'YYYY-MM' format. Returns each habit with its done-count and
    value sum this month (value_sum is only meaningful for number-type habits)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT h.id, h.title, h.category, h.sort_order, h.type, h.unit, h.target, h.config,
                   SUM(CASE WHEN hl.status = 'done' THEN 1 ELSE 0 END) AS done_count,
                   SUM(hl.value) AS value_sum
            FROM habits h
            LEFT JOIN habit_logs hl
                ON hl.habit_id = h.id AND strftime('%Y-%m', hl.log_date) = ?
            WHERE h.user_id = ?
            GROUP BY h.id
            ORDER BY h.sort_order
            """,
            (year_month, user_id),
        ).fetchall()
        note_counts = conn.execute(
            """
            SELECT h.id, COUNT(*) AS cnt
            FROM habits h
            JOIN habit_entries he ON he.habit_id = h.id AND strftime('%Y-%m', he.log_date) = ?
            WHERE h.user_id = ?
            GROUP BY h.id
            """,
            (year_month, user_id),
        ).fetchall()
        note_map = {row["id"]: row["cnt"] for row in note_counts}
        result = []
        for row in rows:
            d = dict(row)
            d["note_count"] = note_map.get(row["id"], 0)
            result.append(d)
        return result


def get_habit_days_summary(user_id: int, year_month: str):
    """Returns {log_date: done_count} for days in the given month that have any habit activity."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT hl.log_date, SUM(CASE WHEN hl.status = 'done' THEN 1 ELSE 0 END) AS done_count
            FROM habit_logs hl
            JOIN habits h ON h.id = hl.habit_id
            WHERE h.user_id = ? AND strftime('%Y-%m', hl.log_date) = ?
            GROUP BY hl.log_date
            """,
            (user_id, year_month),
        ).fetchall()
        return {row["log_date"]: row["done_count"] for row in rows}


def get_slot_message(chat_id: int, slot: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT message_id FROM chat_messages WHERE chat_id = ? AND slot = ?", (chat_id, slot)
        ).fetchone()
        return row["message_id"] if row else None


def set_slot_message(chat_id: int, slot: str, message_id: int):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO chat_messages (chat_id, slot, message_id) VALUES (?, ?, ?)
            ON CONFLICT(chat_id, slot) DO UPDATE SET message_id = excluded.message_id
            """,
            (chat_id, slot, message_id),
        )


def clear_slot_message(chat_id: int, slot: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM chat_messages WHERE chat_id = ? AND slot = ?", (chat_id, slot))


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


# ---------------------------------------------------------------------------
# Events (anniversaries / special dates)
# ---------------------------------------------------------------------------

def add_event(user_id: int, title: str, event_date: str, recurring: bool = False, emoji: str = "🎉") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO events (user_id, title, event_date, recurring, emoji, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, title, event_date, int(recurring), emoji, now()),
        )
        return cur.lastrowid


def get_events(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM events WHERE user_id = ? ORDER BY event_date", (user_id,)
        ).fetchall()


def delete_event(user_id: int, event_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM events WHERE user_id = ? AND id = ?", (user_id, event_id))
