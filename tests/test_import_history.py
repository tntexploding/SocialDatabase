"""来源哈希、批次历史和数据库统计测试。"""

from sqlalchemy import func, select

from social_database.config import REQUIRED_COLUMNS
from social_database.importer import import_xlsx
from social_database.models import (
    ImportBatch,
    RelationObservation,
    init_db,
)
from social_database.reporting import (
    format_database_stats,
    format_import_batches,
    get_database_stats,
    list_import_batches,
)


def record(**values):
    result = {column: None for column in REQUIRED_COLUMNS}
    result.update(values)
    return result


def test_import_history_duplicate_detection_and_force(
    tmp_path,
    workbook_factory,
):
    workbook = workbook_factory(
        tmp_path / "source.xlsx",
        {
            "Members": [
                record(
                    group_id="g-1",
                    user_id="u-1",
                    nickname="Alice",
                    group_name="Group",
                ),
                record(user_id="missing-group"),
                record(group_id="missing-user"),
            ]
        },
    )
    database = tmp_path / "history.db"

    first = import_xlsx(workbook, database)
    duplicate = import_xlsx(workbook, database)
    forced = import_xlsx(workbook, database, force=True)

    assert first.batch_id == 1
    assert first.source_rows == 3
    assert first.valid_rows == 1
    assert first.skipped_rows == 2
    assert first.missing_user_id_rows == 1
    assert first.missing_group_id_rows == 1
    assert first.new_relations == 1

    assert duplicate.duplicate is True
    assert duplicate.duplicate_of == first.batch_id
    assert duplicate.batch_id == first.batch_id

    assert forced.duplicate is False
    assert forced.batch_id == 2
    assert forced.duplicate_of == first.batch_id
    assert forced.unchanged_relations == 1

    engine, Session = init_db(database, create=False)
    try:
        with Session() as session:
            assert (
                session.scalar(select(func.count()).select_from(ImportBatch))
                == 2
            )
            observation = session.get(RelationObservation, ("u-1", "g-1"))
            assert observation.first_seen_batch_id == 1
            assert observation.last_seen_batch_id == 2
    finally:
        engine.dispose()

    batches = list_import_batches(database, limit=10)
    stats = get_database_stats(database)

    assert [batch["id"] for batch in batches] == [2, 1]
    assert batches[0]["source_format_version"] == 1
    assert batches[0]["producer"] is None
    assert batches[0]["observed_at_utc"] is None
    assert stats["schema_version"] == 3
    assert stats["import_batches"] == 2
    assert stats["relations"] == 1
    assert stats["relation_observations"] == 1
    assert '"count": 2' in format_import_batches(batches)
    assert "成功导入批次: 2" in format_database_stats(stats, "text")
