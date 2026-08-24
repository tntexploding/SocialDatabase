"""数据库搜索、分页与结果格式化。"""

import json
from dataclasses import dataclass
from math import ceil
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as SessionType, joinedload

from .config import (
    DB_PATH,
    SEARCH_OUTPUT_FORMAT,
    SEARCH_TEXT_SEPARATOR,
    SEARCH_UNKNOWN_VALUE,
)
from .models import Group, MemberGroupInfo, init_db

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 1000
SEARCH_FIELD_NAMES = (
    "any",
    "user_id",
    "group_id",
    "group_name",
    "nickname",
    "card",
    "title",
    "join_time",
    "last_sent_time",
)


@dataclass(frozen=True)
class SearchPage:
    """按用户分页的搜索结果。"""

    keyword: str
    field: str
    page: int
    page_size: int
    total_users: int
    results: list[dict]

    @property
    def total_pages(self) -> int:
        return ceil(self.total_users / self.page_size) if self.total_users else 0

    def to_dict(self) -> dict:
        return {
            "keyword": self.keyword,
            "field": self.field,
            "page": self.page,
            "page_size": self.page_size,
            "count": len(self.results),
            "total_users": self.total_users,
            "total_pages": self.total_pages,
            "results": self.results,
        }


def _escape_like(keyword: str) -> str:
    """转义 LIKE 通配符，使用户输入按普通文本匹配。"""

    return (
        keyword.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _search_columns() -> dict:
    return {
        "user_id": MemberGroupInfo.user_id,
        "group_id": MemberGroupInfo.group_id,
        "group_name": Group.group_name,
        "nickname": MemberGroupInfo.nickname,
        "card": MemberGroupInfo.card,
        "title": MemberGroupInfo.title,
        "join_time": MemberGroupInfo.join_time,
        "last_sent_time": MemberGroupInfo.last_sent_time,
    }


def _validate_search_options(field: str, page: int, page_size: int) -> None:
    if field not in SEARCH_FIELD_NAMES:
        raise ValueError(f"不支持的搜索字段: {field}")
    if page < 1:
        raise ValueError("页码必须大于 0")
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ValueError(
            f"每页数量必须在 1 到 {MAX_PAGE_SIZE} 之间"
        )


def _matched_users(keyword: str, field: str):
    like_pattern = f"%{_escape_like(keyword)}%"
    columns = _search_columns()
    selected_columns = (
        tuple(columns.values()) if field == "any" else (columns[field],)
    )
    conditions = [
        column.like(like_pattern, escape="\\")
        for column in selected_columns
    ]
    condition = conditions[0] if len(conditions) == 1 else or_(*conditions)
    return (
        select(MemberGroupInfo.user_id)
        .join(Group, MemberGroupInfo.group_id == Group.group_id)
        .where(condition)
        .distinct()
        .subquery()
    )


def _load_user_results(user_ids, session: SessionType) -> list[dict]:
    statement = (
        select(MemberGroupInfo)
        .options(joinedload(MemberGroupInfo.group))
        .join(
            user_ids,
            MemberGroupInfo.user_id == user_ids.c.user_id,
        )
        .order_by(MemberGroupInfo.user_id, MemberGroupInfo.group_id)
    )
    matches = session.scalars(statement).all()

    user_map: dict[str, dict] = {}
    for info in matches:
        user = user_map.setdefault(
            str(info.user_id),
            {"user_id": str(info.user_id), "groups": []},
        )
        user["groups"].append(
            {
                "group_id": info.group_id,
                "group_name": info.group.group_name,
                "nickname": info.nickname,
                "card": info.card,
                "join_time": info.join_time,
                "last_sent_time": info.last_sent_time,
                "title": info.title,
            }
        )

    return list(user_map.values())


def search(
    keyword: str,
    session: SessionType,
    field: str = "any",
) -> list[dict]:
    """兼容接口：返回全部命中用户及其完整群组资料。"""

    keyword = keyword.strip()
    _validate_search_options(field, page=1, page_size=DEFAULT_PAGE_SIZE)
    if not keyword:
        return []
    return _load_user_results(_matched_users(keyword, field), session)


def search_page(
    keyword: str,
    session: SessionType,
    *,
    field: str = "any",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> SearchPage:
    """按用户分页搜索，并返回总用户数。"""

    keyword = keyword.strip()
    _validate_search_options(field, page, page_size)
    if not keyword:
        return SearchPage(keyword, field, page, page_size, 0, [])

    matched_users = _matched_users(keyword, field)
    total_users = (
        session.scalar(select(func.count()).select_from(matched_users)) or 0
    )
    paged_users = (
        select(matched_users.c.user_id)
        .order_by(matched_users.c.user_id)
        .limit(page_size)
        .offset((page - 1) * page_size)
        .subquery()
    )
    results = _load_user_results(paged_users, session)
    return SearchPage(
        keyword=keyword,
        field=field,
        page=page,
        page_size=page_size,
        total_users=total_users,
        results=results,
    )


def format_results_text(results: list[dict]) -> str:
    """把搜索结果格式化为便于阅读的文本。"""

    if not results:
        return "未找到匹配结果。"

    lines = [f"共找到 {len(results)} 个用户的匹配记录:\n"]
    for user in results:
        lines.append(SEARCH_TEXT_SEPARATOR)
        lines.append(f"用户 ID: {user['user_id']}")
        lines.append(f"所在群组 ({len(user['groups'])} 个):")

        for group in user["groups"]:
            lines.append(
                f"  ┌ 群号: {group['group_id']} "
                f"({group['group_name'] or '未知'})"
            )
            lines.append(
                f"  │ 昵称: {group['nickname'] or SEARCH_UNKNOWN_VALUE}"
            )
            lines.append(
                f"  │ 群名片: {group['card'] or SEARCH_UNKNOWN_VALUE}"
            )
            lines.append(
                f"  │ 头衔: {group['title'] or SEARCH_UNKNOWN_VALUE}"
            )
            lines.append(
                f"  │ 入群时间: {group['join_time'] or SEARCH_UNKNOWN_VALUE}"
            )
            lines.append(
                "  └ 最后发言: "
                f"{group['last_sent_time'] or SEARCH_UNKNOWN_VALUE}"
            )
        lines.append("")

    return "\n".join(lines)


def format_results_json(results: list[dict]) -> str:
    """把兼容搜索结果格式化为 JSON。"""

    return json.dumps(
        {"count": len(results), "results": results},
        ensure_ascii=False,
        indent=2,
    )


def format_results(results: list[dict], output_format: str = "json") -> str:
    """按指定格式输出兼容搜索结果。"""

    if output_format == "json":
        return format_results_json(results)
    if output_format == "text":
        return format_results_text(results)
    raise ValueError(f"不支持的输出格式: {output_format}")


def format_search_page(
    result_page: SearchPage,
    output_format: str = "json",
) -> str:
    """格式化带分页元数据的搜索结果。"""

    if output_format == "json":
        return json.dumps(
            result_page.to_dict(),
            ensure_ascii=False,
            indent=2,
        )
    if output_format != "text":
        raise ValueError(f"不支持的输出格式: {output_format}")

    header = (
        f"共 {result_page.total_users} 个用户，"
        f"第 {result_page.page}/{result_page.total_pages or 1} 页，"
        f"本页 {len(result_page.results)} 个。"
    )
    return header + "\n" + format_results_text(result_page.results)


def search_and_print(
    keyword: str,
    db_path: str | Path = DB_PATH,
    output_format: str = SEARCH_OUTPUT_FORMAT,
    *,
    field: str = "any",
    page: int | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
):
    """打开数据库并打印兼容或分页搜索结果。"""

    engine, Session = init_db(db_path, create=False)
    try:
        with Session() as session:
            if page is None:
                results = search(keyword, session, field=field)
                print(format_results(results, output_format=output_format))
                return results

            result_page = search_page(
                keyword,
                session,
                field=field,
                page=page,
                page_size=page_size,
            )
        print(format_search_page(result_page, output_format=output_format))
        return result_page
    finally:
        engine.dispose()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="搜索群成员数据库")
    parser.add_argument("keyword", help="搜索关键字")
    parser.add_argument("--db", default=DB_PATH, help="SQLite 数据库路径")
    parser.add_argument(
        "--field",
        choices=SEARCH_FIELD_NAMES,
        default="any",
        help="限定搜索字段",
    )
    parser.add_argument("--page", type=int, default=1, help="页码")
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="每页用户数",
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default=SEARCH_OUTPUT_FORMAT,
        dest="output_format",
        help="输出格式",
    )
    arguments = parser.parse_args()
    search_and_print(
        arguments.keyword,
        arguments.db,
        arguments.output_format,
        field=arguments.field,
        page=arguments.page,
        page_size=arguments.page_size,
    )
