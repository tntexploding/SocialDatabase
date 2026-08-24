"""数据库 schema 版本和旧库升级测试。"""

import sqlite3

from sqlalchemy import func, select

from social_database import search_index
from social_database.importer import import_to_db
from social_database.migrations import CURRENT_SCHEMA_VERSION, get_schema_version
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
