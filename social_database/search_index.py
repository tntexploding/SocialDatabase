"""FTS5 搜索索引的 schema、重建、状态与健康检查。"""

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import zip_longest
from time import perf_counter_ns

from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

SEARCH_INDEX_TABLE = "member_search"
SEARCH_INDEX_FORMAT_VERSION = 1
SEARCH_INDEX_MIN_KEYWORD_LENGTH = 3
SEARCH_INDEX_COLUMNS = (
    "user_id",
    "group_id",
    "group_name",
    "nickname",
    "card",
    "title",
    "join_time",
    "last_sent_time",
)
SEARCH_INDEX_FIELDS = ("any", *SEARCH_INDEX_COLUMNS)
FTS_ROUTED_FIELDS = frozenset(
    field for field in SEARCH_INDEX_FIELDS if field != "group_id"
)

_STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS search_index_state (
    id INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
    format_version INTEGER NOT NULL,
    status VARCHAR NOT NULL,
    indexed_relations INTEGER NOT NULL,
    updated_at_utc VARCHAR NOT NULL
)
"""

_SOURCE_ROWS_SQL = """
SELECT
    relation.user_id,
    relation.group_id,
    group_table.group_name,
    relation.nickname,
    relation.card,
    relation.title,
    relation.join_time,
    relation.last_sent_time
FROM member_group_info AS relation
JOIN groups AS group_table
  ON group_table.group_id = relation.group_id
"""


class SearchIndexBuildError(RuntimeError):
    """FTS5 索引无法完成一致重建。"""


@dataclass(frozen=True)
class SearchIndexResult:
    """一次索引重建的非敏感结果。"""

    status: str
    indexed_relations: int
    build_ms: float
    reason: str | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict:
        return {"ready": self.ready, **asdict(self)}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _elapsed_ms(start_ns: int) -> float:
    return round((perf_counter_ns() - start_ns) / 1_000_000, 3)


def ensure_search_index_state(connection: Connection) -> None:
    """创建单例索引状态表，但不要求当前运行时支持 FTS5。"""

    connection.exec_driver_sql(_STATE_TABLE_SQL)
    connection.exec_driver_sql(
        """
        INSERT OR IGNORE INTO search_index_state (
            id,
            format_version,
            status,
            indexed_relations,
            updated_at_utc
        ) VALUES (1, ?, 'stale', 0, ?)
        """,
        (SEARCH_INDEX_FORMAT_VERSION, _utc_now()),
    )


def _write_search_index_state(
    connection: Connection,
    *,
    status: str,
    indexed_relations: int,
) -> None:
    connection.exec_driver_sql(
        """
        INSERT INTO search_index_state (
            id,
            format_version,
            status,
            indexed_relations,
            updated_at_utc
        ) VALUES (1, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            format_version = excluded.format_version,
            status = excluded.status,
            indexed_relations = excluded.indexed_relations,
            updated_at_utc = excluded.updated_at_utc
        """,
        (
            SEARCH_INDEX_FORMAT_VERSION,
            status,
            indexed_relations,
            _utc_now(),
        ),
    )


def get_search_index_state(connection: Connection) -> dict | None:
    """读取索引状态和当前业务关系数；状态表缺失时返回 ``None``。"""

    try:
        row = connection.exec_driver_sql(
            """
            SELECT
                state.format_version,
                state.status,
                state.indexed_relations,
                state.updated_at_utc,
                (SELECT count(*) FROM member_group_info) AS expected_relations
            FROM search_index_state AS state
            WHERE state.id = 1
            """
        ).mappings().one_or_none()
    except (SQLAlchemyError, sqlite3.Error):
        return None

    if row is None:
        return None
    result = dict(row)
    result["ready"] = (
        result["format_version"] == SEARCH_INDEX_FORMAT_VERSION
        and result["status"] == "ready"
        and result["indexed_relations"] == result["expected_relations"]
    )
    return result


def is_search_index_ready(connection: Connection) -> bool:
    """返回索引元数据是否允许搜索走 FTS5。"""

    state = get_search_index_state(connection)
    return bool(state and state["ready"])


def _create_fts_table(connection: Connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE VIRTUAL TABLE member_search USING fts5(
            user_id,
            group_id,
            group_name,
            nickname,
            card,
            title,
            join_time,
            last_sent_time,
            tokenize='trigram'
        )
        """
    )


