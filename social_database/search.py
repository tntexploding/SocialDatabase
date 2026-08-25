"""数据库搜索、分页与结果格式化。"""

from dataclasses import dataclass
from math import ceil
from pathlib import Path

from sqlalchemy import String, and_, func, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session as SessionType, aliased, joinedload

from .config import (
    DB_PATH,
    RELATION_FIELDS,
    SEARCH_OUTPUT_FORMAT,
    SEARCH_TEXT_SEPARATOR,
    SEARCH_UNKNOWN_VALUE,
)
from .models import (
    Group,
    ImportBatch,
    MemberGroupInfo,
    RelationObservation,
    init_db,
)
from .output import format_json, safe_print
from .search_index import (
    SEARCH_INDEX_FIELDS,
    fts_match_expression,
    is_search_index_ready,
    should_use_fts,
)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 1000
ANY_SEARCH_FIELD_NAMES = (
    "user_id",
    "group_id",
    "group_name",
    "nickname",
    "card",
    "title",
    "join_time",
    "last_sent_time",
)
SEARCH_FIELD_NAMES = (
    "any",
    *ANY_SEARCH_FIELD_NAMES,
    *(field for field in RELATION_FIELDS if field not in ANY_SEARCH_FIELD_NAMES),
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
    backend: str = "like"

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
            "backend": self.backend,
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
        "sex": MemberGroupInfo.sex,
        "age": MemberGroupInfo.age,
        "area": MemberGroupInfo.area,
        "level": MemberGroupInfo.level,
        "qq_level": MemberGroupInfo.qq_level,
        "title": MemberGroupInfo.title,
        "join_time": MemberGroupInfo.join_time,
        "last_sent_time": MemberGroupInfo.last_sent_time,
        "title_expire_time": MemberGroupInfo.title_expire_time,
        "unfriendly": MemberGroupInfo.unfriendly,
        "card_changeable": MemberGroupInfo.card_changeable,
        "is_robot": MemberGroupInfo.is_robot,
        "shut_up_timestamp": MemberGroupInfo.shut_up_timestamp,
        "role": MemberGroupInfo.role,
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


def _literal_like_condition(keyword: str, field: str):
    like_pattern = f"%{_escape_like(keyword)}%"
    columns = _search_columns()
    selected_columns = tuple(
        columns[name] for name in ANY_SEARCH_FIELD_NAMES
    ) if field == "any" else (columns[field],)
    conditions = [
        column.like(like_pattern, escape="\\")
        for column in selected_columns
    ]
    return conditions[0] if len(conditions) == 1 else or_(*conditions)


def _matched_users_like(keyword: str, field: str):
    return (
        select(MemberGroupInfo.user_id)
        .join(Group, MemberGroupInfo.group_id == Group.group_id)
        .where(_literal_like_condition(keyword, field))
        .distinct()
        .subquery()
    )


def _matched_users_fts(keyword: str, field: str):
    candidates = (
        text(
            """
            SELECT DISTINCT user_id, group_id
            FROM member_search
            WHERE member_search MATCH :match_expression
            """
        )
        .bindparams(
            match_expression=fts_match_expression(keyword, field)
        )
        .columns(user_id=String, group_id=String)
        .subquery()
    )
    return (
        select(MemberGroupInfo.user_id)
        .join(Group, MemberGroupInfo.group_id == Group.group_id)
        .join(
            candidates,
            and_(
                MemberGroupInfo.user_id == candidates.c.user_id,
                MemberGroupInfo.group_id == candidates.c.group_id,
            ),
        )
        .where(_literal_like_condition(keyword, field))
        .distinct()
        .subquery()
    )


def _fts_route_ready(
    keyword: str,
    field: str,
    session: SessionType,
) -> bool:
    return (
        field in SEARCH_INDEX_FIELDS
        and should_use_fts(keyword, field)
        and is_search_index_ready(session.connection())
    )


def _load_user_results(user_ids, session: SessionType) -> list[dict]:
    first_batch = aliased(ImportBatch)
    last_batch = aliased(ImportBatch)
    statement = (
        select(
            MemberGroupInfo,
            RelationObservation.first_seen_batch_id,
            RelationObservation.last_seen_batch_id,
            first_batch.observed_at_utc,
            first_batch.imported_at_utc,
            last_batch.observed_at_utc,
            last_batch.imported_at_utc,
        )
        .options(joinedload(MemberGroupInfo.group))
        .join(
            user_ids,
            MemberGroupInfo.user_id == user_ids.c.user_id,
        )
        .outerjoin(
            RelationObservation,
            and_(
                RelationObservation.user_id == MemberGroupInfo.user_id,
                RelationObservation.group_id == MemberGroupInfo.group_id,
            ),
        )
        .outerjoin(
            first_batch,
            first_batch.id == RelationObservation.first_seen_batch_id,
        )
        .outerjoin(
            last_batch,
            last_batch.id == RelationObservation.last_seen_batch_id,
        )
        .order_by(MemberGroupInfo.user_id, MemberGroupInfo.group_id)
    )
    matches = session.execute(statement).all()

    user_map: dict[str, dict] = {}
    for (
        info,
        first_seen_batch_id,
        last_seen_batch_id,
        first_observed_at,
        first_imported_at,
        last_observed_at,
        last_imported_at,
    ) in matches:
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
                "sex": info.sex,
                "age": info.age,
                "area": info.area,
                "level": info.level,
                "qq_level": info.qq_level,
                "join_time": info.join_time,
                "last_sent_time": info.last_sent_time,
                "title_expire_time": info.title_expire_time,
                "unfriendly": info.unfriendly,
                "card_changeable": info.card_changeable,
                "is_robot": info.is_robot,
                "shut_up_timestamp": info.shut_up_timestamp,
                "role": info.role,
                "title": info.title,
                "first_seen_batch_id": first_seen_batch_id,
                "last_seen_batch_id": last_seen_batch_id,
                "first_seen_at_utc": _utc_text(
                    first_observed_at or first_imported_at
                ),
                "last_seen_at_utc": _utc_text(
                    last_observed_at or last_imported_at
                ),
            }
        )

    return list(user_map.values())


