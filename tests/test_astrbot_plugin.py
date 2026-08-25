"""Tests for the standalone AstrBot adapter without requiring AstrBot."""

import ast
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from integrations.astrbot_plugin_socialdatabase.batch import (
    create_group_batch,
)
from integrations.astrbot_plugin_socialdatabase.queue_store import QueueStore
from integrations.astrbot_plugin_socialdatabase.settings import PluginSettings
from integrations.astrbot_plugin_socialdatabase.uploader import (
    QueueWorker,
    UploadClient,
)
from social_database.json_importer import parse_json_payload

PLUGIN_DIR = (
    Path(__file__).resolve().parents[1]
    / "integrations"
    / "astrbot_plugin_socialdatabase"
)


def _payload():
    return {
        "schema_version": 1,
        "producer": "astrbot-test",
        "batch_id": "stable-plugin-batch-001",
        "source_name": "anonymous-plugin-fixture",
        "observed_at_utc": "2026-08-25T04:00:00Z",
        "records": [
            {
                "group_id": "group-1",
                "user_id": "user-1",
                "nickname": "Example",
            }
        ],
    }


def test_plugin_maps_onebot_group_to_json_v1_batch():
    payload, skipped = create_group_batch(
        group_id=12345,
        group_name="Example Group",
        members=[
            {
                "user_id": 67890,
                "nickname": "Example Member",
                "age": 20,
                "card_changeable": True,
                "title": {"invalid": "non-scalar"},
            },
            {"user_id": None, "nickname": "Skipped"},
        ],
        producer="astrbot-test",
        observed_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
        batch_id="stable-plugin-batch-001",
    )

    parsed, source = parse_json_payload(payload)

    assert skipped == 1
    assert payload["batch_id"] == "stable-plugin-batch-001"
    assert payload["records"] == [
        {
            "group_id": "12345",
            "user_id": "67890",
            "group_name": "Example Group",
            "nickname": "Example Member",
            "age": 20,
            "card_changeable": True,
        }
    ]
    assert parsed.rows[0]["user_id"] == "67890"
    assert source.external_batch_id == "stable-plugin-batch-001"


def test_queue_survives_restart_and_preserves_batch_identity(tmp_path):
    payload = _payload()
    first_store = QueueStore(tmp_path / "plugin-data")
    queued = first_store.enqueue(payload)

    restarted_store = QueueStore(tmp_path / "plugin-data")
    restored = restarted_store.pending_items(
        now=datetime.now(timezone.utc) + timedelta(seconds=1)
    )

    assert len(restored) == 1
    assert restored[0].queue_id == queued.queue_id
    assert restored[0].payload == payload
    assert restored[0].payload["batch_id"] == payload["batch_id"]
    assert restarted_store.counts() == {"pending": 1, "rejected": 0}


def test_queue_retries_then_acknowledges_without_success_archive(
    tmp_path,
    monkeypatch,
):
    import integrations.astrbot_plugin_socialdatabase.queue_store as queue_module

    current = {"now": datetime(2026, 8, 25, tzinfo=timezone.utc)}
    monkeypatch.setattr(queue_module, "_utc_now", lambda: current["now"])
    store = QueueStore(tmp_path / "plugin-data")
    payload = _payload()
    store.enqueue(payload)
    responses = [(503, "temporarily unavailable"), (201, "created")]
    uploaded = []

    async def sender(endpoint, token, sent_payload, timeout, no_cache):
        uploaded.append((endpoint, token, sent_payload, timeout, no_cache))
        return responses.pop(0)

    settings = PluginSettings(
        api_token="plugin-test-token-0123456789",
        retry_interval_seconds=1,
    )
    worker = QueueWorker(
        store,
        UploadClient(settings, sender=sender),
        settings,
    )

    first = asyncio.run(worker.run_once())
    persisted = store.pending_items(now=current["now"] + timedelta(seconds=2))
    current["now"] += timedelta(seconds=2)
    second = asyncio.run(worker.run_once())

    assert first.retried == 1
    assert persisted[0].attempts == 1
    assert persisted[0].payload == payload
    assert second.sent == 1
    assert store.counts() == {"pending": 0, "rejected": 0}
    assert [entry[2]["batch_id"] for entry in uploaded] == [
        payload["batch_id"],
        payload["batch_id"],
    ]


def test_upload_status_classification_and_rejected_queue(tmp_path):
    statuses = [200, 400, 401, 409, 429, 500]

    async def sender(_endpoint, _token, _payload, _timeout, _no_cache):
        return statuses.pop(0), "response"

    settings = PluginSettings(api_token="plugin-test-token-0123456789")
    client = UploadClient(settings, sender=sender)

    async def classify():
        return [await client.send(_payload()) for _ in range(6)]

    results = asyncio.run(classify())

    assert [result.disposition for result in results] == [
        "success",
        "reject",
        "retry",
        "reject",
        "retry",
        "retry",
    ]

    async def conflict_sender(*_arguments):
        return 409, "batch conflict"

    store = QueueStore(tmp_path / "plugin-data")
    store.enqueue(_payload())
    worker = QueueWorker(
        store,
        UploadClient(settings, sender=conflict_sender),
        settings,
    )
    cycle = asyncio.run(worker.run_once())

    assert cycle.rejected == 1
    assert store.counts() == {"pending": 0, "rejected": 1}


def test_missing_token_keeps_batch_retryable():
    called = False

    async def sender(*_arguments):
        nonlocal called
        called = True
        return 201, "created"

    client = UploadClient(PluginSettings(), sender=sender)
    result = asyncio.run(client.send(_payload()))

    assert result.disposition == "retry"
    assert result.status_code is None
    assert called is False


def test_queue_quarantines_malformed_entry(tmp_path):
    store = QueueStore(tmp_path / "plugin-data")
    (store.pending_dir / "broken.json").write_text("not-json", encoding="utf-8")

    assert store.pending_items() == []
    assert store.counts() == {"pending": 0, "rejected": 1}


def test_plugin_distribution_contract_is_self_contained():
    schema = json.loads((PLUGIN_DIR / "_conf_schema.json").read_text("utf-8"))
    metadata = (PLUGIN_DIR / "metadata.yaml").read_text("utf-8")
    requirements = (PLUGIN_DIR / "requirements.txt").read_text("utf-8")
    source = (PLUGIN_DIR / "main.py").read_text("utf-8")

    ast.parse(source)
    assert set(schema) == {
        "server_url",
        "api_token",
        "producer",
        "request_timeout_seconds",
        "retry_interval_seconds",
        "max_attempts_per_cycle",
        "no_cache",
    }
    assert "name: astrbot_plugin_socialdatabase" in metadata
    assert "version: 0.8.0" in metadata
    assert "  - aiocqhttp" in metadata
    assert requirements.strip() == "aiohttp>=3.10,<4.0"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"server_url": "ftp://example.test"}, "HTTP"),
        ({"api_token": "short"}, "16"),
        ({"retry_interval_seconds": 0}, "大于 0"),
    ],
)
def test_plugin_settings_reject_invalid_values(overrides, message):
    with pytest.raises(ValueError, match=message):
        PluginSettings(**overrides)
