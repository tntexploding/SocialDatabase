"""数据库 schema 版本和旧库升级测试。"""

import sqlite3

import pytest
from sqlalchemy import func, select

from social_database import search_index
from social_database.importer import import_to_db
from social_database.migrations import (
    CURRENT_SCHEMA_VERSION,
    DatabaseVersionError,
    get_schema_version,
)
from social_database.models import (
    ImportBatch,
    MemberGroupInfo,
    RelationObservation,
    init_db,
)
from social_database.search import search_page
from social_database.search_index import get_search_index_state


def create_legacy_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE groups (
            group_id VARCHAR NOT NULL PRIMARY KEY,
            group_name VARCHAR
        );
        CREATE TABLE members (
            user_id VARCHAR NOT NULL PRIMARY KEY
        );
        CREATE TABLE member_group_info (
            user_id VARCHAR NOT NULL,
            group_id VARCHAR NOT NULL,
            nickname VARCHAR,
            card VARCHAR,
            join_time VARCHAR,
            last_sent_time VARCHAR,
            title VARCHAR,
            PRIMARY KEY (user_id, group_id),
            FOREIGN KEY(user_id) REFERENCES members (user_id),
            FOREIGN KEY(group_id) REFERENCES groups (group_id)
        );
        INSERT INTO groups VALUES ("g-1", "Legacy Group");
        INSERT INTO members VALUES ("u-1");
        INSERT INTO member_group_info (
            user_id, group_id, nickname
        ) VALUES ("u-1", "g-1", "Legacy User");
        """
    )
    connection.commit()
    connection.close()


def create_schema_two_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE groups (
            group_id VARCHAR NOT NULL PRIMARY KEY,
            group_name VARCHAR
        );
        CREATE TABLE members (
            user_id VARCHAR NOT NULL PRIMARY KEY
        );
        CREATE TABLE member_group_info (
            user_id VARCHAR NOT NULL,
            group_id VARCHAR NOT NULL,
            nickname VARCHAR,
            card VARCHAR,
            join_time VARCHAR,
            last_sent_time VARCHAR,
            title VARCHAR,
            PRIMARY KEY (user_id, group_id)
        );
        CREATE TABLE import_batches (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            source_type VARCHAR NOT NULL,
            source_name VARCHAR,
            source_hash VARCHAR(64),
            imported_at_utc DATETIME NOT NULL,
            forced BOOLEAN NOT NULL,
            duplicate_of_id INTEGER,
            source_rows INTEGER NOT NULL,
            valid_rows INTEGER NOT NULL,
            skipped_rows INTEGER NOT NULL,
            missing_user_id_rows INTEGER NOT NULL,
            missing_group_id_rows INTEGER NOT NULL,
            unique_groups INTEGER NOT NULL,
            unique_members INTEGER NOT NULL,
            unique_relations INTEGER NOT NULL,
            new_groups INTEGER NOT NULL,
            updated_groups INTEGER NOT NULL,
            new_members INTEGER NOT NULL,
            new_relations INTEGER NOT NULL,
            updated_relations INTEGER NOT NULL,
            unchanged_relations INTEGER NOT NULL
        );
        CREATE TABLE relation_observations (
            user_id VARCHAR NOT NULL,
            group_id VARCHAR NOT NULL,
            first_seen_batch_id INTEGER NOT NULL,
            last_seen_batch_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, group_id)
        );
        INSERT INTO groups VALUES ('g-1', 'Schema Two Group');
        INSERT INTO members VALUES ('u-1');
        INSERT INTO member_group_info (
            user_id, group_id, nickname
        ) VALUES ('u-1', 'g-1', 'Schema Two User');
        INSERT INTO import_batches VALUES (
            1, 'xlsx', 'old.xlsx', NULL, '2026-08-24 00:00:00', 0, NULL,
            1, 1, 0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 0, 0
        );
        INSERT INTO relation_observations VALUES ('u-1', 'g-1', 1, 1);
        PRAGMA user_version = 2;
        """
    )
    connection.commit()
    connection.close()


