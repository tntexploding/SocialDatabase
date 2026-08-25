"""标准 JSON 批次导入测试。"""

import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from social_database.importer import BatchIdentityConflictError
from social_database.json_importer import import_json, import_json_payload
from social_database.models import Group, ImportBatch, MemberGroupInfo, init_db
from social_database.search import search


FIXTURE = Path(__file__).parent / "fixtures" / "import-batch-v1.json"


def _business_rows(database):
    engine, Session = init_db(database, create=False)
    try:
        with Session() as session:
            relations = session.scalars(
                select(MemberGroupInfo).order_by(
                    MemberGroupInfo.user_id,
                    MemberGroupInfo.group_id,
                )
            ).all()
            groups = {
                group_id: group_name
                for group_id, group_name in session.execute(
                    select(Group.group_id, Group.group_name)
                )
            }
            return [
                {
                    column.name: getattr(relation, column.name)
                    for column in MemberGroupInfo.__table__.columns
                }
                | {"group_name": groups[relation.group_id]}
                for relation in relations
            ]
    finally:
        engine.dispose()


def test_json_import_records_metadata_and_observation_times(tmp_path):
    database = tmp_path / "json.db"

    first = import_json(FIXTURE, database)
    duplicate = import_json(FIXTURE, database)
    forced = import_json(FIXTURE, database, force=True)

    assert first.relations == 2
    assert duplicate.duplicate is True
    assert forced.batch_id == 2

    engine, Session = init_db(database, create=False)
    try:
        with Session() as session:
            batches = session.scalars(
                select(ImportBatch).order_by(ImportBatch.id)
            ).all()
            relation = session.get(
                MemberGroupInfo,
                ("example-user-1", "example-group-1"),
            )
            results = search("example-user-1", session, field="user_id")

            assert len(batches) == 2
            assert batches[0].source_type == "json"
            assert batches[0].source_format_version == 1
            assert batches[0].producer == "astrbot-example"
            assert batches[0].observed_at_utc == datetime(2026, 8, 25)
            assert relation.area == "Example Area"
            assert relation.card_changeable == "True"
            assert relation.is_robot == "False"

            first_group = results[0]["groups"][0]
            assert first_group["first_seen_batch_id"] == 1
            assert first_group["last_seen_batch_id"] == 2
            assert first_group["first_seen_at_utc"] == "2026-08-25T00:00:00Z"
            assert first_group["last_seen_at_utc"] == "2026-08-25T00:00:00Z"
    finally:
        engine.dispose()


def test_json_and_xlsx_adapters_produce_equivalent_business_rows(
    tmp_path,
    workbook_factory,
):
    from social_database.config import SOURCE_COLUMNS
    from social_database.importer import import_xlsx

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    workbook = workbook_factory(
        tmp_path / "equivalent.xlsx",
        {"Members": payload["records"]},
        headers=SOURCE_COLUMNS,
    )
    json_database = tmp_path / "from-json.db"
    xlsx_database = tmp_path / "from-xlsx.db"

    import_json(FIXTURE, json_database)
    import_xlsx(
        workbook,
        xlsx_database,
        producer="astrbot-example",
        observed_at_utc="2026-08-25T00:00:00Z",
    )

    assert _business_rows(json_database) == _business_rows(xlsx_database)


def test_invalid_json_batch_fails_before_database_creation(tmp_path):
    source = tmp_path / "invalid.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "producer": "example",
                "observed_at_utc": "2026-08-25T00:00:00Z",
                "records": [],
            }
        ),
        encoding="utf-8",
    )
    database = tmp_path / "should-not-exist.db"

    with pytest.raises(ValueError, match="不支持的 JSON 批次版本"):
        import_json(source, database)

    assert not database.exists()


def test_external_batch_id_is_stable_across_json_serialization(
    tmp_path,
    capsys,
):
    database = tmp_path / "stable-batch.db"
    source = tmp_path / "stable-batch.json"
    payload = {
        "schema_version": 1,
        "producer": "astrbot-example",
        "batch_id": "example-run-20260825-001",
        "observed_at_utc": "2026-08-25T00:00:00Z",
        "records": [
            {
                "group_id": "example-group",
                "user_id": "example-user",
                "nickname": "Example User",
            }
        ],
    }
    source.write_text(json.dumps(payload, indent=4), encoding="utf-8")

    first = import_json(source, database)
    reordered = {
        "records": payload["records"],
        "observed_at_utc": payload["observed_at_utc"],
        "batch_id": payload["batch_id"],
        "producer": payload["producer"],
        "schema_version": payload["schema_version"],
    }
    duplicate = import_json_payload(reordered, database)

    assert first.external_batch_id == "example-run-20260825-001"
    assert duplicate.duplicate is True
    assert duplicate.batch_id == first.batch_id
    output = capsys.readouterr().out
    assert "稳定外部批次" in output
    assert "--force" not in output

    engine, Session = init_db(database, create=False)
    try:
        with Session() as session:
            batch = session.scalar(select(ImportBatch))
            assert batch.external_batch_id == "example-run-20260825-001"
    finally:
        engine.dispose()


def test_reused_external_batch_id_with_different_content_is_rejected(tmp_path):
    database = tmp_path / "conflicting-batch.db"
    payload = {
        "schema_version": 1,
        "producer": "astrbot-example",
        "batch_id": "same-id",
        "observed_at_utc": "2026-08-25T00:00:00Z",
        "records": [{"group_id": "g-1", "user_id": "u-1"}],
    }
    import_json_payload(payload, database)
    conflicting = payload | {
        "records": [{"group_id": "g-1", "user_id": "u-2"}]
    }

    with pytest.raises(BatchIdentityConflictError, match="不同内容"):
        import_json_payload(conflicting, database)
