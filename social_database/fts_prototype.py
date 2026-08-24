"""在隔离的临时数据库中验证 FTS5 trigram 搜索方案。"""

import argparse
import platform
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter_ns
from uuid import uuid4

from .config import DB_PATH
from .migrations import CURRENT_SCHEMA_VERSION
from .output import format_json, safe_print
from .search import SEARCH_FIELD_NAMES, _escape_like
from .search_index import SEARCH_INDEX_COLUMNS, fts_match_expression

LIKE_COLUMNS = {
    "user_id": "relation.user_id",
    "group_id": "relation.group_id",
    "group_name": "group_table.group_name",
    "nickname": "relation.nickname",
    "card": "relation.card",
    "title": "relation.title",
    "join_time": "relation.join_time",
    "last_sent_time": "relation.last_sent_time",
}


class FTS5UnavailableError(RuntimeError):
    """当前 SQLite 运行时无法创建 FTS5 表。"""


@dataclass(frozen=True)
class PrototypeScenario:
    """仅在内存中保存的对照查询；关键字不会写入报告。"""

    name: str
    field: str
    keyword: str


def _validate_options(warmups: int, iterations: int) -> None:
    if warmups < 0:
        raise ValueError("预热次数不能小于 0")
    if iterations < 1:
        raise ValueError("计时次数必须大于 0")


def _open_read_only_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _database_stats(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "groups": int(connection.execute("SELECT count(*) FROM groups").fetchone()[0]),
        "members": int(
            connection.execute("SELECT count(*) FROM members").fetchone()[0]
        ),
        "relations": int(
            connection.execute(
                "SELECT count(*) FROM member_group_info"
            ).fetchone()[0]
        ),
    }


