import pytest

from bot import db


@pytest.fixture
def temp_db(tmp_path):
    db.init_db(str(tmp_path / "test.db"))
    yield db


def test_get_or_create_user_creates_default_categories(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    categories = temp_db.get_categories(user_id)
    assert len(categories) == len(temp_db.DEFAULT_CATEGORIES)


def test_get_or_create_user_is_idempotent(temp_db):
    first = temp_db.get_or_create_user(111, "Alice")
    second = temp_db.get_or_create_user(111, "Alice")
    assert first == second


def test_get_category_returns_none_for_unknown_id(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    assert temp_db.get_category(user_id, 99999) is None


def test_add_and_complete_task(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    category = temp_db.get_categories(user_id)[0]
    task_id = temp_db.add_task(user_id, category["id"], "Do something")

    tasks = temp_db.get_tasks(user_id, category_id=category["id"])
    assert len(tasks) == 1
    assert tasks[0]["status"] == "active"

    temp_db.complete_task(user_id, task_id)
    task = temp_db.get_task(user_id, task_id)
    assert task["status"] == "done"
    assert task["done_at"] is not None


def test_delete_task(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    category = temp_db.get_categories(user_id)[0]
    task_id = temp_db.add_task(user_id, category["id"], "Temp task")
    temp_db.delete_task(user_id, task_id)
    assert temp_db.get_task(user_id, task_id) is None


def test_category_stats_counts_done_and_total(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    category = temp_db.get_categories(user_id)[0]
    t1 = temp_db.add_task(user_id, category["id"], "Task 1")
    temp_db.add_task(user_id, category["id"], "Task 2")
    temp_db.complete_task(user_id, t1)

    stats = {row["id"]: row for row in temp_db.category_stats(user_id)}
    row = stats[category["id"]]
    assert row["total"] == 2
    assert row["done"] == 1


def test_log_and_get_weights_ordered_desc(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    temp_db.log_weight(user_id, 60.0)
    temp_db.log_weight(user_id, 61.5)

    weights = temp_db.get_weights(user_id, limit=10)
    assert len(weights) == 2
    assert weights[0]["weight"] == 61.5
    assert weights[1]["weight"] == 60.0


def test_add_expense_and_totals_this_month(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    category = temp_db.get_categories(user_id)[0]
    temp_db.add_expense(user_id, category["id"], 1500.0, "бензин")
    temp_db.add_expense(user_id, category["id"], 500.0, "мойка")

    totals = temp_db.expense_totals_this_month(user_id)
    assert len(totals) == 1
    assert totals[0]["total"] == 2000.0


def test_add_expense_with_fuel_details(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    car_category = next(c for c in temp_db.get_categories(user_id) if c["slug"] == "car")
    temp_db.add_expense(
        user_id,
        car_category["id"],
        2500.0,
        note="Лукойл, 35.5 л",
        liters=35.5,
        station="Лукойл",
        payment_method="Карта",
    )

    with temp_db.get_conn() as conn:
        row = conn.execute("SELECT * FROM expenses WHERE user_id = ?", (user_id,)).fetchone()
    assert row["liters"] == 35.5
    assert row["station"] == "Лукойл"
    assert row["payment_method"] == "Карта"


def test_migrate_adds_missing_expense_columns(tmp_path):
    import sqlite3

    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            note TEXT,
            logged_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    db.init_db(path)
    with db.get_conn() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(expenses)").fetchall()}
    assert {"liters", "station", "payment_method"} <= columns


def test_add_expense_with_explicit_logged_at(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    car_category = next(c for c in temp_db.get_categories(user_id) if c["slug"] == "car")
    temp_db.add_expense(
        user_id,
        car_category["id"],
        1000.0,
        liters=20.0,
        station="Роснефть",
        payment_method="Деньги",
        logged_at="2026-05-15T12:00:00+00:00",
    )

    entries = temp_db.get_expenses_for_date(user_id, "2026-05-15")
    assert len(entries) == 1
    assert entries[0]["station"] == "Роснефть"
    assert entries[0]["liters"] == 20.0


def test_get_expenses_for_date_ignores_other_days(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    category = temp_db.get_categories(user_id)[0]
    temp_db.add_expense(user_id, category["id"], 100.0, logged_at="2026-05-15T12:00:00+00:00")
    temp_db.add_expense(user_id, category["id"], 200.0, logged_at="2026-05-16T12:00:00+00:00")

    assert len(temp_db.get_expenses_for_date(user_id, "2026-05-15")) == 1
    assert len(temp_db.get_expenses_for_date(user_id, "2026-05-16")) == 1
    assert len(temp_db.get_expenses_for_date(user_id, "2026-05-17")) == 0


def test_get_habit_days_summary_counts_done_per_day(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habit = temp_db.get_habits(user_id)[0]

    temp_db.toggle_habit_log(habit["id"], "2026-05-01")  # done
    temp_db.toggle_habit_log(habit["id"], "2026-05-02")  # done
    temp_db.toggle_habit_log(habit["id"], "2026-05-02")  # skip
    temp_db.toggle_habit_log(habit["id"], "2026-06-01")  # different month

    summary = temp_db.get_habit_days_summary(user_id, "2026-05")
    assert summary["2026-05-01"] == 1
    assert summary["2026-05-02"] == 0
    assert "2026-06-01" not in summary


def test_get_or_create_user_creates_default_habits(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habits = temp_db.get_habits(user_id)
    assert len(habits) == len(temp_db.DEFAULT_HABITS)


def test_toggle_habit_log_cycles_through_states(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habit = temp_db.get_habits(user_id)[0]
    day = "2026-07-01"

    assert temp_db.toggle_habit_log(habit["id"], day) == "done"
    assert temp_db.toggle_habit_log(habit["id"], day) == "skip"
    assert temp_db.toggle_habit_log(habit["id"], day) == "none"


def test_get_habit_logs_for_date_reflects_status(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habit = temp_db.get_habits(user_id)[0]
    day = "2026-07-01"

    rows = temp_db.get_habit_logs_for_date(user_id, day)
    assert rows[0]["status"] is None

    temp_db.toggle_habit_log(habit["id"], day)
    rows = temp_db.get_habit_logs_for_date(user_id, day)
    assert rows[0]["status"] == "done"


def test_get_habit_month_stats_counts_done_only(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habit = temp_db.get_habits(user_id)[0]

    temp_db.toggle_habit_log(habit["id"], "2026-07-01")  # done
    temp_db.toggle_habit_log(habit["id"], "2026-07-02")  # done
    temp_db.toggle_habit_log(habit["id"], "2026-07-02")  # skip
    temp_db.toggle_habit_log(habit["id"], "2026-06-15")  # done, different month

    stats = {row["id"]: row for row in temp_db.get_habit_month_stats(user_id, "2026-07")}
    assert stats[habit["id"]]["done_count"] == 1


def test_slot_message_roundtrip_and_overwrite(temp_db):
    assert temp_db.get_slot_message(555, "panel") is None

    temp_db.set_slot_message(555, "panel", 100)
    assert temp_db.get_slot_message(555, "panel") == 100

    temp_db.set_slot_message(555, "panel", 200)
    assert temp_db.get_slot_message(555, "panel") == 200


def test_slot_message_survives_across_get_conn_calls(temp_db):
    """Regression: message ids must persist independent of any in-memory state,
    so a bot process restart doesn't orphan the tracked panel/aux message."""
    temp_db.set_slot_message(1, "panel", 42)
    temp_db.set_slot_message(1, "aux", 99)
    assert temp_db.get_slot_message(1, "panel") == 42
    assert temp_db.get_slot_message(1, "aux") == 99


def test_clear_slot_message(temp_db):
    temp_db.set_slot_message(1, "panel", 42)
    temp_db.clear_slot_message(1, "panel")
    assert temp_db.get_slot_message(1, "panel") is None


def test_add_habit_creates_custom_checker(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habit_id = temp_db.add_habit(user_id, "Белок", "Здоровье", habit_type="number", unit="г", target=150)
    habit = temp_db.get_habit(user_id, habit_id)
    assert habit["title"] == "Белок"
    assert habit["type"] == "number"
    assert habit["unit"] == "г"
    assert habit["target"] == 150.0


def test_add_habit_generates_unique_slug_on_collision(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    id1 = temp_db.add_habit(user_id, "Тест", "Категория")
    id2 = temp_db.add_habit(user_id, "Тест", "Категория")
    h1 = temp_db.get_habit(user_id, id1)
    h2 = temp_db.get_habit(user_id, id2)
    assert h1["slug"] != h2["slug"]


def test_update_habit_changes_fields(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habit_id = temp_db.add_habit(user_id, "Старое", "Кат1")
    temp_db.update_habit(user_id, habit_id, title="Новое", category="Кат2")
    habit = temp_db.get_habit(user_id, habit_id)
    assert habit["title"] == "Новое"
    assert habit["category"] == "Кат2"


def test_delete_habit_removes_logs_and_entries(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habit_id = temp_db.add_habit(user_id, "Тест", "Кат", habit_type="note")
    day = "2026-07-01"
    temp_db.add_habit_entry(habit_id, day, "запись")
    temp_db.delete_habit(user_id, habit_id)
    assert temp_db.get_habit(user_id, habit_id) is None
    assert temp_db.get_habit_entries_for_date(habit_id, day) == []


def test_set_and_clear_habit_value(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habit_id = temp_db.add_habit(user_id, "Белок", "Кат", habit_type="number", unit="г")
    day = "2026-07-01"
    temp_db.set_habit_value(habit_id, day, 180.5)
    rows = temp_db.get_habit_logs_for_date(user_id, day)
    row = next(r for r in rows if r["id"] == habit_id)
    assert row["value"] == 180.5
    assert row["status"] == "done"

    temp_db.clear_habit_value(habit_id, day)
    rows = temp_db.get_habit_logs_for_date(user_id, day)
    row = next(r for r in rows if r["id"] == habit_id)
    assert row["value"] is None


def test_habit_entries_multiple_per_day(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habit_id = temp_db.add_habit(user_id, "Интерактивы", "Отношения", habit_type="note")
    day = "2026-07-01"
    temp_db.add_habit_entry(habit_id, day, "Кино")
    temp_db.add_habit_entry(habit_id, day, "Ужин")
    entries = temp_db.get_habit_entries_for_date(habit_id, day)
    assert [e["text"] for e in entries] == ["Кино", "Ужин"]


def test_delete_habit_entry_scoped_to_user(temp_db):
    user_a = temp_db.get_or_create_user(111, "Alice")
    user_b = temp_db.get_or_create_user(222, "Bob")
    habit_id = temp_db.add_habit(user_a, "Интерактивы", "Отношения", habit_type="note")
    day = "2026-07-01"
    entry_id = temp_db.add_habit_entry(habit_id, day, "Кино")

    temp_db.delete_habit_entry(user_b, entry_id)  # not owner, no-op
    assert len(temp_db.get_habit_entries_for_date(habit_id, day)) == 1

    temp_db.delete_habit_entry(user_a, entry_id)
    assert temp_db.get_habit_entries_for_date(habit_id, day) == []


def test_habit_entry_counts_for_month(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habit_id = temp_db.add_habit(user_id, "Интерактивы", "Отношения", habit_type="note")
    temp_db.add_habit_entry(habit_id, "2026-07-01", "a")
    temp_db.add_habit_entry(habit_id, "2026-07-01", "b")
    temp_db.add_habit_entry(habit_id, "2026-06-15", "c")

    counts = temp_db.get_habit_entry_counts_for_month(user_id, "2026-07")
    assert counts[(habit_id, "2026-07-01")] == 2
    assert (habit_id, "2026-06-15") not in counts


def test_habit_month_stats_includes_type_specific_totals(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    number_habit = temp_db.add_habit(user_id, "Белок", "Здоровье", habit_type="number", unit="г")
    note_habit = temp_db.add_habit(user_id, "Интерактивы", "Отношения", habit_type="note")

    temp_db.set_habit_value(number_habit, "2026-07-01", 100)
    temp_db.set_habit_value(number_habit, "2026-07-02", 150)
    temp_db.add_habit_entry(note_habit, "2026-07-01", "запись")

    stats = {r["id"]: r for r in temp_db.get_habit_month_stats(user_id, "2026-07")}
    assert stats[number_habit]["value_sum"] == 250
    assert stats[note_habit]["note_count"] == 1


def test_add_and_get_events(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    temp_db.add_event(user_id, "Вместе", "2024-01-01", recurring=False)
    temp_db.add_event(user_id, "ДР", "1995-08-15", recurring=True, emoji="🎂")
    events = temp_db.get_events(user_id)
    assert len(events) == 2
    by_title = {e["title"]: e for e in events}
    assert by_title["Вместе"]["recurring"] == 0
    assert by_title["ДР"]["recurring"] == 1
    assert by_title["ДР"]["emoji"] == "🎂"


def test_delete_event(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    event_id = temp_db.add_event(user_id, "Вместе", "2024-01-01")
    temp_db.delete_event(user_id, event_id)
    assert temp_db.get_events(user_id) == []


def test_default_habits_cover_new_types(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habits = {h["title"]: h for h in temp_db.get_habits(user_id)}
    assert habits["Вода"]["type"] == "number"
    assert habits["Тренировка"]["type"] == "interval"
    assert habits["Подработка"]["type"] == "interval"
    assert habits["Покупки"]["type"] == "expense"
    assert habits["Заправка"]["type"] == "fuel"
    assert habits["Ипотека внесена"]["type"] == "bool"


def test_habit_config_roundtrip(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habit_id = temp_db.add_habit(
        user_id, "Тест", "Кат", habit_type="interval",
        config={"has_amount": True, "subtype_options": ["A", "B"]},
    )
    habit = temp_db.get_habit(user_id, habit_id)
    cfg = temp_db.habit_config(habit)
    assert cfg == {"has_amount": True, "subtype_options": ["A", "B"]}


def test_habit_config_empty_when_unset(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habit_id = temp_db.add_habit(user_id, "Тест", "Кат")
    habit = temp_db.get_habit(user_id, habit_id)
    assert temp_db.habit_config(habit) == {}


def test_get_category_by_slug(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    category = temp_db.get_category_by_slug(user_id, "car")
    assert category is not None
    assert category["title"] == "Машина"
    assert temp_db.get_category_by_slug(user_id, "does-not-exist") is None


def test_add_and_get_habit_intervals(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habit_id = temp_db.add_habit(user_id, "Смена", "Работа", habit_type="interval")
    day = "2026-07-01"
    temp_db.add_habit_interval(habit_id, day, start_time="09:00", end_time="18:00")
    temp_db.add_habit_interval(habit_id, day, start_time="19:00", end_time="20:00", subtype="Переработка")

    intervals = temp_db.get_habit_intervals_for_date(habit_id, day)
    assert len(intervals) == 2
    assert intervals[1]["subtype"] == "Переработка"


def test_delete_habit_interval_scoped_to_user(temp_db):
    user_a = temp_db.get_or_create_user(111, "Alice")
    user_b = temp_db.get_or_create_user(222, "Bob")
    habit_id = temp_db.add_habit(user_a, "Смена", "Работа", habit_type="interval")
    day = "2026-07-01"
    interval_id = temp_db.add_habit_interval(habit_id, day, start_time="09:00", end_time="18:00")

    temp_db.delete_habit_interval(user_b, interval_id)  # not owner, no-op
    assert len(temp_db.get_habit_intervals_for_date(habit_id, day)) == 1

    temp_db.delete_habit_interval(user_a, interval_id)
    assert temp_db.get_habit_intervals_for_date(habit_id, day) == []


def test_delete_habit_cascades_intervals(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habit_id = temp_db.add_habit(user_id, "Смена", "Работа", habit_type="interval")
    day = "2026-07-01"
    temp_db.add_habit_interval(habit_id, day, start_time="09:00", end_time="18:00")
    temp_db.delete_habit(user_id, habit_id)
    assert temp_db.get_habit_intervals_for_date(habit_id, day) == []


def test_get_habit_intervals_for_month(temp_db):
    user_id = temp_db.get_or_create_user(111, "Alice")
    habit_id = temp_db.add_habit(user_id, "Смена", "Работа", habit_type="interval")
    temp_db.add_habit_interval(habit_id, "2026-07-01", start_time="09:00", end_time="18:00")
    temp_db.add_habit_interval(habit_id, "2026-06-15", start_time="09:00", end_time="18:00")

    rows = temp_db.get_habit_intervals_for_month(user_id, "2026-07")
    assert len(rows) == 1
    assert rows[0]["log_date"] == "2026-07-01"


def test_migration_removes_legacy_habits_and_backfills_new_ones(tmp_path):
    """Existing users (registered before this schema change) should have their
    built-in checkers upgraded in place: old ones removed/retyped, new ones added,
    with habit_logs for removed habits cascaded away (no orphan rows)."""
    import sqlite3

    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER UNIQUE NOT NULL, name TEXT, created_at TEXT NOT NULL);
        CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, slug TEXT NOT NULL, title TEXT NOT NULL, emoji TEXT NOT NULL, sort_order INTEGER NOT NULL, UNIQUE(user_id, slug));
        CREATE TABLE habits (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, slug TEXT NOT NULL, title TEXT NOT NULL, category TEXT NOT NULL, sort_order INTEGER NOT NULL, type TEXT NOT NULL DEFAULT 'bool', unit TEXT, target REAL, UNIQUE(user_id, slug));
        CREATE TABLE habit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, habit_id INTEGER NOT NULL, log_date TEXT NOT NULL, status TEXT NOT NULL, value REAL, UNIQUE(habit_id, log_date));
        """
    )
    conn.execute("INSERT INTO users (tg_id, name, created_at) VALUES (999, 'Legacy', '2026-01-01')")
    user_id = conn.execute("SELECT id FROM users WHERE tg_id = 999").fetchone()[0]
    conn.execute(
        "INSERT INTO habits (user_id, slug, title, category, sort_order) VALUES (?, 'productive_day', 'Продуктивный день', 'Работа', 0)",
        (user_id,),
    )
    legacy_habit_id = conn.execute("SELECT id FROM habits WHERE slug = 'productive_day'").fetchone()[0]
    conn.execute(
        "INSERT INTO habit_logs (habit_id, log_date, status) VALUES (?, '2026-01-05', 'done')",
        (legacy_habit_id,),
    )
    conn.commit()
    conn.close()

    from bot import db as botdb
    botdb.init_db(path)

    with botdb.get_conn() as conn:
        slugs = {row["slug"] for row in conn.execute("SELECT slug FROM habits WHERE user_id = ?", (user_id,)).fetchall()}
        assert "productive_day" not in slugs
        assert "side_job" in slugs
        orphan_count = conn.execute(
            "SELECT COUNT(*) AS c FROM habit_logs WHERE habit_id = ?", (legacy_habit_id,)
        ).fetchone()["c"]
        assert orphan_count == 0
