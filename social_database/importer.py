"""从 xlsx 文件导入数据到 SQLite。"""

from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session as SessionType

from .config import DB_PATH, REQUIRED_COLUMNS
from .models import Group, Member, MemberGroupInfo, init_db

Record = dict[str, str | None]
RELATION_FIELDS = ("nickname", "card", "join_time", "last_sent_time", "title")


@dataclass(frozen=True)
class ImportStats:
    """一次导入实际处理的数据规模。"""

    valid_rows: int
    groups: int
    members: int
    relations: int


def _normalize_cell(value: Any) -> str | None:
    """把 Excel 单元格转换为稳定的文本值。"""

    if value is None:
        return None
    if isinstance(value, datetime):
        text = value.isoformat(sep=" ")
    elif isinstance(value, (date, time)):
        text = value.isoformat()
    else:
        text = str(value).strip()
    return text or None


def parse_xlsx(filepath: str | Path) -> list[Record]:
    """读取全部工作表，校验表头并返回同时含用户和群组 ID 的记录。"""

    path = Path(filepath)
    if not path.is_file():
        raise FileNotFoundError(f"Excel 文件不存在: {path}")
    if path.suffix.lower() != ".xlsx":
        raise ValueError(f"仅支持 .xlsx 文件: {path}")

    workbook = load_workbook(path, read_only=True, data_only=True)
    rows: list[Record] = []

    try:
        for worksheet in workbook.worksheets:
            iterator = worksheet.iter_rows(values_only=True)
            try:
                header_row = next(iterator)
            except StopIteration as exc:
                raise ValueError(f"工作表 {worksheet.title!r} 为空") from exc

            headers = {
                header: index
                for index, value in enumerate(header_row)
                if (header := _normalize_cell(value)) is not None
            }
            missing = sorted(set(REQUIRED_COLUMNS) - set(headers))
            if missing:
                missing_text = ", ".join(missing)
                raise ValueError(
                    f"工作表 {worksheet.title!r} 缺少必要列: {missing_text}"
                )

            for row in iterator:
                record = {
                    column: _normalize_cell(row[headers[column]])
                    if headers[column] < len(row)
                    else None
                    for column in REQUIRED_COLUMNS
                }
                if record["user_id"] and record["group_id"]:
                    rows.append(record)
    finally:
        workbook.close()

    return rows


def import_to_db(rows: list[Record], session: SessionType) -> ImportStats:
    """使用 SQLite upsert 合并群组、成员和成员-群组关系。"""

    groups: dict[str, dict[str, str | None]] = {}
    members: set[str] = set()
    relations: dict[tuple[str, str], Record] = {}

    for record in rows:
        user_id = record["user_id"]
        group_id = record["group_id"]
        if user_id is None or group_id is None:
            continue

        group_name = record.get("group_name")
        if group_id not in groups:
            groups[group_id] = {
                "group_id": group_id,
                "group_name": group_name,
            }
        elif group_name is not None:
            groups[group_id]["group_name"] = group_name

        members.add(user_id)
        relation_key = (user_id, group_id)
        relation = relations.setdefault(
            relation_key,
            {
                "user_id": user_id,
                "group_id": group_id,
                **{field: None for field in RELATION_FIELDS},
            },
        )
        for field in RELATION_FIELDS:
            if (value := record.get(field)) is not None:
                relation[field] = value

    if groups:
        group_insert = sqlite_insert(Group)
        group_upsert = group_insert.on_conflict_do_update(
            index_elements=[Group.group_id],
            set_={
                "group_name": func.coalesce(
                    group_insert.excluded.group_name,
                    Group.group_name,
                )
            },
        )
        session.execute(group_upsert, [groups[key] for key in sorted(groups)])

    if members:
        member_insert = sqlite_insert(Member).on_conflict_do_nothing(
            index_elements=[Member.user_id]
        )
        session.execute(
            member_insert,
            [{"user_id": user_id} for user_id in sorted(members)],
        )

    if relations:
        relation_insert = sqlite_insert(MemberGroupInfo)
        relation_upsert = relation_insert.on_conflict_do_update(
            index_elements=[
                MemberGroupInfo.user_id,
                MemberGroupInfo.group_id,
            ],
            set_={
                field: func.coalesce(
                    getattr(relation_insert.excluded, field),
                    getattr(MemberGroupInfo, field),
                )
                for field in RELATION_FIELDS
            },
        )
        session.execute(
            relation_upsert,
            [relations[key] for key in sorted(relations)],
        )

    session.flush()
    return ImportStats(
        valid_rows=len(rows),
        groups=len(groups),
        members=len(members),
        relations=len(relations),
    )


def import_xlsx(
    filepath: str | Path,
    db_path: str | Path = DB_PATH,
) -> ImportStats:
    """执行解析和事务化导入，失败时自动回滚。"""

    print(f"正在解析: {filepath}")
    rows = parse_xlsx(filepath)
    print(f"解析到 {len(rows)} 条有效记录")

    engine, Session = init_db(db_path, create=True)
    try:
        with Session.begin() as session:
            stats = import_to_db(rows, session)
    finally:
        engine.dispose()

    print(
        "导入完成: "
        f"{stats.groups} 个群组, "
        f"{stats.members} 个成员, "
        f"{stats.relations} 条成员-群组关系"
    )
    return stats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="导入群成员 xlsx 数据")
    parser.add_argument("xlsx_path", help="xlsx 文件路径")
    parser.add_argument("--db", default=DB_PATH, help="SQLite 数据库路径")
    arguments = parser.parse_args()
    import_xlsx(arguments.xlsx_path, arguments.db)
