"""受认证 HTTP API 测试。"""

from fastapi.testclient import TestClient

from social_database.api import create_app
from social_database.service import ServiceSettings

TOKEN = "test-api-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _settings(database, **overrides):
    values = {
        "db_path": str(database),
        "api_token": TOKEN,
        "max_request_bytes": 1024 * 1024,
        "max_records": 100,
    }
    values.update(overrides)
    return ServiceSettings(**values)


def _payload():
    return {
        "schema_version": 1,
        "producer": "astrbot-http-test",
        "batch_id": "http-batch-001",
        "source_name": "anonymous-http-fixture",
        "observed_at_utc": "2026-08-25T12:00:00+08:00",
        "records": [
            {
                "group_id": "http-group-1",
                "user_id": "http-user-1",
                "nickname": "HTTP Example",
                "group_name": "HTTP Test Group",
            }
        ],
    }


def test_service_probes_and_bearer_authentication(tmp_path):
    app = create_app(_settings(tmp_path / "api.db"))

    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").json() == {"status": "ready"}
        assert client.get("/api/v1/stats").status_code == 401
        assert client.get(
            "/api/v1/stats",
            headers={"Authorization": "Bearer wrong-token-value"},
        ).status_code == 401

        response = client.get("/api/v1/stats", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["schema_version"] == 4
    assert response.json()["relations"] == 0
    assert "database_path" not in response.json()


def test_http_import_is_idempotent_and_searchable(tmp_path):
    app = create_app(_settings(tmp_path / "import.db"))
    payload = _payload()

    with TestClient(app) as client:
        first = client.post("/api/v1/imports/json", headers=AUTH, json=payload)
        reordered = {
            "records": payload["records"],
            "observed_at_utc": payload["observed_at_utc"],
            "source_name": payload["source_name"],
            "batch_id": payload["batch_id"],
            "producer": payload["producer"],
            "schema_version": payload["schema_version"],
        }
        duplicate = client.post(
            "/api/v1/imports/json",
            headers=AUTH,
            json=reordered,
        )
        search = client.get(
            "/api/v1/search",
            headers=AUTH,
            params={"q": "HTTP Example", "field": "nickname"},
        )
        batches = client.get("/api/v1/imports", headers=AUTH)
        health = client.get("/api/v1/health", headers=AUTH)

    assert first.status_code == 201
    assert first.json()["external_batch_id"] == "http-batch-001"
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert search.status_code == 200
    assert search.json()["results"][0]["user_id"] == "http-user-1"
    assert batches.json()["results"][0]["external_batch_id"] == "http-batch-001"
    assert health.status_code == 200
    assert health.json()["healthy"] is True
    assert "database_path" not in health.json()


def test_http_rejects_conflicting_batch_identity(tmp_path):
    app = create_app(_settings(tmp_path / "conflict.db"))
    payload = _payload()

    with TestClient(app) as client:
        assert client.post(
            "/api/v1/imports/json",
            headers=AUTH,
            json=payload,
        ).status_code == 201
        payload["records"][0]["user_id"] = "different-user"
        conflict = client.post(
            "/api/v1/imports/json",
            headers=AUTH,
            json=payload,
        )

    assert conflict.status_code == 409
    assert "不同内容" in conflict.json()["detail"]


def test_http_enforces_media_type_size_and_record_limits(tmp_path):
    limited_app = create_app(
        _settings(
            tmp_path / "limited.db",
            max_request_bytes=300,
            max_records=1,
        )
    )

    with TestClient(limited_app) as client:
        wrong_type = client.post(
            "/api/v1/imports/json",
            headers=AUTH,
            content=b"{}",
        )
        oversized = client.post(
            "/api/v1/imports/json",
            headers=AUTH,
            json=_payload() | {"padding": "x" * 500},
        )

    record_limited_app = create_app(
        _settings(tmp_path / "record-limited.db", max_records=1)
    )
    payload = _payload()
    payload["records"] = payload["records"] * 2
    with TestClient(record_limited_app) as client:
        too_many_records = client.post(
            "/api/v1/imports/json",
            headers=AUTH,
            json=payload,
        )

    assert wrong_type.status_code == 415
    assert oversized.status_code == 413
    assert too_many_records.status_code == 400
    assert "超过限制" in too_many_records.json()["detail"]