def _create_fts_table(connection: sqlite3.Connection) -> None:
    try:
        connection.execute(
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
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "fts5" in message or "trigram" in message:
            raise FTS5UnavailableError(
                f"当前 SQLite 不支持 FTS5 trigram: {exc}"
            ) from exc
        raise


def rebuild_fts_index(
    source: sqlite3.Connection,
    scratch: sqlite3.Connection,
    *,
    batch_size: int = 2000,
) -> dict[str, float | int]:
    """从只读源库流式重建临时 FTS5 索引，并执行完整性检查。"""

    if batch_size < 1:
        raise ValueError("批量大小必须大于 0")

    start_ns = perf_counter_ns()
    scratch.execute("DROP TABLE IF EXISTS member_search")
    _create_fts_table(scratch)

    source_cursor = source.execute(
        """
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
        ORDER BY relation.user_id, relation.group_id
        """
    )
    insert_sql = (
        "INSERT INTO member_search("
        + ", ".join(SEARCH_INDEX_COLUMNS)
        + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )

    indexed_rows = 0
    while rows := source_cursor.fetchmany(batch_size):
        scratch.executemany(insert_sql, (tuple(row) for row in rows))
        indexed_rows += len(rows)

    scratch.commit()
    actual_rows = int(
        scratch.execute("SELECT count(*) FROM member_search").fetchone()[0]
    )
    if actual_rows != indexed_rows:
        raise RuntimeError(
            f"FTS5 索引行数不一致: 写入 {indexed_rows}，读取 {actual_rows}"
        )

    scratch.execute(
        "INSERT INTO member_search(member_search) VALUES('integrity-check')"
    )
    scratch.commit()
    return {
        "indexed_rows": indexed_rows,
        "build_ms": round((perf_counter_ns() - start_ns) / 1_000_000, 3),
    }


def _selected_like_columns(field: str) -> tuple[str, ...]:
    if field not in SEARCH_FIELD_NAMES:
        raise ValueError(f"不支持的搜索字段: {field}")
    if field == "any":
        return tuple(LIKE_COLUMNS.values())
    return (LIKE_COLUMNS[field],)


def _like_user_ids(
    connection: sqlite3.Connection,
    keyword: str,
    field: str,
) -> set[str]:
    columns = _selected_like_columns(field)
    conditions = " OR ".join(
        f"{column} LIKE ? ESCAPE '\\'" for column in columns
    )
    pattern = f"%{_escape_like(keyword)}%"
    rows = connection.execute(
        f"""
        SELECT DISTINCT relation.user_id
        FROM member_group_info AS relation
        JOIN groups AS group_table
          ON group_table.group_id = relation.group_id
        WHERE {conditions}
        """,
        [pattern] * len(columns),
    )
    return {str(row[0]) for row in rows}


def _fts_user_ids(
    connection: sqlite3.Connection,
    keyword: str,
    field: str,
) -> set[str]:
    if field not in SEARCH_FIELD_NAMES:
        raise ValueError(f"不支持的搜索字段: {field}")
    if len(keyword) < 3:
        raise ValueError("FTS5 trigram 查询至少需要 3 个字符")

    expression = fts_match_expression(keyword, field)
    columns = SEARCH_INDEX_COLUMNS if field == "any" else (field,)
    conditions = " OR ".join(
        f"{column} LIKE ? ESCAPE '\\'" for column in columns
    )
    pattern = f"%{_escape_like(keyword)}%"
    rows = connection.execute(
        f"""
        SELECT DISTINCT user_id
        FROM member_search
        WHERE member_search MATCH ?
          AND ({conditions})
        """,
        (expression, *([pattern] * len(columns))),
    )
    return {str(row[0]) for row in rows}


def _nearest_rank_percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _measure_query(query, *, warmups: int, iterations: int) -> tuple[set[str], dict]:
    initial_ms = None
    result: set[str] | None = None
    for index in range(warmups):
        start_ns = perf_counter_ns()
        result = query()
        if index == 0:
            initial_ms = (perf_counter_ns() - start_ns) / 1_000_000

    durations = []
    for _ in range(iterations):
        start_ns = perf_counter_ns()
        result = query()
        durations.append((perf_counter_ns() - start_ns) / 1_000_000)

    if result is None:
        raise RuntimeError("原型未执行任何查询")
    return result, {
        "initial_ms": round(initial_ms, 3) if initial_ms is not None else None,
        "min_ms": round(min(durations), 3),
        "median_ms": round(median(durations), 3),
        "p95_ms": round(_nearest_rank_percentile(durations, 0.95), 3),
        "max_ms": round(max(durations), 3),
    }


def _first_value(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple = (),
):
    row = connection.execute(sql, parameters).fetchone()
    return row[0] if row is not None else None


def select_prototype_scenarios(
    connection: sqlite3.Connection,
) -> list[PrototypeScenario]:
    """根据源库分布选择隐私安全的默认对照场景。"""

    scenarios: list[PrototypeScenario] = []
    user_id = _first_value(
        connection,
        """
        SELECT user_id
        FROM members
        ORDER BY length(user_id) DESC, user_id
        LIMIT 1
        """,
    )
    if user_id:
        scenarios.append(
            PrototypeScenario("user_id_selective", "user_id", str(user_id))
        )

    group_id = _first_value(
        connection,
        """
        SELECT group_id
        FROM member_group_info
        GROUP BY group_id
        ORDER BY count(*) DESC, group_id
        LIMIT 1
        """,
    )
    if group_id:
        scenarios.append(
            PrototypeScenario("group_id_broad", "group_id", str(group_id))
        )

    group_name = _first_value(
        connection,
        """
        SELECT group_table.group_name
        FROM groups AS group_table
        JOIN member_group_info AS relation
          ON relation.group_id = group_table.group_id
        WHERE group_table.group_name IS NOT NULL
          AND group_table.group_name != ''
        GROUP BY group_table.group_id, group_table.group_name
        ORDER BY count(*) DESC, group_table.group_id
        LIMIT 1
        """,
    )
    if group_name:
        scenarios.append(
            PrototypeScenario(
                "group_name_broad", "group_name", str(group_name)
            )
        )

    nickname_count = int(
        _first_value(
            connection,
            """
            SELECT count(*)
            FROM member_group_info
            WHERE nickname IS NOT NULL AND nickname != ''
            """,
        )
        or 0
    )
    typical_nickname = None
    longest_nickname = None
    if nickname_count:
        median_offset = (nickname_count - 1) // 2
        typical_length = _first_value(
            connection,
            f"""
            SELECT length(nickname)
            FROM member_group_info
            WHERE nickname IS NOT NULL AND nickname != ''
            ORDER BY length(nickname), nickname
            LIMIT 1 OFFSET {median_offset}
            """,
        )
        typical_nickname = _first_value(
            connection,
            """
            SELECT nickname
            FROM member_group_info
            WHERE nickname IS NOT NULL
              AND nickname != ''
              AND length(nickname) = ?
            GROUP BY nickname
            ORDER BY count(*), nickname
            LIMIT 1
            """,
            (typical_length,),
        )
        longest_nickname = _first_value(
            connection,
            """
            SELECT nickname
            FROM member_group_info
            WHERE nickname IS NOT NULL AND nickname != ''
            ORDER BY length(nickname) DESC, nickname
            LIMIT 1
            """,
        )

    if typical_nickname:
        typical_nickname = str(typical_nickname)
        scenarios.extend(
            [
                PrototypeScenario(
                    "nickname_typical_selective",
                    "nickname",
                    typical_nickname,
                ),
                PrototypeScenario(
                    "any_typical_selective", "any", typical_nickname
                ),
            ]
        )
        if len(typical_nickname) >= 2:
            scenarios.append(
                PrototypeScenario(
                    "nickname_short_fallback",
                    "nickname",
                    typical_nickname[:2],
                )
            )

    if longest_nickname and str(longest_nickname) != typical_nickname:
        longest_nickname = str(longest_nickname)
        scenarios.extend(
            [
                PrototypeScenario(
                    "nickname_long_selective",
                    "nickname",
                    longest_nickname,
                ),
                PrototypeScenario(
                    "any_long_selective", "any", longest_nickname
                ),
            ]
        )

    random_bits = uuid4().int
    missing_keyword = "".join(
        chr(0xE000 + ((random_bits >> (index * 12)) & 0xFFF))
        for index in range(4)
    )
    scenarios.append(
        PrototypeScenario("any_miss_typical", "any", missing_keyword)
    )
    return scenarios


def _measure_scenario(
    scenario: PrototypeScenario,
    source: sqlite3.Connection,
    scratch: sqlite3.Connection,
    *,
    warmups: int,
    iterations: int,
) -> dict:
    like_ids, like_timing = _measure_query(
        lambda: _like_user_ids(source, scenario.keyword, scenario.field),
        warmups=warmups,
        iterations=iterations,
    )
    result = {
        "name": scenario.name,
        "field": scenario.field,
        "keyword_length": len(scenario.keyword),
        "matched_users": len(like_ids),
        "like": like_timing,
    }

    if len(scenario.keyword) < 3:
        return {
            **result,
            "backend": "like_fallback",
            "fts": None,
            "result_sets_equal": None,
            "speedup_median": None,
        }

    fts_ids, fts_timing = _measure_query(
        lambda: _fts_user_ids(scratch, scenario.keyword, scenario.field),
        warmups=warmups,
        iterations=iterations,
    )
    speedup = (
        like_timing["median_ms"] / fts_timing["median_ms"]
        if fts_timing["median_ms"] > 0
        else None
    )
    return {
        **result,
        "backend": "fts5_candidate",
        "fts": fts_timing,
        "fts_matched_users": len(fts_ids),
        "result_sets_equal": like_ids == fts_ids,
        "speedup_median": round(speedup, 2) if speedup is not None else None,
    }


def run_fts_prototype(
    db_path: str | Path = DB_PATH,
    *,
    warmups: int = 1,
    iterations: int = 5,
    scenarios: list[PrototypeScenario] | None = None,
) -> dict:
    """构建一次性 FTS5 索引并与现有 LIKE 语义逐场景对照。"""

    _validate_options(warmups, iterations)
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"数据库不存在: {path}")

    source = _open_read_only_database(path)
    try:
        schema_version = _schema_version(source)
        if schema_version != CURRENT_SCHEMA_VERSION:
            raise ValueError(
                "原型要求当前 schema 版本 "
                f"{CURRENT_SCHEMA_VERSION}，实际为 {schema_version}；"
                "请先运行 check"
            )
        database_stats = _database_stats(source)
        selected_scenarios = (
            list(scenarios)
            if scenarios is not None
            else select_prototype_scenarios(source)
        )

        with TemporaryDirectory(prefix="social-database-fts-") as directory:
            scratch_path = Path(directory) / "member-search.db"
            scratch = sqlite3.connect(scratch_path)
            try:
                build = rebuild_fts_index(source, scratch)
                results = [
                    _measure_scenario(
                        scenario,
                        source,
                        scratch,
                        warmups=warmups,
                        iterations=iterations,
                    )
                    for scenario in selected_scenarios
                ]
                scratch_file_size = scratch_path.stat().st_size
            except FTS5UnavailableError as exc:
                return {
                    "prototype_version": 1,
                    "measured_at_utc": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                    "runtime": {
                        "python": platform.python_version(),
                        "implementation": platform.python_implementation(),
                        "sqlite": sqlite3.sqlite_version,
                        "platform": platform.system(),
                        "fts5_available": False,
                    },
                    "database": {
                        "file_size_bytes": path.stat().st_size,
                        "schema_version": schema_version,
                        **database_stats,
                    },
                    "configuration": {
                        "warmups": warmups,
                        "iterations": iterations,
                        "minimum_fts_keyword_length": 3,
                    },
                    "privacy": {
                        "keywords_included": False,
                        "member_details_included": False,
                    },
                    "temporary_index": {
                        "retained": False,
                        "supported": False,
                    },
                    "scenarios": [],
                    "all_eligible_results_equal": None,
                    "recommendation": "keep_like",
                    "reason": str(exc),
                }
            finally:
                scratch.close()

        eligible = [
            item for item in results if item["backend"] == "fts5_candidate"
        ]
        all_equal = bool(eligible) and all(
            item["result_sets_equal"] for item in eligible
        )
        all_faster = bool(eligible) and all(
            item["fts"]["median_ms"] < item["like"]["median_ms"]
            for item in eligible
        )
        faster_queries = sum(
            item["fts"]["median_ms"] < item["like"]["median_ms"]
            for item in eligible
        )
        return {
            "prototype_version": 1,
            "measured_at_utc": datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            "runtime": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "sqlite": sqlite3.sqlite_version,
                "platform": platform.system(),
                "fts5_available": True,
            },
            "database": {
                "file_size_bytes": path.stat().st_size,
                "schema_version": schema_version,
                **database_stats,
            },
            "configuration": {
                "warmups": warmups,
                "iterations": iterations,
                "minimum_fts_keyword_length": 3,
            },
            "privacy": {
                "keywords_included": False,
                "member_details_included": False,
            },
            "temporary_index": {
                "retained": False,
                "supported": True,
                **build,
                "file_size_bytes": scratch_file_size,
            },
            "scenarios": results,
            "all_eligible_results_equal": all_equal,
            "all_eligible_queries_faster": all_faster,
            "eligible_queries_faster": faster_queries,
            "eligible_queries_measured": len(eligible),
            "recommendation": (
                "candidate_for_hybrid_integration"
                if all_equal and faster_queries
                else "keep_like"
            ),
        }
    finally:
        source.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="在临时数据库中运行 FTS5/LIKE 对照原型"
    )
    parser.add_argument("--db", default=DB_PATH, help="SQLite 数据库路径")
    parser.add_argument("--warmups", type=int, default=1, help="预热次数")
    parser.add_argument("--iterations", type=int, default=5, help="计时次数")
    args = parser.parse_args(argv)

    try:
        report = run_fts_prototype(
            args.db,
            warmups=args.warmups,
            iterations=args.iterations,
        )
    except (FileNotFoundError, RuntimeError, sqlite3.Error, ValueError) as exc:
        safe_print(f"错误: {exc}", file=sys.stderr)
        return 1

    safe_print(format_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