def _populate_search_index(connection: Connection) -> None:
    connection.exec_driver_sql(
        """
        INSERT INTO member_search (
            user_id,
            group_id,
            group_name,
            nickname,
            card,
            title,
            join_time,
            last_sent_time
        )
        """
        + _SOURCE_ROWS_SQL
    )


def _check_fts_integrity(connection: Connection) -> None:
    connection.exec_driver_sql(
        "INSERT INTO member_search(member_search) VALUES('integrity-check')"
    )


def _failure_status(error: BaseException) -> tuple[str, str]:
    message = str(error).lower()
    unavailable_markers = (
        "no such module: fts5",
        "no such tokenizer: trigram",
        "fts5 is not available",
    )
    if any(marker in message for marker in unavailable_markers):
        return "unavailable", "fts5_unavailable"
    return "stale", "rebuild_failed"


def rebuild_search_index(connection: Connection) -> SearchIndexResult:
    """在 savepoint 内全量重建 FTS5；失败时保留业务事务并标记回退。"""

    start_ns = perf_counter_ns()
    ensure_search_index_state(connection)
    try:
        with connection.begin_nested():
            connection.exec_driver_sql("DROP TABLE IF EXISTS member_search")
            _create_fts_table(connection)
            _populate_search_index(connection)
            indexed_relations = int(
                connection.exec_driver_sql(
                    "SELECT count(*) FROM member_search"
                ).scalar_one()
            )
            expected_relations = int(
                connection.exec_driver_sql(
                    "SELECT count(*) FROM member_group_info"
                ).scalar_one()
            )
            if indexed_relations != expected_relations:
                raise SearchIndexBuildError(
                    "搜索索引行数与成员关系数不一致"
                )
            _check_fts_integrity(connection)
            _write_search_index_state(
                connection,
                status="ready",
                indexed_relations=indexed_relations,
            )
        return SearchIndexResult(
            status="ready",
            indexed_relations=indexed_relations,
            build_ms=_elapsed_ms(start_ns),
        )
    except (
        SearchIndexBuildError,
        SQLAlchemyError,
        sqlite3.Error,
    ) as exc:
        status, reason = _failure_status(exc)
        _write_search_index_state(
            connection,
            status=status,
            indexed_relations=0,
        )
        return SearchIndexResult(
            status=status,
            indexed_relations=0,
            build_ms=_elapsed_ms(start_ns),
            reason=reason,
        )


def should_use_fts(keyword: str, field: str) -> bool:
    """应用已验证的混合路由：短词和群 ID 保持 LIKE。"""

    if field not in SEARCH_INDEX_FIELDS:
        raise ValueError(f"不支持的搜索字段: {field}")
    return (
        len(keyword) >= SEARCH_INDEX_MIN_KEYWORD_LENGTH
        and field in FTS_ROUTED_FIELDS
    )


def fts_phrase(keyword: str) -> str:
    """把用户文本编码为不含 FTS5 操作符语义的双引号短语。"""

    return '"' + keyword.replace('"', '""') + '"'


def fts_match_expression(keyword: str, field: str) -> str:
    """构造经过字段白名单和短语引用的 MATCH 参数。"""

    if field not in SEARCH_INDEX_FIELDS:
        raise ValueError(f"不支持的搜索字段: {field}")
    if len(keyword) < SEARCH_INDEX_MIN_KEYWORD_LENGTH:
        raise ValueError(
            "FTS5 trigram 查询至少需要 "
            f"{SEARCH_INDEX_MIN_KEYWORD_LENGTH} 个字符"
        )
    phrase = fts_phrase(keyword)
    return phrase if field == "any" else f"{field} : {phrase}"