def _utc_text(value) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds") + "Z"


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
    if _fts_route_ready(keyword, field, session):
        try:
            return _load_user_results(
                _matched_users_fts(keyword, field),
                session,
            )
        except DBAPIError:
            pass
    return _load_user_results(_matched_users_like(keyword, field), session)


def _build_search_page(
    keyword: str,
    field: str,
    page: int,
    page_size: int,
    matched_users,
    session: SessionType,
    *,
    backend: str,
) -> SearchPage:
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
        backend=backend,
    )


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

    if _fts_route_ready(keyword, field, session):
        try:
            return _build_search_page(
                keyword,
                field,
                page,
                page_size,
                _matched_users_fts(keyword, field),
                session,
                backend="fts5",
            )
        except DBAPIError:
            pass
    return _build_search_page(
        keyword,
        field,
        page,
        page_size,
        _matched_users_like(keyword, field),
        session,
        backend="like",
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
                "  │ 性别/年龄/地区: "
                f"{group.get('sex') or SEARCH_UNKNOWN_VALUE} / "
                f"{group.get('age') or SEARCH_UNKNOWN_VALUE} / "
                f"{group.get('area') or SEARCH_UNKNOWN_VALUE}"
            )
            lines.append(
                "  │ 群等级/QQ 等级: "
                f"{group.get('level') or SEARCH_UNKNOWN_VALUE} / "
                f"{group.get('qq_level') or SEARCH_UNKNOWN_VALUE}"
            )
            lines.append(
                f"  │ 角色: {group.get('role') or SEARCH_UNKNOWN_VALUE}"
            )
            lines.append(
                f"  │ 头衔: {group['title'] or SEARCH_UNKNOWN_VALUE}"
            )
            lines.append(
                "  │ 头衔到期/禁言到期: "
                f"{group.get('title_expire_time') or SEARCH_UNKNOWN_VALUE} / "
                f"{group.get('shut_up_timestamp') or SEARCH_UNKNOWN_VALUE}"
            )
            lines.append(
                "  │ 不友好/可改名片/机器人: "
                f"{group.get('unfriendly') or SEARCH_UNKNOWN_VALUE} / "
                f"{group.get('card_changeable') or SEARCH_UNKNOWN_VALUE} / "
                f"{group.get('is_robot') or SEARCH_UNKNOWN_VALUE}"
            )
            lines.append(
                f"  │ 入群时间: {group['join_time'] or SEARCH_UNKNOWN_VALUE}"
            )
            lines.append(
                "  │ 最后发言: "
                f"{group['last_sent_time'] or SEARCH_UNKNOWN_VALUE}"
            )
            lines.append(
                "  │ 首次观察: "
                f"#{group.get('first_seen_batch_id') or '-'} "
                f"{group.get('first_seen_at_utc') or SEARCH_UNKNOWN_VALUE}"
            )
            lines.append(
                "  └ 最近观察: "
                f"#{group.get('last_seen_batch_id') or '-'} "
                f"{group.get('last_seen_at_utc') or SEARCH_UNKNOWN_VALUE}"
            )
        lines.append("")

    return "\n".join(lines)


def format_results_json(results: list[dict]) -> str:
    """把兼容搜索结果格式化为 JSON。"""

    return format_json({"count": len(results), "results": results})


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
        return format_json(result_page.to_dict())
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
                safe_print(format_results(results, output_format=output_format))
                return results

            result_page = search_page(
                keyword,
                session,
                field=field,
                page=page,
                page_size=page_size,
            )
        safe_print(format_search_page(result_page, output_format=output_format))
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
