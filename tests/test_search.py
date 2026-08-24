"""搜索语义和结果格式测试。"""

import json

import pytest

from social_database.importer import import_to_db
from social_database.models import init_db
from social_database.search import format_results, search


def relation(user_id, group_id, group_name, **values):
    return {
        "user_id": user_id,
        "group_id": group_id,
        "group_name": group_name,
        "nickname": values.get("nickname"),
        "card": values.get("card"),
        "join_time": values.get("join_time"),
        "last_sent_time": values.get("last_sent_time"),
        "title": values.get("title"),
    }


def test_search_escapes_wildcards_and_returns_all_user_groups(tmp_path):
    database = tmp_path / "search.db"
    engine, Session = init_db(database)
    try:
        with Session.begin() as session:
            import_to_db(
                [
                    relation(
                        "u-1",
                        "g-1",
                        "Percent Group",
                        nickname="Alice_100%",
                    ),
                    relation(
                        "u-1",
                        "g-2",
                        "Other Group",
                        card="secondary",
                    ),
                    relation(
                        "u-2",
                        "g-3",
                        "Third Group",
                        nickname="AliceX100Y",
                    ),
                ],
                session,
            )

        with Session() as session:
            results = search("_100%", session)
            group_match = search("Other Group", session)
            percent_match = search("%", session)
            empty = search("   ", session)

        assert [item["user_id"] for item in results] == ["u-1"]
        assert [group["group_id"] for group in results[0]["groups"]] == [
            "g-1",
            "g-2",
        ]
        assert group_match == results
        assert [item["user_id"] for item in percent_match] == ["u-1"]
        assert empty == []
    finally:
        engine.dispose()


def test_result_formatting_is_explicit():
    results = [
        {
            "user_id": "u-1",
            "groups": [
                {
                    "group_id": "g-1",
                    "group_name": "Group",
                    "nickname": "Alice",
                    "card": None,
                    "join_time": None,
                    "last_sent_time": None,
                    "title": None,
                }
            ],
        }
    ]

    payload = json.loads(format_results(results, "json"))
    text = format_results(results, "text")

    assert payload["count"] == 1
    assert payload["results"][0]["user_id"] == "u-1"
    assert "用户 ID: u-1" in text
    assert format_results([], "text") == "未找到匹配结果。"
    with pytest.raises(ValueError, match="不支持的输出格式"):
        format_results(results, "yaml")
