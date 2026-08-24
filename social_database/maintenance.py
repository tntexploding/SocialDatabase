"""SQLite 数据库健康检查与一致性备份。"""

import sqlite3
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from .config import DB_PATH, SEARCH_TEXT_SEPARATOR
from .migrations import CURRENT_SCHEMA_VERSION, get_schema_version
from .models import MemberGroupInfo, RelationObservation, init_db
from .output import format_json


class DatabaseMaintenanceError(RuntimeError):
    """数据库维护操作无法安全完成。"""


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def check_database(db_path: str | Path = DB_PATH) -> dict:
    """执行 SQLite、外键和关系观察覆盖检查。"""

    path = Path(db_path).expanduser().resolve()
    engine, Session = init_db(path, create=False)
    try:
        with engine.connect() as connection:
            integrity_messages = list(
                connection.exec_driver_sql("PRAGMA integrity_check").scalars()
            )
            foreign_key_rows = connection.exec_driver_sql(
                "PRAGMA foreign_key_check"
            ).all()

        with Session() as session:
            relation_count = session.scalar(
                select(func.count()).select_from(MemberGroupInfo)
            )
            observation_count = session.scalar(
                select(func.count()).select_from(RelationObservation)
            )
            missing_observations = session.scalar(
                select(func.count())
                .select_from(MemberGroupInfo)
                .outerjoin(
                    RelationObservation,
                    (
                        RelationObservation.user_id
                        == MemberGroupInfo.user_id
                    )
                    & (
                        RelationObservation.group_id
                        == MemberGroupInfo.group_id
                    ),
                )
                .where(RelationObservation.user_id.is_(None))
            )

        schema_version = get_schema_version(engine)
        healthy = (
            integrity_messages == ["ok"]
            and not foreign_key_rows
            and missing_observations == 0
            and schema_version == CURRENT_SCHEMA_VERSION
        )
        return {
            "healthy": healthy,
            "database_path": str(path),
            "file_size_bytes": path.stat().st_size,
            "schema_version": schema_version,
            "expected_schema_version": CURRENT_SCHEMA_VERSION,
            "integrity_messages": integrity_messages,
            "foreign_key_violations": [
                {
                    "table": row[0],
                    "rowid": row[1],
                    "parent": row[2],
                    "foreign_key_index": row[3],
                }
                for row in foreign_key_rows
            ],
            "relations": relation_count,
            "relation_observations": observation_count,
            "missing_relation_observations": missing_observations,
        }
    finally:
        engine.dispose()


def format_database_check(report: dict, output_format: str = "json") -> str:
    """格式化数据库健康检查。"""

    if output_format == "json":
        return format_json(report)
    if output_format != "text":
        raise ValueError(f"不支持的输出格式: {output_format}")

    status = "健康" if report["healthy"] else "异常"
    return "\n".join(
        [
            f"数据库: {report['database_path']}",
            f"状态: {status}",
            f"Schema: {report['schema_version']} "
            f"(期望 {report['expected_schema_version']})",
            SEARCH_TEXT_SEPARATOR,
            f"SQLite 完整性: {', '.join(report['integrity_messages'])}",
            f"外键违规: {len(report['foreign_key_violations'])}",
            f"成员关系: {report['relations']}",
            f"关系观察记录: {report['relation_observations']}",
            (
                "缺少观察记录的关系: "
                f"{report['missing_relation_observations']}"
            ),
        ]
    )


def _default_backup_path(source: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return source.parent / "backups" / f"{source.stem}-{timestamp}.db"


def backup_database(
    db_path: str | Path = DB_PATH,
    destination: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> dict:
    """使用 SQLite Online Backup API 创建一致性数据库副本。"""

    source = Path(db_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"数据库不存在: {source}")

    target = (
        Path(destination).expanduser().resolve()
        if destination is not None
        else _default_backup_path(source)
    )
    if source == target:
        raise ValueError("备份目标不能与源数据库相同")
    if target.exists() and not overwrite:
        raise FileExistsError(f"备份文件已存在: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.{uuid4().hex}.tmp"
    )

    try:
        source_connection = None
        target_connection = None
        try:
            source_connection = sqlite3.connect(
                source.as_uri() + "?mode=ro",
                uri=True,
            )
            target_connection = sqlite3.connect(temporary)
            source_connection.backup(target_connection)
            target_connection.commit()
            integrity = target_connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]
            if integrity != "ok":
                raise DatabaseMaintenanceError(
                    f"备份完整性检查失败: {integrity}"
                )
        finally:
            if target_connection is not None:
                target_connection.close()
            if source_connection is not None:
                source_connection.close()

        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()

    return {
        "source_path": str(source),
        "backup_path": str(target),
        "file_size_bytes": target.stat().st_size,
        "sha256": _file_hash(target),
        "integrity": "ok",
    }


def format_backup_result(result: dict, output_format: str = "json") -> str:
    """格式化备份结果。"""

    if output_format == "json":
        return format_json(result)
    if output_format != "text":
        raise ValueError(f"不支持的输出格式: {output_format}")
    return "\n".join(
        [
            f"源数据库: {result['source_path']}",
            f"备份文件: {result['backup_path']}",
            f"文件大小: {result['file_size_bytes']} 字节",
            f"SHA-256: {result['sha256']}",
            f"完整性: {result['integrity']}",
        ]
    )