def test_new_database_uses_current_schema_without_legacy_batch(tmp_path):
    database = tmp_path / "new.db"

    engine, Session = init_db(database)
    try:
        assert get_schema_version(engine) == CURRENT_SCHEMA_VERSION
        with engine.connect() as connection:
            state = get_search_index_state(connection)
            assert state["status"] in ("ready", "unavailable")
        with Session() as session:
            assert (
                session.scalar(select(func.count()).select_from(ImportBatch))
                == 0
            )
    finally:
        engine.dispose()


def test_legacy_database_gets_one_non_destructive_baseline(tmp_path):
    database = tmp_path / "legacy.db"
    create_legacy_database(database)

    engine, Session = init_db(database, create=False)
    try:
        assert get_schema_version(engine) == CURRENT_SCHEMA_VERSION
        with Session() as session:
            batch = session.scalar(select(ImportBatch))
            observation = session.get(RelationObservation, ("u-1", "g-1"))
            relation = session.get(MemberGroupInfo, ("u-1", "g-1"))

            assert batch.source_type == "legacy"
            assert batch.unique_relations == 1
            assert observation.first_seen_batch_id == batch.id
            assert observation.last_seen_batch_id == batch.id
            assert relation.nickname == "Legacy User"

        with engine.connect() as connection:
            state = get_search_index_state(connection)
            if state["ready"]:
                assert connection.exec_driver_sql(
                    "SELECT count(*) FROM member_search"
                ).scalar_one() == 1
    finally:
        engine.dispose()

    second_engine, SecondSession = init_db(database, create=False)
    try:
        with SecondSession() as session:
            assert (
                session.scalar(select(func.count()).select_from(ImportBatch))
                == 1
            )
            assert (
                session.scalar(
                    select(func.count()).select_from(RelationObservation)
                )
                == 1
            )
    finally:
        second_engine.dispose()


def test_missing_fts5_keeps_schema_and_like_search_available(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "without-fts.db"

    def fail_fts(_connection):
        raise sqlite3.OperationalError("no such module: fts5")

    monkeypatch.setattr(search_index, "_create_fts_table", fail_fts)
    engine, Session = init_db(database)
    try:
        with Session.begin() as session:
            stats = import_to_db(
                [
                    {
                        "user_id": "u-1",
                        "group_id": "g-1",
                        "group_name": "Group",
                        "nickname": "Searchable",
                        "card": None,
                        "join_time": None,
                        "last_sent_time": None,
                        "title": None,
                    }
                ],
                session,
            )
        with Session() as session:
            state = get_search_index_state(session.connection())
            result = search_page("Searchable", session, field="nickname")

        assert get_schema_version(engine) == CURRENT_SCHEMA_VERSION
        assert stats.search_index_status == "unavailable"
        assert state["status"] == "unavailable"
        assert result.backend == "like"
        assert [item["user_id"] for item in result.results] == ["u-1"]
    finally:
        engine.dispose()


def test_schema_two_migrates_to_three_without_losing_data(tmp_path):
    database = tmp_path / "schema-two.db"
    create_schema_two_database(database)

    engine, Session = init_db(database, create=False)
    try:
        assert get_schema_version(engine) == CURRENT_SCHEMA_VERSION == 3
        with Session() as session:
            relation = session.get(MemberGroupInfo, ("u-1", "g-1"))
            batch = session.get(ImportBatch, 1)
            assert relation.nickname == "Schema Two User"
            assert relation.area is None
            assert relation.role is None
            assert batch.source_type == "xlsx"
            assert batch.source_format_version is None
            assert batch.producer is None
            assert batch.observed_at_utc is None
    finally:
        engine.dispose()


def test_future_schema_is_rejected_without_creating_tables(tmp_path):
    database = tmp_path / "future.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE future_sentinel (value TEXT);
        INSERT INTO future_sentinel VALUES ('unchanged');
        PRAGMA user_version = 99;
        """
    )
    before = connection.execute(
        "SELECT name, sql FROM sqlite_master ORDER BY name"
    ).fetchall()
    connection.commit()
    connection.close()

    with pytest.raises(DatabaseVersionError, match="数据库版本 99"):
        init_db(database, create=False)

    connection = sqlite3.connect(database)
    try:
        after = connection.execute(
            "SELECT name, sql FROM sqlite_master ORDER BY name"
        ).fetchall()
        value = connection.execute(
            "SELECT value FROM future_sentinel"
        ).fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()

    assert after == before
    assert value == "unchanged"
    assert version == 99
