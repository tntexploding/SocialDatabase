"""隐私安全、只读的搜索性能基准。"""

import argparse
import platform
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from statistics import median
from time import perf_counter_ns
from uuid import uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as SessionType, sessionmaker
from sqlalchemy.pool import NullPool

from .config import DB_PATH
from .migrations import CURRENT_SCHEMA_VERSION
from .models import Group, Member, MemberGroupInfo
from .output import format_json, safe_print
from .search import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, search_page


@dataclass(frozen=True)
class BenchmarkScenario:
    """仅在内存中保存的基准查询；关键字不会进入报告。"""

    name: str
    field: str
    keyword: str


def _validate_options(warmups: int, iterations: int, page_size: int) -> None:
    if warmups < 0:
        raise ValueError("预热次数不能小于 0")
    if iterations < 1:
        raise ValueError("计时次数必须大于 0")
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ValueError(f"每页数量必须在 1 到 {MAX_PAGE_SIZE} 之间")


def _open_read_only_database(path: Path):
    uri = path.as_uri() + "?mode=ro"

    def creator():
        return sqlite3.connect(uri, uri=True)

    engine = create_engine(
        "sqlite+pysqlite://",
        creator=creator,
        poolclass=NullPool,
    )
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, Session


def _select_scenarios(session: SessionType) -> list[BenchmarkScenario]:
    scenarios: list[BenchmarkScenario] = []

    user_id = session.scalar(
        select(Member.user_id)
        .order_by(func.length(Member.user_id).desc(), Member.user_id)
        .limit(1)
    )
    if user_id:
        scenarios.append(
            BenchmarkScenario("user_id_selective", "user_id", user_id)
        )

    group_id = session.scalar(
        select(MemberGroupInfo.group_id)
        .group_by(MemberGroupInfo.group_id)
        .order_by(
            func.count().desc(),
            MemberGroupInfo.group_id,
        )
        .limit(1)
    )
    if group_id:
        scenarios.append(
            BenchmarkScenario("group_id_broad", "group_id", group_id)
        )

    group_name = session.scalar(
        select(Group.group_name)
        .join(
            MemberGroupInfo,
            MemberGroupInfo.group_id == Group.group_id,
        )
        .where(Group.group_name.is_not(None), Group.group_name != "")
        .group_by(Group.group_id, Group.group_name)
        .order_by(func.count().desc(), Group.group_id)
        .limit(1)
    )
    if group_name:
        scenarios.append(
            BenchmarkScenario(
                "group_name_broad",
                "group_name",
                group_name,
            )
        )

    nickname_filter = (
        MemberGroupInfo.nickname.is_not(None),
        MemberGroupInfo.nickname != "",
    )
    nickname_count = session.scalar(
        select(func.count())
        .select_from(MemberGroupInfo)
        .where(*nickname_filter)
    ) or 0
    if nickname_count:
        median_offset = (nickname_count - 1) // 2
        typical_length = session.scalar(
            select(func.length(MemberGroupInfo.nickname))
            .where(*nickname_filter)
            .order_by(
                func.length(MemberGroupInfo.nickname),
                MemberGroupInfo.nickname,
            )
            .offset(median_offset)
            .limit(1)
        )
        typical_nickname = session.scalar(
            select(MemberGroupInfo.nickname)
            .where(
                *nickname_filter,
                func.length(MemberGroupInfo.nickname) == typical_length,
            )
            .group_by(MemberGroupInfo.nickname)
            .order_by(func.count(), MemberGroupInfo.nickname)
            .limit(1)
        )
        longest_nickname = session.scalar(
            select(MemberGroupInfo.nickname)
            .where(*nickname_filter)
            .order_by(
                func.length(MemberGroupInfo.nickname).desc(),
                MemberGroupInfo.nickname,
            )
            .limit(1)
        )
    else:
        typical_nickname = None
        longest_nickname = None

    if typical_nickname:
        scenarios.extend(
            [
                BenchmarkScenario(
                    "nickname_typical_selective",
                    "nickname",
                    typical_nickname,
                ),
                BenchmarkScenario(
                    "any_typical_selective",
                    "any",
                    typical_nickname,
                ),
            ]
        )

    if longest_nickname and longest_nickname != typical_nickname:
        scenarios.extend(
            [
                BenchmarkScenario(
                    "nickname_long_selective",
                    "nickname",
                    longest_nickname,
                ),
                BenchmarkScenario(
                    "any_long_selective",
                    "any",
                    longest_nickname,
                ),
            ]
        )

    random_bits = uuid4().int
    missing_keyword = "".join(
        chr(0xE000 + ((random_bits >> (index * 12)) & 0xFFF))
        for index in range(4)
    )
    scenarios.append(
        BenchmarkScenario(
            "any_miss_typical",
            "any",
            missing_keyword,
        )
    )
    return scenarios


