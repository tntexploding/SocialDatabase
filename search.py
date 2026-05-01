"""数据库搜索功能"""

import json

from sqlalchemy import or_
from sqlalchemy.orm import Session as SessionType
from config import (
    DB_PATH,
    SEARCH_OUTPUT_FORMAT,
    SEARCH_TEXT_SEPARATOR,
    SEARCH_UNKNOWN_VALUE,
)
from models import Group, Member, MemberGroupInfo, init_db


def search(keyword: str, session: SessionType) -> list[dict]:
    """
    搜索任意有效字段，返回包含关键字的所有匹配结果。
    搜索范围: user_id, group_id, group_name, nickname, card, title,
              join_time, last_sent_time
    返回以 user_id 为主键聚合的完整条目。
    """
    keyword = keyword.strip()
    if not keyword:
        return []

    like_pattern = f"%{keyword}%"

    query = (
        session.query(MemberGroupInfo)
        .join(Group, MemberGroupInfo.group_id == Group.group_id)
        .filter(
            or_(
                MemberGroupInfo.user_id.like(like_pattern),
                MemberGroupInfo.group_id.like(like_pattern),
                MemberGroupInfo.nickname.like(like_pattern),
                MemberGroupInfo.card.like(like_pattern),
                MemberGroupInfo.title.like(like_pattern),
                MemberGroupInfo.join_time.like(like_pattern),
                MemberGroupInfo.last_sent_time.like(like_pattern),
                Group.group_name.like(like_pattern),
            )
        )
        .all()
    )

    user_map: dict[str, dict] = {}
    for info in query:
        uid = str(info.user_id)
        if uid not in user_map:
            user_map[uid] = {
                "user_id": uid,
                "groups": []
            }

        user_map[uid]["groups"].append({
            "group_id": info.group_id,
            "group_name": info.group.group_name,
            "nickname": info.nickname,
            "card": info.card,
            "join_time": info.join_time,
            "last_sent_time": info.last_sent_time,
            "title": info.title,
        })

    return list(user_map.values())


def format_results_text(results: list[dict]) -> str:
    """将搜索结果格式化为可读字符串"""
    if not results:
        return "未找到匹配结果。"

    lines = []
    lines.append(f"共找到 {len(results)} 个用户的匹配记录:\n")

    for user in results:
        lines.append(SEARCH_TEXT_SEPARATOR)
        lines.append(f"用户 ID: {user['user_id']}")
        lines.append(f"所在群组 ({len(user['groups'])} 个):")

        for g in user["groups"]:
            lines.append(f"  ┌ 群号: {g['group_id']} ({g['group_name'] or '未知'})")
            lines.append(f"  │ 昵称: {g['nickname'] or SEARCH_UNKNOWN_VALUE}")
            lines.append(f"  │ 群名片: {g['card'] or SEARCH_UNKNOWN_VALUE}")
            lines.append(f"  │ 头衔: {g['title'] or SEARCH_UNKNOWN_VALUE}")
            lines.append(f"  │ 入群时间: {g['join_time'] or SEARCH_UNKNOWN_VALUE}")
            lines.append(f"  └ 最后发言: {g['last_sent_time'] or SEARCH_UNKNOWN_VALUE}")

        lines.append("")

    return "\n".join(lines)


def format_results_json(results: list[dict]) -> str:
    """将搜索结果格式化为 JSON 字符串"""
    payload = {
        "count": len(results),
        "results": results,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def format_results(results: list[dict], output_format: str = "json") -> str:
    """根据输出格式返回搜索结果字符串"""
    if output_format == "text":
        return format_results_text(results)
    return format_results_json(results)


def search_and_print(keyword: str, db_path: str = DB_PATH, output_format: str = SEARCH_OUTPUT_FORMAT):
    """搜索并打印结果"""
    _, Session = init_db(db_path)
    session = Session()

    try:
        results = search(keyword, session)
        print(format_results(results, output_format=output_format))
    finally:
        session.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python search.py <搜索关键字> [数据库路径]")
        sys.exit(1)

    kw = sys.argv[1]
    db = sys.argv[2] if len(sys.argv) > 2 else DB_PATH
    search_and_print(kw, db)
