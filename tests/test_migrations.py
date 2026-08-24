"""数据库 schema 版本和旧库升级测试。"""

import sqlite3

from sqlalchemy import func, select

from social_database.migrations import CURRENT_SCHEMA_VERSION, get_schema_version
from social_database.models import (
    ImportBatch,
    MemberGroupInfo,
    RelationObservation,
    init_db,
)


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
