"""搜索结果导出为 JSON、CSV 或 xlsx。"""

import csv
import json
from pathlib import Path
from uuid import uuid4

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from .config import DB_PATH, RELATION_FIELDS
from .models import init_db
from .search import SEARCH_FIELD_NAMES, search

EXPORT_FORMATS = ("json", "csv", "xlsx")
EXPORT_COLUMNS = (
    "user_id",
    "group_id",
    "group_name",
    *RELATION_FIELDS,
    "first_seen_batch_id",
    "last_seen_batch_id",
    "first_seen_at_utc",
    "last_seen_at_utc",
)


def _resolve_export_format(path: Path, output_format: str | None) -> str:
    selected = output_format or path.suffix.lower().lstrip(".")
    if selected not in EXPORT_FORMATS:
        supported = ", ".join(EXPORT_FORMATS)
        raise ValueError(f"不支持的导出格式；可用格式: {supported}")
    return selected


def _flatten_results(results: list[dict]) -> list[dict]:
    rows = []
    for user in results:
        for group in user["groups"]:
            rows.append(
                {
                    "user_id": user["user_id"],
                    **{column: group.get(column) for column in EXPORT_COLUMNS[1:]},
                }
            )
    return rows


def _write_json(path: Path, keyword: str, field: str, results: list[dict]) -> None:
    payload = {
        "keyword": keyword,
        "field": field,
        "count": len(results),
        "results": results,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _xlsx_literal_value(worksheet, value):
    """让公式型来源文本在 xlsx 中保持为文本。"""

    if not isinstance(value, str) or not value.startswith("="):
        return value
    cell = WriteOnlyCell(worksheet, value=value)
    cell.data_type = "s"
    return cell


def _write_xlsx(path: Path, rows: list[dict]) -> None:
    workbook = Workbook(write_only=True)
    try:
        worksheet = workbook.create_sheet("search_results")
        worksheet.append(list(EXPORT_COLUMNS))
        for row in rows:
            worksheet.append(
                [
                    _xlsx_literal_value(worksheet, row.get(column))
                    for column in EXPORT_COLUMNS
                ]
            )
        workbook.save(path)
    finally:
        workbook.close()


def export_search_results(
    keyword: str,
    output_path: str | Path,
    db_path: str | Path = DB_PATH,
    *,
    field: str = "any",
    output_format: str | None = None,
    overwrite: bool = False,
) -> dict:
    """搜索全部命中用户并原子写入指定导出文件。"""

    if field not in SEARCH_FIELD_NAMES:
        raise ValueError(f"不支持的搜索字段: {field}")

    target = Path(output_path).expanduser().resolve()
    selected_format = _resolve_export_format(target, output_format)
    if target.exists() and not overwrite:
        raise FileExistsError(f"导出文件已存在: {target}")

    engine, Session = init_db(db_path, create=False)
    try:
        with Session() as session:
            results = search(keyword, session, field=field)
    finally:
        engine.dispose()

    rows = _flatten_results(results)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        if selected_format == "json":
            _write_json(temporary, keyword.strip(), field, results)
        elif selected_format == "csv":
            _write_csv(temporary, rows)
        else:
            _write_xlsx(temporary, rows)
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()

    return {
        "output_path": str(target),
        "format": selected_format,
        "users": len(results),
        "relations": len(rows),
        "file_size_bytes": target.stat().st_size,
    }


def format_export_result(result: dict) -> str:
    """返回适合 CLI 的导出摘要。"""

    return (
        f"已导出 {result['users']} 个用户、"
        f"{result['relations']} 条成员-群关系到 "
        f"{result['output_path']} "
        f"({result['format']}, {result['file_size_bytes']} 字节)"
    )
