from app.config import PROJECT_ROOT, Settings
from app.database.database import SCHEMA_VERSION, check_db, init_db


def test_root_returns_app_info(client):
    res = client.get("/")
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "PySearch"
    assert body["health"] == "/health"


def test_health_reports_ok_and_database(client, settings):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["environment"] == "test"
    assert body["database"]["ok"] is True
    assert body["database"]["schema_version"] == SCHEMA_VERSION
    assert settings.DATABASE_PATH.exists()


def test_init_db_is_idempotent(tmp_path):
    db = tmp_path / "again.db"
    init_db(db)
    init_db(db)  # running twice must not fail or duplicate the row
    assert check_db(db)["schema_version"] == SCHEMA_VERSION


def test_relative_db_path_resolves_to_project_root():
    s = Settings(DATABASE_PATH="data/x.db")
    assert s.DATABASE_PATH == (PROJECT_ROOT / "data" / "x.db").resolve()


def test_cors_allows_frontend_origin(client):
    res = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_rejects_unknown_origin(client):
    res = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in res.headers
