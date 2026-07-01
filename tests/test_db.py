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
