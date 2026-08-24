"""隔离式 FTS5 原型的语义与只读约束测试。"""

import hashlib
import sqlite3

import pytest

from social_database.fts_prototype import (
    FTS5UnavailableError,
    PrototypeScenario,
    _fts_user_ids,
    _like_user_ids,
    _open_read_only_database,
    rebuild_fts_index,
    run_fts_prototype,
)
from social_database.importer import import_to_db
from social_database.models import init_db
from social_database.output import format_json


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


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def prototype_database(tmp_path):
    database = tmp_path / "prototype-source.db"
    engine, Session = init_db(database)
    try:
        with Session.begin() as session:
            import_to_db(
                [
                    relation(
                        "user-alpha",
                        "group_100%",
                        'Team "Alpha" 100%',
                        nickname="Alice_100%",
                        card=r"path\tag",
                        join_time="2024-01-02 03:04",
                        last_sent_time="2024-05-06 07:08",
                        title="管理员",
                    ),
                    relation(
                        "user-alpha",
                        "group-2",
                        "Second Group",
                        nickname="Secondary",
                    ),
                    relation(
                        "user-beta",
                        "group-2",
                        "Second Group",
                        nickname="AliceX100Y",
                        card="ordinary",
                        title="普通成员",
                    ),
                ],
                session,
            )
    finally:
        engine.dispose()
    return database


def test_rebuilt_fts_matches_like_for_literal_and_unicode_queries(
    prototype_database,
    tmp_path,
):
    source = _open_read_only_database(prototype_database)
    scratch = sqlite3.connect(tmp_path / "scratch.db")
    try:
        try:
            first_build = rebuild_fts_index(source, scratch, batch_size=1)
        except FTS5UnavailableError:
            pytest.skip("当前 Python SQLite 未启用 FTS5 trigram")

        second_build = rebuild_fts_index(source, scratch, batch_size=2)
        assert first_build["indexed_rows"] == 3
        assert second_build["indexed_rows"] == 3

        scenarios = [
            PrototypeScenario("ascii", "nickname", "Alice"),
            PrototypeScenario("percent", "nickname", "100%"),
            PrototypeScenario("underscore", "nickname", "_100"),
            PrototypeScenario("backslash", "card", r"path\tag"),
            PrototypeScenario("quote", "group_name", 'Team "Alpha"'),
            PrototypeScenario("unicode", "title", "管理员"),
            PrototypeScenario("any", "any", "2024-01"),
        ]
        for scenario in scenarios:
            assert _fts_user_ids(
                scratch,
                scenario.keyword,
                scenario.field,
            ) == _like_user_ids(
                source,
                scenario.keyword,
                scenario.field,
            )

        assert _fts_user_ids(scratch, "100%", "nickname") == {
            "user-alpha"
        }
        assert _fts_user_ids(scratch, "_100", "nickname") == {
            "user-alpha"
        }
        with pytest.raises(ValueError, match="至少需要 3 个字符"):
            _fts_user_ids(scratch, "管理", "title")
    finally:
        scratch.close()
        source.close()


def test_prototype_is_read_only_private_and_routes_short_queries_to_like(
    prototype_database,
):
    scenarios = [
        PrototypeScenario("eligible", "nickname", "Alice"),
        PrototypeScenario("short", "title", "管理"),
    ]
    before = file_hash(prototype_database)
    report = run_fts_prototype(
        prototype_database,
        warmups=0,
        iterations=1,
        scenarios=scenarios,
    )
    rendered = format_json(report)

    if not report["runtime"]["fts5_available"]:
        pytest.skip("当前 Python SQLite 未启用 FTS5 trigram")

    assert file_hash(prototype_database) == before
    assert report["temporary_index"]["retained"] is False
    assert report["temporary_index"]["indexed_rows"] == 3
    assert report["all_eligible_results_equal"] is True
    assert report["recommendation"] == "candidate_for_hybrid_integration"
    assert report["privacy"] == {
        "keywords_included": False,
        "member_details_included": False,
    }
    assert report["scenarios"][0]["result_sets_equal"] is True
    assert report["scenarios"][1]["backend"] == "like_fallback"
    assert report["scenarios"][1]["fts"] is None
    assert report["scenarios"][1]["result_sets_equal"] is None
    assert "Alice" not in rendered
    assert "管理" not in rendered
    assert "user-alpha" not in rendered
    assert "Team" not in rendered

    with pytest.raises(ValueError, match="计时次数"):
        run_fts_prototype(prototype_database, iterations=0)
