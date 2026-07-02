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