def _fts5_runtime_available(connection: Connection) -> bool:
    """仅在临时 schema 中探测 FTS5 trigram，不改变业务数据库。"""

    try:
        with connection.begin_nested():
            connection.exec_driver_sql(
                "DROP TABLE IF EXISTS temp.__social_database_fts_probe"
            )
            connection.exec_driver_sql(
                """
                CREATE VIRTUAL TABLE temp.__social_database_fts_probe
                USING fts5(value, tokenize='trigram')
                """
            )
            connection.exec_driver_sql(
                "DROP TABLE temp.__social_database_fts_probe"
            )
        return True
    except (SQLAlchemyError, sqlite3.Error):
        return False


def _search_index_content_matches(connection: Connection) -> bool:
    expected = connection.exec_driver_sql(
        _SOURCE_ROWS_SQL + " ORDER BY relation.user_id, relation.group_id"
    )
    actual = connection.exec_driver_sql(
        """
        SELECT
            user_id,
            group_id,
            group_name,
            nickname,
            card,
            title,
            join_time,
            last_sent_time
        FROM member_search
        ORDER BY user_id, group_id
        """
    )
    sentinel = object()
    return all(
        expected_row is not sentinel
        and actual_row is not sentinel
        and tuple(expected_row) == tuple(actual_row)
        for expected_row, actual_row in zip_longest(
            expected,
            actual,
            fillvalue=sentinel,
        )
    )


def inspect_search_index(connection: Connection) -> dict:
    """验证运行时、元数据、FTS 内部结构以及与业务表的完整内容一致性。"""

    state = get_search_index_state(connection)
    runtime_available = _fts5_runtime_available(connection)
    base = {
        "runtime_available": runtime_available,
        "format_version": state["format_version"] if state else None,
        "expected_format_version": SEARCH_INDEX_FORMAT_VERSION,
        "status": state["status"] if state else "missing",
        "indexed_relations": state["indexed_relations"] if state else 0,
        "expected_relations": state["expected_relations"] if state else None,
    }
    if state is None:
        return {
            **base,
            "ready": False,
            "healthy": False,
            "degraded": True,
            "internal_integrity": None,
            "content_matches": None,
            "reason": "state_missing",
        }

    if not runtime_available:
        acceptable = (
            state["format_version"] == SEARCH_INDEX_FORMAT_VERSION
            and state["status"] in ("ready", "unavailable")
        )
        return {
            **base,
            "ready": False,
            "healthy": acceptable,
            "degraded": True,
            "internal_integrity": None,
            "content_matches": None,
            "reason": "fts5_runtime_unavailable",
        }

    if not state["ready"]:
        return {
            **base,
            "ready": False,
            "healthy": False,
            "degraded": True,
            "internal_integrity": None,
            "content_matches": None,
            "reason": "index_not_ready",
        }

    try:
        indexed_relations = int(
            connection.exec_driver_sql(
                "SELECT count(*) FROM member_search"
            ).scalar_one()
        )
        with connection.begin_nested():
            _check_fts_integrity(connection)
        content_matches = _search_index_content_matches(connection)
    except (SQLAlchemyError, sqlite3.Error):
        return {
            **base,
            "ready": False,
            "healthy": False,
            "degraded": True,
            "internal_integrity": False,
            "content_matches": None,
            "reason": "index_check_failed",
        }

    counts_match = (
        indexed_relations == state["indexed_relations"]
        and indexed_relations == state["expected_relations"]
    )
    healthy = counts_match and content_matches
    return {
        **base,
        "indexed_relations": indexed_relations,
        "ready": healthy,
        "healthy": healthy,
        "degraded": not healthy,
        "internal_integrity": True,
        "content_matches": content_matches,
        "reason": None if healthy else "index_content_mismatch",
    }
