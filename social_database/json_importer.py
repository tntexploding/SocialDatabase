"""版本化 JSON 批次解析与导入。"""

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from .config import DB_PATH, SOURCE_COLUMNS
from .importer import (
    ImportSource,
    ImportStats,
    ParsedRecords,
    import_parsed_records,
    normalize_observed_at_utc,
    normalize_source_value,
)
from .output import safe_print

JSON_SOURCE_FORMAT_VERSION = 1
MAX_EXTERNAL_BATCH_ID_LENGTH = 128


def _validate_json_path(filepath: str | Path) -> Path:
    path = Path(filepath)
    if not path.is_file():
        raise FileNotFoundError(f"JSON 文件不存在: {path}")
    if path.suffix.lower() != ".json":
        raise ValueError(f"仅支持 .json 文件: {path}")
    return path


def _required_text(payload: dict, name: str) -> str:
    raw_value = payload.get(name)
    if not isinstance(raw_value, str):
        raise ValueError(f"JSON 批次缺少有效的 {name}")
    value = raw_value.strip()
    if not value:
        raise ValueError(f"JSON 批次缺少有效的 {name}")
    return value


def decode_json_bytes(content: bytes) -> Any:
    """解码 UTF-8 JSON 请求或文件内容。"""

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("JSON 批次必须使用 UTF-8 编码") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("JSON 批次不是有效的 JSON") from exc


def calculate_json_payload_hash(payload: Any) -> str:
    """计算与空白和对象键顺序无关的 JSON SHA-256。"""

    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("JSON 批次包含不可序列化的值") from exc
    return sha256(canonical).hexdigest()


def parse_json_payload(
    payload: Any,
    *,
    default_source_name: str = "json-batch",
    source_hash: str | None = None,
    max_records: int | None = None,
) -> tuple[ParsedRecords, ImportSource]:
    """把已解码的 JSON v1 对象转换为通用来源记录。"""

    if not isinstance(payload, dict):
        raise ValueError("JSON 批次顶层必须是对象")
    version = payload.get("schema_version")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != JSON_SOURCE_FORMAT_VERSION
    ):
        raise ValueError(
            "不支持的 JSON 批次版本: "
            f"{version!r}；当前仅支持 {JSON_SOURCE_FORMAT_VERSION}"
        )

    producer = _required_text(payload, "producer")
    external_batch_id = None
    if "batch_id" in payload:
        external_batch_id = _required_text(payload, "batch_id")
        if len(external_batch_id) > MAX_EXTERNAL_BATCH_ID_LENGTH:
            raise ValueError(
                "JSON 批次的 batch_id 不能超过 "
                f"{MAX_EXTERNAL_BATCH_ID_LENGTH} 个字符"
            )

    if "observed_at_utc" not in payload:
        raise ValueError("JSON 批次缺少 observed_at_utc")
    if not isinstance(payload["observed_at_utc"], str):
        raise ValueError("JSON 批次的 observed_at_utc 必须是字符串")
    observed_at = normalize_observed_at_utc(payload["observed_at_utc"])
    if observed_at is None:
        raise ValueError("JSON 批次缺少有效的 observed_at_utc")

    if "source_name" in payload and not isinstance(payload["source_name"], str):
        raise ValueError("JSON 批次的 source_name 必须是字符串")
    source_name = (
        normalize_source_value(payload.get("source_name"))
        or default_source_name
    )
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        raise ValueError("JSON 批次的 records 必须是数组")
    if max_records is not None:
        if max_records < 1:
            raise ValueError("最大记录数必须大于 0")
        if len(raw_records) > max_records:
            raise ValueError(
                f"JSON 批次包含 {len(raw_records)} 条记录，"
                f"超过限制 {max_records}"
            )

    rows = []
    skipped_rows = 0
    missing_user_id_rows = 0
    missing_group_id_rows = 0
    for index, raw_record in enumerate(raw_records, start=1):
        if not isinstance(raw_record, dict):
            raise ValueError(f"JSON records 第 {index} 项必须是对象")
        record = {
            column: normalize_source_value(raw_record.get(column))
            for column in SOURCE_COLUMNS
        }
        missing_user_id = not record["user_id"]
        missing_group_id = not record["group_id"]
        if missing_user_id:
            missing_user_id_rows += 1
        if missing_group_id:
            missing_group_id_rows += 1
        if missing_user_id or missing_group_id:
            skipped_rows += 1
            continue
        rows.append(record)

    parsed = ParsedRecords(
        rows=tuple(rows),
        source_rows=len(raw_records),
        skipped_rows=skipped_rows,
        missing_user_id_rows=missing_user_id_rows,
        missing_group_id_rows=missing_group_id_rows,
    )
    source = ImportSource(
        source_type="json",
        source_name=source_name,
        source_hash=source_hash or calculate_json_payload_hash(payload),
        source_format_version=version,
        producer=producer,
        external_batch_id=external_batch_id,
        observed_at_utc=observed_at,
    )
    return parsed, source


def parse_json_with_stats(
    filepath: str | Path,
) -> tuple[ParsedRecords, ImportSource]:
    """读取标准 JSON v1 文件，并转换为通用来源记录。"""

    path = _validate_json_path(filepath)
    content = path.read_bytes()
    payload = decode_json_bytes(content)
    source_hash = (
        calculate_json_payload_hash(payload)
        if isinstance(payload, dict) and "batch_id" in payload
        else sha256(content).hexdigest()
    )
    return parse_json_payload(
        payload,
        default_source_name=path.name,
        source_hash=source_hash,
    )


def import_json_payload(
    payload: Any,
    db_path: str | Path = DB_PATH,
    *,
    default_source_name: str = "http-json",
    max_records: int | None = None,
) -> ImportStats:
    """导入内存中的 JSON v1 对象，供 HTTP 等适配器复用。"""

    parsed, source = parse_json_payload(
        payload,
        default_source_name=default_source_name,
        max_records=max_records,
    )
    return import_parsed_records(parsed, source, db_path)


def import_json(
    filepath: str | Path,
    db_path: str | Path = DB_PATH,
    *,
    force: bool = False,
) -> ImportStats:
    """导入标准 JSON v1 文件。"""

    path = _validate_json_path(filepath)
    safe_print(f"正在解析: {path}")
    parsed, source = parse_json_with_stats(path)
    safe_print(
        f"读取 {parsed.source_rows} 条记录，"
        f"有效 {len(parsed.rows)} 条，"
        f"跳过 {parsed.skipped_rows} 条"
    )
    return import_parsed_records(parsed, source, db_path, force=force)
