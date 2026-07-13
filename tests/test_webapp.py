import importlib

import pytest
from fastapi.testclient import TestClient

from bot import db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "web.db"))
    monkeypatch.setenv("WEB_PASSWORD", "secret")
    monkeypatch.setenv("WEB_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("WEB_USER_TG_ID", "0")
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_login_required_redirects(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/login"


def test_wrong_password_rejected(client):
    resp = client.post("/login", data={"password": "nope"}, follow_redirects=False)
    assert resp.status_code == 401


def test_login_and_panel(client):
    resp = client.post("/login", data={"password": "secret"}, follow_redirects=False)
    assert resp.status_code == 303
    panel = client.get("/")
    assert panel.status_code == 200
    assert "Отмечено сегодня" in panel.text


def test_toggle_checker_updates_partial(client):
    client.post("/login", data={"password": "secret"})
    user_id = db.get_or_create_user(0, "Веб")
    habit = db.get_habits(user_id)[0]
    today = db.today()
    resp = client.post(f"/checker/{habit['id']}/toggle", data={"day": today})
    assert resp.status_code == 200
    assert "Отмечено сегодня: <b>1/" in resp.text


def test_add_weight_and_expense(client):
    client.post("/login", data={"password": "secret"})
    client.post("/weight", data={"value": "61.5"}, follow_redirects=False)
    user_id = db.get_or_create_user(0, "Веб")
    assert db.get_weights(user_id)[0]["weight"] == 61.5

    category = next(c for c in db.get_categories(user_id) if c["slug"] == "car")
    client.post(
        "/expenses",
        data={"category_id": category["id"], "amount": "2500", "liters": "35",
              "station": "Лукойл", "payment_method": "Карта", "day": db.today()},
        follow_redirects=False,
    )
    entries = db.get_expenses_for_month(user_id, db.today()[:7])
    assert len(entries) == 1
    assert entries[0]["station"] == "Лукойл"


def test_create_number_and_note_checkers(client):
    client.post("/login", data={"password": "secret"})
    user_id = db.get_or_create_user(0, "Веб")

    client.post(
        "/checkers",
        data={"title": "Белок", "category": "Здоровье", "habit_type": "number",
              "unit": "г", "target": "150"},
        follow_redirects=False,
    )
    client.post(
        "/checkers",
        data={"title": "Интерактивы", "category": "Отношения", "habit_type": "note"},
        follow_redirects=False,
    )

    habits = {h["title"]: h for h in db.get_habits(user_id)}
    assert habits["Белок"]["type"] == "number"
    assert habits["Белок"]["unit"] == "г"
    assert habits["Белок"]["target"] == 150.0
    assert habits["Интерактивы"]["type"] == "note"


def test_set_number_value_via_checker_endpoint(client):
    client.post("/login", data={"password": "secret"})
    user_id = db.get_or_create_user(0, "Веб")
    client.post(
        "/checkers",
        data={"title": "Белок", "category": "Здоровье", "habit_type": "number", "unit": "г"},
    )
    habit = next(h for h in db.get_habits(user_id) if h["title"] == "Белок")
    today = db.today()

    resp = client.post(f"/checker/{habit['id']}/value", data={"day": today, "value": "180"})
    assert resp.status_code == 200
    assert "180" in resp.text

    logs = db.get_habit_logs_for_month(user_id, today[:7])
    assert logs[(habit["id"], today)]["value"] == 180.0


def test_add_and_delete_note_entries(client):
    client.post("/login", data={"password": "secret"})
    user_id = db.get_or_create_user(0, "Веб")
    client.post("/checkers", data={"title": "Интерактивы", "category": "Отношения", "habit_type": "note"})
    habit = next(h for h in db.get_habits(user_id) if h["title"] == "Интерактивы")
    today = db.today()

    resp = client.post(f"/checker/{habit['id']}/entry", data={"day": today, "text": "Кино"})
    assert "Кино" in resp.text
    entries = db.get_habit_entries_for_date(habit["id"], today)
    assert len(entries) == 1

    resp = client.post(f"/entry/{entries[0]['id']}/delete", data={"day": today})
    assert "Кино" not in resp.text
    assert db.get_habit_entries_for_date(habit["id"], today) == []


def test_delete_checker_removes_it(client):
    client.post("/login", data={"password": "secret"})
    user_id = db.get_or_create_user(0, "Веб")
    habit = db.get_habits(user_id)[0]
    resp = client.post(f"/checkers/{habit['id']}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert db.get_habit(user_id, habit["id"]) is None


def test_event_countup_and_countdown(client):
    client.post("/login", data={"password": "secret"})
    user_id = db.get_or_create_user(0, "Веб")

    client.post("/events", data={"title": "Вместе", "event_date": "2024-01-01"}, follow_redirects=False)
    client.post(
        "/events", data={"title": "ДР", "event_date": "1995-08-15", "recurring": "1"},
        follow_redirects=False,
    )
    events = {e["title"]: e for e in db.get_events(user_id)}
    assert events["Вместе"]["recurring"] == 0
    assert events["ДР"]["recurring"] == 1

    from webapp.app import event_display
    today = db.today()
    countup = event_display(events["Вместе"], today)
    assert countup["mode"] == "countup"
    assert countup["days"] > 0

    countdown = event_display(events["ДР"], today)
    assert countdown["mode"] == "countdown"
    assert countdown["days"] >= 0


def test_calendar_grid_shows_checkers_and_values(client):
    client.post("/login", data={"password": "secret"})
    user_id = db.get_or_create_user(0, "Веб")
    client.post(
        "/checkers",
        data={"title": "Белок", "category": "Здоровье", "habit_type": "number", "unit": "г"},
    )
    habit = next(h for h in db.get_habits(user_id) if h["title"] == "Белок")
    client.post(f"/checker/{habit['id']}/value", data={"day": db.today(), "value": "180"})

    resp = client.get("/calendar")
    assert resp.status_code == 200
    assert "grid-table" in resp.text
    assert "Белок" in resp.text
