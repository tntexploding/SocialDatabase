"""Filesystem-backed pending queue for lossless plugin retries."""

import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

QUEUE_VERSION = 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("队列时间必须包含时区")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class QueueItem:
    queue_id: str
    payload: dict[str, Any]
    created_at_utc: str
    attempts: int
    next_attempt_at_utc: str
    last_error: str | None = None
    queue_version: int = QUEUE_VERSION

    def is_due(self, now: datetime) -> bool:
        return _parse_utc(self.next_attempt_at_utc) <= now


class QueueStore:
    """Persist pending and rejected batches under AstrBot plugin data."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.pending_dir = self.root / "pending"
        self.rejected_dir = self.root / "rejected"
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self.rejected_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _atomic_write(path: Path, item: QueueItem) -> None:
        temporary = path.with_name(f".{path.stem}-{uuid4().hex}.tmp")
        content = json.dumps(
            asdict(item),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _load(path: Path) -> QueueItem:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("队列文件顶层必须是对象")
        item = QueueItem(**raw)
        if item.queue_version != QUEUE_VERSION:
            raise ValueError(f"不支持的队列版本: {item.queue_version}")
        if item.queue_id != path.stem:
            raise ValueError("队列文件名与 queue_id 不一致")
        if not isinstance(item.payload, dict):
            raise ValueError("队列 payload 必须是对象")
        if item.attempts < 0:
            raise ValueError("队列 attempts 不能为负数")
        _parse_utc(item.created_at_utc)
        _parse_utc(item.next_attempt_at_utc)
        return item

    def enqueue(self, payload: dict[str, Any]) -> QueueItem:
        """Atomically persist a new immutable payload before upload."""

        now = _utc_now()
        item = QueueItem(
            queue_id=uuid4().hex,
            payload=payload,
            created_at_utc=_utc_text(now),
            attempts=0,
            next_attempt_at_utc=_utc_text(now),
        )
        self._atomic_write(self.pending_dir / f"{item.queue_id}.json", item)
        return item

    def _quarantine_corrupt(self, path: Path) -> None:
        target = self.rejected_dir / (
            f"{path.stem}-corrupt-{uuid4().hex[:8]}.json"
        )
        os.replace(path, target)

    def pending_items(
        self,
        *,
        now: datetime | None = None,
        limit: int | None = None,
        include_deferred: bool = False,
    ) -> list[QueueItem]:
        """Return due items in stable order and quarantine unreadable entries."""

        due_at = (now or _utc_now()).astimezone(timezone.utc)
        result: list[QueueItem] = []
        for path in sorted(self.pending_dir.glob("*.json")):
            try:
                item = self._load(path)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                self._quarantine_corrupt(path)
                continue
            if include_deferred or item.is_due(due_at):
                result.append(item)
                if limit is not None and len(result) >= limit:
                    break
        return result

    def acknowledge(self, item: QueueItem) -> None:
        (self.pending_dir / f"{item.queue_id}.json").unlink(missing_ok=True)

    def retry(
        self,
        item: QueueItem,
        error: str,
        *,
        delay_seconds: int,
    ) -> QueueItem:
        updated = replace(
            item,
            attempts=item.attempts + 1,
            last_error=error[:1000],
            next_attempt_at_utc=_utc_text(
                _utc_now() + timedelta(seconds=delay_seconds)
            ),
        )
        self._atomic_write(
            self.pending_dir / f"{item.queue_id}.json",
            updated,
        )
        return updated

    def reject(self, item: QueueItem, error: str) -> QueueItem:
        updated = replace(
            item,
            attempts=item.attempts + 1,
            last_error=error[:1000],
            next_attempt_at_utc=_utc_text(_utc_now()),
        )
        target = self.rejected_dir / f"{item.queue_id}.json"
        self._atomic_write(target, updated)
        self.acknowledge(item)
        return updated

    def counts(self) -> dict[str, int]:
        return {
            "pending": sum(1 for _ in self.pending_dir.glob("*.json")),
            "rejected": sum(1 for _ in self.rejected_dir.glob("*.json")),
        }
