"""正式 FTS5 索引的事务同步、降级与修复测试。"""

import sqlite3

import pytest

from social_database import search_index
from social_database.importer import import_to_db
from social_database.maintenance import check_database, reindex_database
from social_database.models import init_db
from social_database.search import search_page
from social_database.search_index import get_search_index_state


def relation(nickname, *, group_name="Group"):
    return {
        "user_id": "user-1",
        "group_id": "group-1",
        "group_name": group_name,
        "nickname": nickname,
        "card": None,
        "join_time": None,
        "last_sent_time": None,
        "title": None,
    }


def test_search_index_changes_rollback_with_business_transaction(tmp_path):
    database = tmp_path / "rollback.db"
    engine, Session = init_db(database)
    try:
        with pytest.raises(RuntimeError, match="rollback"):
            with Session.begin() as session:
                stats = import_to_db([relation("Transient")], session)
                if stats.search_index_status != "ready":
                    pytest.skip("当前 Python SQLite 未启用 FTS5 trigram")
                raise RuntimeError("rollback")

        with engine.connect() as connection:
            state = get_search_index_state(connection)
            indexed_rows = connection.exec_driver_sql(
                "SELECT count(*) FROM member_search"
            ).scalar_one()
            relation_rows = connection.exec_driver_sql(
                "SELECT count(*) FROM member_group_info"
            ).scalar_one()

        assert state["ready"] is True
        assert state["indexed_relations"] == 0
        assert indexed_rows == 0
        assert relation_rows == 0
    finally:
        engine.dispose()


def test_failed_index_sync_keeps_import_and_routes_to_like(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "fallback.db"
    engine, Session = init_db(database)
    try:
        with Session.begin() as session:
            initial = import_to_db([relation("Initial")], session)
        if initial.search_index_status != "ready":
            pytest.skip("当前 Python SQLite 未启用 FTS5 trigram")

        def fail_population(_connection):
            raise sqlite3.OperationalError("simulated rebuild failure")

        monkeypatch.setattr(
            search_index,
            "_populate_search_index",
            fail_population,
        )
        with Session.begin() as session:
            changed = import_to_db([relation("Changed")], session)

        with Session() as session:
            result = search_page("Changed", session, field="nickname")
            state = get_search_index_state(session.connection())

        assert changed.search_index_status == "stale"
        assert state["status"] == "stale"
        assert result.backend == "like"
        assert [item["user_id"] for item in result.results] == ["user-1"]
    finally:
        engine.dispose()


def test_health_detects_drift_and_manual_reindex_repairs_it(tmp_path):
    database = tmp_path / "repair.db"
    engine, Session = init_db(database)
    try:
        with Session.begin() as session:
            stats = import_to_db([relation("Initial")], session)
        if stats.search_index_status != "ready":
            pytest.skip("当前 Python SQLite 未启用 FTS5 trigram")

        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE member_group_info SET nickname = 'Drifted'"
            )
    finally:
        engine.dispose()

    drifted = check_database(database)
    assert drifted["healthy"] is False
    assert drifted["search_index"]["content_matches"] is False

    rebuilt = reindex_database(database)
    repaired = check_database(database)
    engine, Session = init_db(database, create=False)
    try:
        with Session() as session:
            result = search_page("Drifted", session, field="nickname")
    finally:
        engine.dispose()

    assert rebuilt["ready"] is True
    assert repaired["search_index"]["healthy"] is True
    assert repaired["search_index"]["content_matches"] is True
    assert result.backend == "fts5"


def test_missing_fts_table_falls_back_to_like(tmp_path):
    database = tmp_path / "missing-index.db"
    engine, Session = init_db(database)
    try:
        with Session.begin() as session:
            stats = import_to_db([relation("Searchable")], session)
        if stats.search_index_status != "ready":
            pytest.skip("当前 Python SQLite 未启用 FTS5 trigram")

        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE member_search")

        with Session() as session:
            result = search_page("Searchable", session, field="nickname")

        assert result.backend == "like"
        assert [item["user_id"] for item in result.results] == ["user-1"]
    finally:
        engine.dispose()
