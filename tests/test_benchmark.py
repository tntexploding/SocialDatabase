"""搜索性能基准的只读与隐私约束测试。"""

import hashlib

import pytest

from social_database.benchmark import benchmark_search
from social_database.importer import import_to_db
from social_database.models import init_db
from social_database.output import format_json


def relation(user_id, group_id, group_name, nickname):
    return {
        "user_id": user_id,
        "group_id": group_id,
        "group_name": group_name,
        "nickname": nickname,
        "card": None,
        "join_time": None,
        "last_sent_time": None,
        "title": None,
    }


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_benchmark_is_read_only_and_omits_private_values(tmp_path):
    database = tmp_path / "benchmark.db"
    engine, Session = init_db(database)
    try:
        with Session.begin() as session:
            import_to_db(
                [
                    relation("private-user", "g-1", "Private Group", "秘密²"),
                    relation("other-user", "g-1", "Private Group", "Other"),
                ],
                session,
            )
    finally:
        engine.dispose()

    before = file_hash(database)
    report = benchmark_search(database, warmups=0, iterations=1, page_size=1)
    rendered = format_json(report)

    assert file_hash(database) == before
    assert report["database"]["relations"] == 2
    assert report["privacy"] == {
        "keywords_included": False,
        "member_details_included": False,
    }
    assert report["scenarios"]
    assert all(item["p95_ms"] >= 0 for item in report["scenarios"])
    scenario_names = {item["name"] for item in report["scenarios"]}
    assert "nickname_typical_selective" in scenario_names
    assert "any_miss_typical" in scenario_names
    assert all(item["keyword_length"] > 0 for item in report["scenarios"])
    assert "private-user" not in rendered
    assert "Private Group" not in rendered
    assert "秘密" not in str(report)

    with pytest.raises(ValueError, match="计时次数"):
        benchmark_search(database, iterations=0)
