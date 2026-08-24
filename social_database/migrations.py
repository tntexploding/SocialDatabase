"""轻量级 SQLite schema 版本与迁移。"""

from datetime import datetime, timezone

from sqlalchemy.engine import Connection, Engine

CURRENT_SCHEMA_VERSION = 1


class DatabaseVersionError(RuntimeError):
    """数据库版本高于当前程序支持版本。"""


def _read_version(connection: Connection) -> int:
    return int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())


def get_schema_version(engine: Engine) -> int:
    """返回数据库的 PRAGMA user_version。"""

    with engine.connect() as connection:
        return _read_version(connection)


def _upgrade_to_version_1(connection: Connection) -> None:
    """为既有关系建立一个不改变业务数据的历史基线。"""

    missing_relations = connection.exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM member_group_info AS relation
        LEFT JOIN relation_observations AS observation
          ON observation.user_id = relation.user_id
         AND observation.group_id = relation.group_id
        WHERE observation.user_id IS NULL
        """
    ).scalar_one()
    if not missing_relations:
        return

    group_count = connection.exec_driver_sql(
        "SELECT COUNT(*) FROM groups"
    ).scalar_one()
    member_count = connection.exec_driver_sql(
        "SELECT COUNT(*) FROM members"
    ).scalar_one()
    relation_count = connection.exec_driver_sql(
        "SELECT COUNT(*) FROM member_group_info"
    ).scalar_one()
    imported_at = (
        datetime.now(timezone.utc)
        .replace(tzinfo=None)
        .isoformat(sep=" ", timespec="microseconds")
    )

    result = connection.exec_driver_sql(
        """
        INSERT INTO import_batches (
            source_type,
            source_name,
            source_hash,
            imported_at_utc,
            forced,
            duplicate_of_id,
            source_rows,
            valid_rows,
            skipped_rows,
            missing_user_id_rows,
            missing_group_id_rows,
            unique_groups,
            unique_members,
            unique_relations,
            new_groups,
            updated_groups,
            new_members,
            new_relations,
            updated_relations,
            unchanged_relations
        )
        VALUES (
            ?, ?, NULL, ?, 0, NULL,
            ?, ?, 0, 0, 0,
            ?, ?, ?, ?, 0, ?, ?, 0, 0
        )
        """,
        (
            "legacy",
            "pre-0.3 database",
            imported_at,
            relation_count,
            relation_count,
            group_count,
            member_count,
            relation_count,
            group_count,
            member_count,
            relation_count,
        ),
    )
    batch_id = result.lastrowid

    connection.exec_driver_sql(
        """
        INSERT INTO relation_observations (
            user_id,
            group_id,
            first_seen_batch_id,
            last_seen_batch_id
        )
        SELECT
            relation.user_id,
            relation.group_id,
            ?,
            ?
        FROM member_group_info AS relation
        LEFT JOIN relation_observations AS observation
          ON observation.user_id = relation.user_id
         AND observation.group_id = relation.group_id
        WHERE observation.user_id IS NULL
        """,
        (batch_id, batch_id),
    )


def upgrade_database(engine: Engine) -> int:
    """按顺序应用兼容迁移并返回最终 schema 版本。"""

    with engine.begin() as connection:
        version = _read_version(connection)
        if version > CURRENT_SCHEMA_VERSION:
            raise DatabaseVersionError(
                "数据库版本 "
                f"{version} 高于程序支持的 {CURRENT_SCHEMA_VERSION}"
            )

        if version < 1:
            _upgrade_to_version_1(connection)
            connection.exec_driver_sql("PRAGMA user_version = 1")
            version = 1

    return version
