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
