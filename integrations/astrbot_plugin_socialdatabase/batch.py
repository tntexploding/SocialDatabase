"""Create SocialDatabase JSON v1 batches from OneBot group data."""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

MEMBER_FIELDS = (
    "nickname",
    "card",
    "sex",
    "age",
    "area",
    "level",
    "qq_level",
    "join_time",
    "last_sent_time",
    "title_expire_time",
    "unfriendly",
    "card_changeable",
    "is_robot",
    "shut_up_timestamp",
    "role",
    "title",
)


def _json_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


def _identifier(value: Any) -> str | None:
    scalar = _json_scalar(value)
    if scalar is None or isinstance(scalar, bool):
        return None
    text = str(scalar).strip()
    return text or None


def new_batch_id(group_id: str, observed_at: datetime) -> str:
    """Generate a compact ID which remains stable after the batch is queued."""

    utc = observed_at.astimezone(timezone.utc)
    timestamp = utc.strftime("%Y%m%dT%H%M%SZ")
    safe_group = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in group_id
    )[:72]
    return f"aiocqhttp-{safe_group}-{timestamp}-{uuid4().hex[:12]}"


def create_group_batch(
    *,
    group_id: Any,
    group_name: Any,
    members: Sequence[Mapping[str, Any]],
    producer: str,
    observed_at: datetime | None = None,
    batch_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Map one OneBot group snapshot to one durable JSON v1 payload."""

    normalized_group_id = _identifier(group_id)
    if normalized_group_id is None:
        raise ValueError("群组缺少有效的 group_id")
    collected_at = observed_at or datetime.now(timezone.utc)
    if collected_at.tzinfo is None:
        raise ValueError("observed_at 必须包含时区")
    observed_utc = collected_at.astimezone(timezone.utc)

    normalized_group_name = _json_scalar(group_name)
    records: list[dict[str, Any]] = []
    skipped = 0
    for member in members:
        user_id = _identifier(member.get("user_id"))
        if user_id is None:
            skipped += 1
            continue
        record: dict[str, Any] = {
            "group_id": normalized_group_id,
            "user_id": user_id,
        }
        if normalized_group_name is not None:
            record["group_name"] = normalized_group_name
        for field in MEMBER_FIELDS:
            value = _json_scalar(member.get(field))
            if value is not None:
                record[field] = value
        records.append(record)

    resolved_batch_id = (batch_id or "").strip() or new_batch_id(
        normalized_group_id,
        observed_utc,
    )
    if len(resolved_batch_id) > 128:
        raise ValueError("batch_id 不能超过 128 个字符")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "producer": producer,
        "batch_id": resolved_batch_id,
        "source_name": f"aiocqhttp-group-{normalized_group_id}",
        "observed_at_utc": observed_utc.isoformat().replace("+00:00", "Z"),
        "records": records,
    }
    return payload, skipped
