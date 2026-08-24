"""数据库搜索与结果格式化。"""

import json
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session as SessionType, joinedload

from .config import (
    DB_PATH,
    SEARCH_OUTPUT_FORMAT,
    SEARCH_TEXT_SEPARATOR,
    SEARCH_UNKNOWN_VALUE,
)
from .models import Group, MemberGroupInfo, init_db


def _escape_like(keyword: str) -> str:
    """转义 LIKE 通配符，使用户输入按普通文本匹配。"""

    return (
        keyword.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def search(keyword: str, session: SessionType) -> list[dict]:
    """查找匹配用户，并返回这些用户的全部群组资料。"""

    keyword = keyword.strip()
    if not keyword:
        return []

    like_pattern = f"%{_escape_like(keyword)}%"
    conditions = or_(
        MemberGroupInfo.user_id.like(like_pattern, escape="\\"),
        MemberGroupInfo.group_id.like(like_pattern, escape="\\"),
        MemberGroupInfo.nickname.like(like_pattern, escape="\\"),
        MemberGroupInfo.card.like(like_pattern, escape="\\"),
        MemberGroupInfo.title.like(like_pattern, escape="\\"),
        MemberGroupInfo.join_time.like(like_pattern, escape="\\"),
        MemberGroupInfo.last_sent_time.like(like_pattern, escape="\\"),
        Group.group_name.like(like_pattern, escape="\\"),
    )

    matched_users = (
        select(MemberGroupInfo.user_id)
        .join(Group, MemberGroupInfo.group_id == Group.group_id)
        .where(conditions)
        .distinct()
        .subquery()
    )
    statement = (
        select(MemberGroupInfo)
        .options(joinedload(MemberGroupInfo.group))
        .join(
            matched_users,
            MemberGroupInfo.user_id == matched_users.c.user_id,
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
    """把搜索结果格式化为 JSON。"""

    return json.dumps(
        {"count": len(results), "results": results},
        ensure_ascii=False,
        indent=2,
    )


def format_results(results: list[dict], output_format: str = "json") -> str:
    """按指定格式输出结果，拒绝未知格式。"""

    if output_format == "json":
        return format_results_json(results)
    if output_format == "text":
        return format_results_text(results)
    raise ValueError(f"不支持的输出格式: {output_format}")


def search_and_print(
    keyword: str,
    db_path: str | Path = DB_PATH,
    output_format: str = SEARCH_OUTPUT_FORMAT,
) -> list[dict]:
    """打开已有数据库、执行搜索并打印结果。"""

    engine, Session = init_db(db_path, create=False)
    try:
        with Session() as session:
            results = search(keyword, session)
        print(format_results(results, output_format=output_format))
        return results
    finally:
        engine.dispose()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="搜索群成员数据库")
    parser.add_argument("keyword", help="搜索关键字")
    parser.add_argument("--db", default=DB_PATH, help="SQLite 数据库路径")
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default=SEARCH_OUTPUT_FORMAT,
        dest="output_format",
        help="输出格式",
    )
    arguments = parser.parse_args()
    search_and_print(arguments.keyword, arguments.db, arguments.output_format)