def _nearest_rank_percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _elapsed_ms(start_ns: int) -> float:
    return (perf_counter_ns() - start_ns) / 1_000_000


def _measure_scenario(
    scenario: BenchmarkScenario,
    session: SessionType,
    *,
    warmups: int,
    iterations: int,
    page_size: int,
) -> dict:
    initial_ms = None
    result_page = None
    for index in range(warmups):
        start_ns = perf_counter_ns()
        result_page = search_page(
            scenario.keyword,
            session,
            field=scenario.field,
            page=1,
            page_size=page_size,
        )
        if index == 0:
            initial_ms = _elapsed_ms(start_ns)

    durations = []
    for _ in range(iterations):
        start_ns = perf_counter_ns()
        result_page = search_page(
            scenario.keyword,
            session,
            field=scenario.field,
            page=1,
            page_size=page_size,
        )
        durations.append(_elapsed_ms(start_ns))

    if result_page is None:
        raise RuntimeError("基准未执行任何查询")

    return {
        "name": scenario.name,
        "field": scenario.field,
        "keyword_length": len(scenario.keyword),
        "matched_users": result_page.total_users,
        "page_users": len(result_page.results),
        "backend": result_page.backend,
        "initial_ms": round(initial_ms, 3) if initial_ms is not None else None,
        "min_ms": round(min(durations), 3),
        "median_ms": round(median(durations), 3),
        "p95_ms": round(
            _nearest_rank_percentile(durations, 0.95),
            3,
        ),
        "max_ms": round(max(durations), 3),
    }


def benchmark_search(
    db_path: str | Path = DB_PATH,
    *,
    warmups: int = 1,
    iterations: int = 5,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict:
    """在只读连接上运行分页搜索基准，且不返回查询关键字。"""

    _validate_options(warmups, iterations, page_size)
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"数据库不存在: {path}")

    engine, Session = _open_read_only_database(path)
    try:
        with engine.connect() as connection:
            schema_version = int(
                connection.exec_driver_sql("PRAGMA user_version").scalar_one()
            )
            if schema_version != CURRENT_SCHEMA_VERSION:
                raise ValueError(
                    "基准要求当前 schema 版本 "
                    f"{CURRENT_SCHEMA_VERSION}，实际为 {schema_version}；"
                    "请先运行 check"
                )
            compile_options = set(
                connection.exec_driver_sql("PRAGMA compile_options").scalars()
            )

        with Session() as session:
            database_stats = {
                "groups": session.scalar(
                    select(func.count()).select_from(Group)
                ),
                "members": session.scalar(
                    select(func.count()).select_from(Member)
                ),
                "relations": session.scalar(
                    select(func.count()).select_from(MemberGroupInfo)
                ),
            }
            scenarios = _select_scenarios(session)
            results = [
                _measure_scenario(
                    scenario,
                    session,
                    warmups=warmups,
                    iterations=iterations,
                    page_size=page_size,
                )
                for scenario in scenarios
            ]

        return {
            "benchmark_version": 1,
            "measured_at_utc": datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            "runtime": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "sqlite": sqlite3.sqlite_version,
                "platform": platform.system(),
                "fts5_enabled": "ENABLE_FTS5" in compile_options,
            },
            "database": {
                "file_size_bytes": path.stat().st_size,
                "schema_version": schema_version,
                **database_stats,
            },
            "configuration": {
                "warmups": warmups,
                "iterations": iterations,
                "page_size": page_size,
            },
            "privacy": {
                "keywords_included": False,
                "member_details_included": False,
            },
            "scenarios": results,
        }
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行只读搜索性能基准")
    parser.add_argument("--db", default=DB_PATH, help="SQLite 数据库路径")
    parser.add_argument("--warmups", type=int, default=1, help="预热次数")
    parser.add_argument("--iterations", type=int, default=5, help="计时次数")
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="每次加载的用户数",
    )
    args = parser.parse_args(argv)

    try:
        report = benchmark_search(
            args.db,
            warmups=args.warmups,
            iterations=args.iterations,
            page_size=args.page_size,
        )
    except (FileNotFoundError, RuntimeError, SQLAlchemyError, ValueError) as exc:
        safe_print(f"错误: {exc}", file=sys.stderr)
        return 1

    safe_print(format_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
