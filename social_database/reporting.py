"""数据库统计和导入批次查询。"""

from pathlib import Path

from sqlalchemy import func, select

from .config import DB_PATH, SEARCH_TEXT_SEPARATOR
from .migrations import get_schema_version
from .models import (
    Group,
    ImportBatch,
    Member,
    MemberGroupInfo,
    RelationObservation,
    init_db,
)
from .output import format_json
from .search_index import get_search_index_state


def _utc_text(value) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds") + "Z"


def batch_to_dict(batch: ImportBatch) -> dict:
    """把导入批次转换为稳定的输出结构。"""

    return {
        "id": batch.id,
        "source_type": batch.source_type,
        "source_name": batch.source_name,
        "source_hash": batch.source_hash,
        "source_format_version": batch.source_format_version,
        "producer": batch.producer,
        "external_batch_id": batch.external_batch_id,
        "observed_at_utc": _utc_text(batch.observed_at_utc),
        "imported_at_utc": _utc_text(batch.imported_at_utc),
        "forced": batch.forced,
        "duplicate_of_id": batch.duplicate_of_id,
        "source_rows": batch.source_rows,
        "valid_rows": batch.valid_rows,
        "skipped_rows": batch.skipped_rows,
        "missing_user_id_rows": batch.missing_user_id_rows,
        "missing_group_id_rows": batch.missing_group_id_rows,
        "unique_groups": batch.unique_groups,
        "unique_members": batch.unique_members,
        "unique_relations": batch.unique_relations,
        "new_groups": batch.new_groups,
        "updated_groups": batch.updated_groups,
        "new_members": batch.new_members,
        "new_relations": batch.new_relations,
        "updated_relations": batch.updated_relations,
        "unchanged_relations": batch.unchanged_relations,
    }


def list_import_batches(
    db_path: str | Path = DB_PATH,
    *,
    limit: int = 20,
) -> list[dict]:
    """按时间倒序返回最近的成功导入批次。"""

    if limit < 1:
        raise ValueError("批次数量必须大于 0")

    engine, Session = init_db(db_path, create=False)
    try:
        with Session() as session:
            batches = session.scalars(
                select(ImportBatch)
                .order_by(ImportBatch.id.desc())
                .limit(limit)
            ).all()
            return [batch_to_dict(batch) for batch in batches]
    finally:
        engine.dispose()


def get_database_stats(db_path: str | Path = DB_PATH) -> dict:
    """返回数据库规模、schema 版本和最近导入信息。"""

    path = Path(db_path).expanduser().resolve()
    engine, Session = init_db(path, create=False)
    try:
        with Session() as session:
            counts = {
                "groups": session.scalar(
                    select(func.count()).select_from(Group)
                ),
                "members": session.scalar(
                    select(func.count()).select_from(Member)
                ),
                "relations": session.scalar(
                    select(func.count()).select_from(MemberGroupInfo)
                ),
                "relation_observations": session.scalar(
                    select(func.count()).select_from(RelationObservation)
                ),
                "import_batches": session.scalar(
                    select(func.count()).select_from(ImportBatch)
                ),
            }
            latest_batch = session.scalar(
                select(ImportBatch).order_by(ImportBatch.id.desc()).limit(1)
            )
        with engine.connect() as connection:
            search_index = get_search_index_state(connection)

        return {
            "database_path": str(path),
            "file_size_bytes": path.stat().st_size,
            "schema_version": get_schema_version(engine),
            **counts,
            "search_index": search_index,
            "latest_import": batch_to_dict(latest_batch)
            if latest_batch is not None
            else None,
        }
    finally:
        engine.dispose()


def format_database_stats(stats: dict, output_format: str = "json") -> str:
    """格式化数据库统计。"""

    if output_format == "json":
        return format_json(stats)
    if output_format != "text":
        raise ValueError(f"不支持的输出格式: {output_format}")

    lines = [
        f"数据库: {stats['database_path']}",
        f"Schema 版本: {stats['schema_version']}",
        f"文件大小: {stats['file_size_bytes']} 字节",
        SEARCH_TEXT_SEPARATOR,
        f"群组: {stats['groups']}",
        f"成员: {stats['members']}",
        f"成员-群组关系: {stats['relations']}",
        f"关系观察记录: {stats['relation_observations']}",
        f"成功导入批次: {stats['import_batches']}",
        (
            "搜索索引: "
            + (
                "就绪"
                if stats["search_index"] and stats["search_index"]["ready"]
                else "LIKE 回退"
            )
        ),
    ]
    if stats["latest_import"] is not None:
        latest = stats["latest_import"]
        lines.append(
            f"最近导入: #{latest['id']} "
            f"{latest['source_name']} "
            f"{latest['imported_at_utc']}"
        )
    return "\n".join(lines)


def format_import_batches(
    batches: list[dict],
    output_format: str = "json",
) -> str:
    """格式化导入批次列表。"""

    if output_format == "json":
        return format_json({"count": len(batches), "results": batches})
    if output_format != "text":
        raise ValueError(f"不支持的输出格式: {output_format}")
    if not batches:
        return "暂无导入批次。"

    lines = []
    for batch in batches:
        lines.extend(
            [
                SEARCH_TEXT_SEPARATOR,
                f"批次 #{batch['id']}: {batch['source_name']}",
                (
                    f"来源: {batch['source_type']} v"
                    f"{batch['source_format_version'] or '-'} / "
                    f"{batch['producer'] or '未标注'}"
                ),
                f"外部批次: {batch['external_batch_id'] or '-'}",
                f"采集时间: {batch['observed_at_utc'] or '-'}",
                f"导入时间: {batch['imported_at_utc']}",
                (
                    f"数据行: {batch['source_rows']}, "
                    f"有效: {batch['valid_rows']}, "
                    f"跳过: {batch['skipped_rows']}"
                ),
                (
                    f"关系: {batch['unique_relations']}, "
                    f"新增: {batch['new_relations']}, "
                    f"更新: {batch['updated_relations']}, "
                    f"未变化: {batch['unchanged_relations']}"
                ),
            ]
        )
    return "\n".join(lines)
