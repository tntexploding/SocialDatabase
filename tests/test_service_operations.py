"""Operational service tests for the 0.8.0 single-writer model."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import social_database.api as api_module
import social_database.importer as importer_module
from social_database.api import create_app
from social_database.json_importer import import_json_payload
from social_database.reporting import get_database_stats, list_import_batches
from social_database.service import ServiceSettings

TOKEN = "operations-test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _settings(database):
    return ServiceSettings(
        db_path=str(database),
        api_token=TOKEN,
        max_request_bytes=1024 * 1024,
        max_records=100,
    )


def _payload(batch_id, user_id):
    return {
        "schema_version": 1,
        "producer": "operations-test",
        "batch_id": batch_id,
        "observed_at_utc": "2026-08-25T12:00:00Z",
        "records": [
            {
                "group_id": "operations-group",
                "user_id": user_id,
                "nickname": "Anonymous",
            }
        ],
    }


def test_reads_continue_while_import_transaction_is_open(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "concurrent-read.db"
    transaction_open = threading.Event()
    release_import = threading.Event()
    original_import_to_db = importer_module.import_to_db

    def paused_import_to_db(records, session, batch_id=None):
        result = original_import_to_db(
            records,
            session,
            batch_id=batch_id,
        )
        transaction_open.set()
        if not release_import.wait(timeout=5):
            raise TimeoutError("test did not release import transaction")
        return result

    monkeypatch.setattr(
        importer_module,
        "import_to_db",
        paused_import_to_db,
    )
    app = create_app(_settings(database))

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        import_future = pool.submit(
            client.post,
            "/api/v1/imports/json",
            headers=AUTH,
            json=_payload("concurrent-batch", "concurrent-user"),
        )
        assert transaction_open.wait(timeout=2)
        read_future = pool.submit(client.get, "/api/v1/stats", headers=AUTH)
        try:
            read_response = read_future.result(timeout=2)
            assert import_future.done() is False
        finally:
            release_import.set()
        import_response = import_future.result(timeout=5)

    assert read_response.status_code == 200
    assert read_response.json()["relations"] == 0
    assert import_response.status_code == 201
    assert get_database_stats(database)["relations"] == 1


def test_concurrent_http_imports_are_serialized(tmp_path, monkeypatch):
    database = tmp_path / "serialized-writes.db"
    state_lock = threading.Lock()
    active = 0
    maximum_active = 0
    original_import = api_module.import_json_payload

    def observed_import(*args, **kwargs):
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            time.sleep(0.05)
            return original_import(*args, **kwargs)
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(api_module, "import_json_payload", observed_import)
    app = create_app(_settings(database))

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                client.post,
                "/api/v1/imports/json",
                headers=AUTH,
                json=_payload(f"serialized-{index}", f"user-{index}"),
            )
            for index in range(2)
        ]
        responses = [future.result(timeout=5) for future in futures]

    assert [response.status_code for response in responses] == [201, 201]
    assert maximum_active == 1
    assert get_database_stats(database)["relations"] == 2


def test_interrupted_import_rolls_back_all_business_rows(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "interrupted.db"
    original_import_to_db = importer_module.import_to_db

    class SimulatedInterruption(RuntimeError):
        pass

    def interrupted_import_to_db(records, session, batch_id=None):
        original_import_to_db(records, session, batch_id=batch_id)
        raise SimulatedInterruption("simulated process interruption")

    monkeypatch.setattr(
        importer_module,
        "import_to_db",
        interrupted_import_to_db,
    )

    with pytest.raises(SimulatedInterruption):
        import_json_payload(
            _payload("interrupted-batch", "interrupted-user"),
            database,
        )

    stats = get_database_stats(database)
    assert stats["groups"] == 0
    assert stats["members"] == 0
    assert stats["relations"] == 0
    assert stats["relation_observations"] == 0
    assert list_import_batches(database) == []
