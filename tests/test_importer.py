"""Excel 解析和数据库导入测试。"""

from datetime import datetime

import pytest
from sqlalchemy import func, select

from social_database.config import REQUIRED_COLUMNS
from social_database.importer import import_xlsx, parse_xlsx
from social_database.models import Group, Member, MemberGroupInfo, init_db
from social_database.search import search_page


def record(**values):
    result = {column: None for column in REQUIRED_COLUMNS}
    result.update(values)
    return result


def test_parse_all_sheets_and_skip_rows_without_ids(tmp_path, workbook_factory):
    workbook = workbook_factory(
        tmp_path / "members.xlsx",
        {
            "First": [
                record(
                    group_id=" g-1 ",
                    user_id="u-1",
                    nickname=" Alice ",
                    join_time=datetime(2026, 8, 24, 9, 30),
                    group_name="Group One",
                ),
                record(user_id="missing-group"),
            ],
            "Second": [
                record(
                    group_id="g-2",
                    user_id="u-2",
                    nickname="Bob",
                    group_name="Group Two",
                ),
                record(group_id="missing-user"),
            ],
        },
    )

    rows = parse_xlsx(workbook)

    assert len(rows) == 2
    assert rows[0]["group_id"] == "g-1"
    assert rows[0]["nickname"] == "Alice"
    assert rows[0]["join_time"] == "2026-08-24 09:30:00"
    assert rows[1]["user_id"] == "u-2"


def test_invalid_headers_fail_before_database_creation(tmp_path, workbook_factory):
    headers = [column for column in REQUIRED_COLUMNS if column != "title"]
    workbook = workbook_factory(
        tmp_path / "invalid.xlsx",
        {"Sheet": [record(group_id="g-1", user_id="u-1")]},
        headers=headers,
    )
    database = tmp_path / "should-not-exist.db"

    with pytest.raises(ValueError, match="缺少必要列.*title"):
        import_xlsx(workbook, database)

    assert not database.exists()


def test_import_deduplicates_and_updates_non_empty_values(
    tmp_path,
    workbook_factory,
):
    database = tmp_path / "members.db"
    first = workbook_factory(
        tmp_path / "first.xlsx",
        {
            "Members": [
                record(
                    group_id="g-1",
                    user_id="u-1",
                    nickname="Initial",
                    card="Keep this card",
                    group_name="Old group name",
                ),
                record(
                    group_id="g-1",
                    user_id="u-1",
                    nickname="Latest",
                    title="Admin",
                    group_name="New group name",
                ),
                record(
                    group_id="g-2",
                    user_id="u-1",
                    nickname="Other group nickname",
                    group_name="Second group",
                ),
                record(
                    group_id="g-1",
                    user_id="u-2",
                    nickname="Bob",
                    group_name="New group name",
                ),
            ]
        },
    )

    stats = import_xlsx(first, database)

    assert stats.valid_rows == 4
    assert stats.groups == 2
    assert stats.members == 2
    assert stats.relations == 3

    second = workbook_factory(
        tmp_path / "second.xlsx",
        {
            "Members": [
                record(
                    group_id="g-1",
                    user_id="u-1",
                    title="Owner",
                    group_name="Newest group name",
                )
            ]
        },
    )
    second_stats = import_xlsx(second, database)

    engine, Session = init_db(database, create=False)
    try:
        with Session() as session:
            assert session.scalar(select(func.count()).select_from(Group)) == 2
            assert session.scalar(select(func.count()).select_from(Member)) == 2
            assert (
                session.scalar(
                    select(func.count()).select_from(MemberGroupInfo)
                )
                == 3
            )
            group = session.get(Group, "g-1")
            relation = session.get(MemberGroupInfo, ("u-1", "g-1"))
            assert group.group_name == "Newest group name"
            assert relation.nickname == "Latest"
            assert relation.card == "Keep this card"
            assert relation.title == "Owner"

            current_group_name = search_page(
                "Newest group name",
                session,
                field="group_name",
            )
            old_group_name = search_page(
                "Old group name",
                session,
                field="group_name",
            )

            assert [
                item["user_id"] for item in current_group_name.results
            ] == ["u-1", "u-2"]
            assert old_group_name.results == []
            if second_stats.search_index_status == "ready":
                assert current_group_name.backend == "fts5"

        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    finally:
        engine.dispose()


def test_database_filename_without_parent_directory(
    tmp_path,
    monkeypatch,
    workbook_factory,
):
    workbook = workbook_factory(
        tmp_path / "single.xlsx",
        {
            "Members": [
                record(
                    group_id="g-1",
                    user_id="u-1",
                    group_name="Group",
                )
            ]
        },
    )
    monkeypatch.chdir(tmp_path)

    import_xlsx(workbook, "relative.db")

    assert (tmp_path / "relative.db").is_file()
