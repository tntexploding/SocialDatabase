"""Synthetic numbered QQ lists, selection, quoting and navigation contracts."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from social_database.api import (
    _bounded_query_text,
    _parse_query_text,
    _query_command,
    create_app,
)
from social_database.search import SearchPage, search_qq_pattern_page
from social_database.service import ServiceSettings

AUTH = {"Authorization": "Bearer synthetic-query-token-0123456789"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("小明", ("小明", 1, None)),
        ("sd查：小明 13", ("小明", 1, 13)),
        ("社交查询 小明 --page 2", ("小明", 2, None)),
        ("9000000001", ("9000000001", 1, None)),
        ('"小明 2"', ("小明 2", 1, None)),
        ('sd查 "小明 2" 3', ("小明 2", 1, 3)),
        ('"小明 2" --page 2', ("小明 2", 2, None)),
        ("O'Brien 2", ("O'Brien", 1, 2)),
        ("小明  张三 3", ("小明  张三", 1, 3)),
    ],
)
def test_query_syntax(text, expected):
    assert _parse_query_text(text) == (*expected, None)


@pytest.mark.parametrize(
    "text",
    ["", "sd查", '""', '"未闭合', '"小明"2', "小明 0", "小明 -1",
     "小明 2147483648", "小明 --page 0", "小明 --page -2",
     "小明 --page abc", "小明 --page", "小明 --page 2 3", "x" * 129],
)
def test_invalid_query_syntax(text):
    with pytest.raises(ValueError):
        _parse_query_text(text)


@pytest.mark.parametrize("keyword", ['小明 2', '昵称 "甲" \\ 路径', "x" * 128])
def test_generated_commands_round_trip(keyword):
    command = _query_command(keyword).removeprefix("/sd query ")
    assert _parse_query_text("sd查 " + command) == (keyword, 1, None, None)
    assert _parse_query_text("sd查 " + command + " 13") == (keyword, 1, 13, None)
    assert _parse_query_text("sd查 " + command + " --page 2") == (keyword, 2, None, None)


@pytest.fixture
def client(tmp_path):
    settings = ServiceSettings(
        db_path=str(tmp_path / "synthetic-query.db"),
        api_token=AUTH["Authorization"].removeprefix("Bearer "),
    )
    with TestClient(create_app(settings)) as session:
        payload = {
            "schema_version": 1, "producer": "query-fixture",
            "batch_id": "query-fixture-1", "source_name": "synthetic only",
            "observed_at_utc": "2026-09-18T00:00:00Z",
            "records": [
                {
                    "group_id": "900000000", "group_name": "合成群",
                    "user_id": str(9000000000 + index),
                    "nickname": f"合成匹配成员 {index:02}",
                    "role": "member",
                }
                for index in range(1, 24)
            ],
        }
        assert session.post("/api/v1/imports/json", headers=AUTH, json=payload).status_code == 201
        yield session


def query(client, text):
    response = client.post("/api/v1/query-text", headers=AUTH, json={"q": text})
    assert response.status_code == 200
    return response.text


def test_ten_per_page_global_numbers_and_detail(client):
    before = client.get("/api/v1/search", headers=AUTH, params={"q": "合成匹配"}).json()
    first = query(client, "sd查 合成匹配")
    second = query(client, "sd查 合成匹配 --page 2")
    last = query(client, "sd查 合成匹配 --page 3")
    detail = query(client, "sd查 合成匹配 13")
    assert re.findall(r"^([0-9]+)\.", first, re.M) == [str(i) for i in range(1, 11)]
    assert re.findall(r"^([0-9]+)\.", second, re.M) == [str(i) for i in range(11, 21)]
    assert re.findall(r"^([0-9]+)\.", last, re.M) == ["21", "22", "23"]
    assert "共 23 条" in first
    assert '下一页：/sd query "合成匹配" --page 2' in first
    assert '上一页：/sd query "合成匹配" --page 1' in second
    assert "下一页" not in last
    assert "第 13/23 条" in detail and "QQ：9000000013" in detail
    assert "9000000012" not in detail and "9000000014" not in detail
    assert '返回列表：/sd query "合成匹配" --page 2' in detail
    assert "合成群（900000000）" in detail
    assert all("最近记录" not in text and "2026-09-18" not in text for text in (first, second, last, detail))
    assert client.get("/api/v1/search", headers=AUTH, params={"q": "合成匹配"}).json() == before


def test_numeric_keyword_and_selection_are_not_confused(client):
    assert "9000000013" in query(client, "sd查 9000000013")
    assert "第 1/1 条" in query(client, "sd查 9000000013 1")
    assert "9000000013" in query(client, 'sd查 "合成匹配成员 13"')
    assert "第 1/1 条" in query(client, 'sd查 "合成匹配成员 13" 1')


def test_no_match_and_out_of_range_are_distinct(client):
    assert query(client, "秘密无匹配") == "未找到匹配成员。"
    assert query(client, "秘密无匹配 2") == "未找到匹配成员。"
    assert "共 23 条结果，没有第 24 条" in query(client, "合成匹配 24")
    assert "共 23 条结果，没有第 4 页" in query(client, "合成匹配 --page 4")


def test_invalid_options_never_query_database(client, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("invalid query must not open a database")
    monkeypatch.setattr("social_database.api.init_db", unexpected)
    for text in ("合成匹配 0", "合成匹配 --page -1", '"未闭合'):
        response = client.post("/api/v1/query-text", headers=AUTH, json={"q": text})
        assert response.status_code == 422
    assert client.post("/api/v1/query-text", json={"q": "合成匹配 1"}).status_code == 401


def test_long_fields_keep_all_ten_numbers_and_navigation():
    groups = [
        {"group_id": "g" * 200, "group_name": "群" * 1000, "card": "名\n" * 2000,
         "last_seen_at_utc": "2099-01-01", "role": "member"}
        for _ in range(8)
    ]
    users = [{"user_id": str(9000000000 + i), "groups": groups} for i in range(1, 11)]
    page = SearchPage('关键词 "2"', "any", 1, 10, 20, users)
    rendered = _bounded_query_text(page)
    detail = _bounded_query_text(SearchPage(page.keyword, "any", 1, 1, 20, users[:1]), selected=1)
    assert re.findall(r"^([0-9]+)\.", rendered, re.M) == [str(i) for i in range(1, 11)]
    assert "下一页" in rendered and "详情" in rendered
    assert "另有 3 个群组未展开" in detail
    assert "2099-01-01" not in detail + rendered
    assert len(detail) <= 3000 and len(rendered) <= 3000
    assert "…" in detail and "…" in rendered


def test_maximum_keyword_still_accepts_navigation_syntax(client):
    keyword = "x" * 128
    assert query(client, json.dumps(keyword) + " --page 2") == "未找到匹配成员。"


@pytest.fixture
def qq_ends_client(client):
    expected = sorted(["12034", "129999999934"] + [f"12{i:05d}34" for i in range(1, 24)])
    misses = ["992000034", "120000099", "912000034", "1200000349",
              "12A34", "12_34", "12%34", "12*34", "12１34", "888888888"]
    records = [
        {"group_id": "123456734", "group_name": "12*34", "user_id": uid,
         "nickname": "首尾合成", "role": "member"}
        for uid in expected + misses
    ]
    records[-1]["nickname"] = "12*34"
    records.append({"group_id": "900000001", "group_name": "合成第二群",
                    "user_id": expected[0], "card": "另一名片", "role": "member"})
    payload = {"schema_version": 1, "producer": "query-fixture", "batch_id": "qq-ends-1",
               "source_name": "synthetic only", "observed_at_utc": "2026-10-14T00:00:00Z",
               "records": records}
    assert client.post("/api/v1/imports/json", headers=AUTH, json=payload).status_code == 201
    return client, expected


def test_qq_ends_matches_only_ids_and_preserves_numbering_and_data(qq_ends_client):
    client, expected = qq_ends_client
    before = client.get("/api/v1/stats", headers=AUTH).json()
    pages = [query(client, f"sd查 12*34 --page {page}") for page in (1, 2, 3)]
    ids = [uid for page in pages for uid in re.findall(r"QQ ([0-9]+)｜", page)]
    assert ids == expected
    assert "共 25 条" in pages[0]
    assert re.findall(r"^([0-9]+)\.", pages[1], re.M) == [str(i) for i in range(11, 21)]
    assert '下一页：/sd query "12*34" --page 2' in pages[0]
    detail = query(client, "sd查 12*34 1")
    assert "第 1/25 条" in detail and "QQ：" + expected[0] in detail
    assert "123456734" in detail and "900000001" in detail
    assert "QQ：" + expected[12] in query(client, "sd查 12*34 13")
    assert '返回列表：/sd query "12*34" --page 2' in query(client, "sd查 12*34 13")
    assert query(client, 'sd查 "12*34"') == pages[0]
    assert "没有第 26 条" in query(client, "12*34 26")
    assert "没有第 4 页" in query(client, "12*34 --page 4")
    assert query(client, "77*88") == "未找到匹配成员。"
    assert all(len(page) <= 3000 and "最近记录" not in page for page in pages + [detail])
    after = client.get("/api/v1/stats", headers=AUTH).json()
    for key in ("schema_version", "groups", "members", "relations", "relation_observations", "import_batches"):
        assert before[key] == after[key]


def test_qq_ends_does_not_change_generic_json_or_other_literal_wildcards(qq_ends_client):
    client, _ = qq_ends_client
    response = client.get("/api/v1/search", headers=AUTH, params={"q": "12*34", "field": "user_id"})
    assert [user["user_id"] for user in response.json()["results"]] == ["12*34"]
    for keyword in ("12%34", "12_34", "12A34"):
        assert "共 1 条" in query(client, keyword)
        assert f"QQ {keyword}｜" in query(client, keyword)
    for keyword in ("12**34", "12?34", "12*", "*34", "12*34' OR 1=1"):
        literal = client.get("/api/v1/search", headers=AUTH, params={"q": keyword}).json()
        rendered = query(client, keyword)
        if literal["total_users"]:
            assert f"共 {literal['total_users']} 条" in rendered
        else:
            assert rendered == "未找到匹配成员。"


def test_qq_ends_preserves_zero_in_suffix(client):
    rendered = query(client, "90*01")
    assert "共 1 条" in rendered and "QQ 9000000001｜" in rendered


def test_qq_ends_requires_existing_authentication(client):
    for headers in ({}, {"Authorization": "Bearer wrong-token"}):
        response = client.post("/api/v1/query-text", headers=headers, json={"q": "12*34"})
        assert response.status_code == 401


@pytest.mark.parametrize("pattern", ["1*34", "12*3", "123*34", "12**34", "１２*34", "12%34", "12*34' OR 1=1"])
def test_qq_ends_helper_rejects_unsupported_patterns_before_database_access(pattern):
    with pytest.raises(ValueError, match="QQ 首尾查询格式"):
        search_qq_pattern_page(pattern, None)


@pytest.mark.parametrize("options", [{"page": 0}, {"page_size": 0}, {"page_size": 1001}])
def test_qq_ends_helper_keeps_pagination_limits(options):
    with pytest.raises(ValueError):
        search_qq_pattern_page("12*34", None, **options)


@pytest.mark.parametrize("suffix,expected", [
    ("--name 小", (1, None, "小")),
    ("--name 小明 2", (1, 2, "小明")),
    ("2 --name 小明", (1, 2, "小明")),
    ("--name 小明1 --page 2", (2, None, "小明1")),
    ("--page 2 --name 小明", (2, None, "小明")),
    ('--name " A"', (1, None, " A")),
])
def test_name_prefix_syntax(suffix, expected):
    assert _parse_query_text("sd查 25*82 " + suffix) == ("25*82", *expected)


@pytest.mark.parametrize("prefix", ["小", "小明", "小明1", "%_", '"A', "A\\", " A"])
def test_name_prefix_navigation_round_trip(prefix):
    command = _query_command("25*82", prefix).removeprefix("/sd query ")
    assert _parse_query_text(command) == ("25*82", 1, None, prefix)
    assert _parse_query_text(command + " 13") == ("25*82", 1, 13, prefix)
    assert _parse_query_text(command + " --page 2") == ("25*82", 2, None, prefix)


@pytest.mark.parametrize("text", [
    "25*82 --name", '25*82 --name ""', '25*82 --name "   "',
    "25*82 --name 四个字符", "25*82 --name 小 --name 明",
    '25*82 --name "小', '25*82 --name "小"2',
    "25*82 --name 小 --page 0", "25*82 --name 小 2 --page 2",
    "25*82 --name 小 --page 2 3", "25*82 --name 小 --unknown 2",
    "小明 --name 小", "25%82 --name 小", "25*82 --page --name 小",
])
def test_invalid_name_prefix_before_database_access(client, monkeypatch, text):
    def unexpected(*args, **kwargs):
        raise AssertionError("invalid query must not open a database")
    monkeypatch.setattr("social_database.api.init_db", unexpected)
    response = client.post("/api/v1/query-text", headers=AUTH, json={"q": text})
    assert response.status_code == 422


@pytest.mark.parametrize("prefix", ["", "    ", "四个字符", 1, True])
def test_name_prefix_helper_validation(prefix):
    with pytest.raises(ValueError, match="名称前缀"):
        search_qq_pattern_page("25*82", None, name_prefix=prefix)


@pytest.fixture
def named_qq_client(client):
    records = [
        {"group_id": "800000000", "group_name": "合成群", "user_id": f"25{i:05d}82",
         "nickname": f"小明{i}", "role": "member"}
        for i in range(1, 14)
    ]
    for i, nickname, card in [
        (14, "另一昵称", "小明名片"), (15, "前小明", None),
        (16, "%_名", None), (17, "_Ab", None), (18, '"A', None),
        (19, "A\\B", None),
    ]:
        records.append({"group_id": "800000000", "user_id": f"25{i:05d}82",
                        "nickname": nickname, "card": card, "role": "member"})
    records.extend([
        {"group_id": "800000001", "user_id": "250000182", "card": "小明另群"},
        {"group_id": "800000000", "user_id": "990000182", "nickname": "小明错号"},
        {"group_id": "800000000", "user_id": "25A82", "nickname": "小明非数字"},
    ])
    payload = {"schema_version": 1, "producer": "query-fixture", "batch_id": "qq-name-1",
               "source_name": "synthetic only", "observed_at_utc": "2026-10-19T00:00:00Z",
               "records": records}
    assert client.post("/api/v1/imports/json", headers=AUTH, json=payload).status_code == 201
    return client


def test_name_prefix_filters_deduplicates_and_preserves_navigation_and_data(named_qq_client):
    client = named_qq_client
    before = client.get("/api/v1/stats", headers=AUTH).json()
    first = query(client, "25*82 --name 小明")
    second = query(client, "25*82 --name 小明 --page 2")
    ids = [uid for page in (first, second) for uid in re.findall(r"QQ ([0-9]+)｜", page)]
    assert ids == [f"25{i:05d}82" for i in range(1, 15)]
    assert "共 14 条" in first
    assert '下一页：/sd query "25*82" --name "小明" --page 2' in first
    assert "14. 小明名片" in second
    assert query(client, "25*82 --name 小") == first.replace('"小明"', '"小"')
    assert "共 5 条" in query(client, "25*82 --name 小明1")
    detail = query(client, "25*82 --name 小明 14")
    assert "第 14/14 条" in detail and "QQ：250001482" in detail
    assert '返回列表：/sd query "25*82" --name "小明" --page 2' in detail
    assert "没有第 15 条" in query(client, "25*82 --name 小明 15")
    assert query(client, "25*82 --name 不存在") == "未找到匹配成员。"
    assert "共 19 条" in query(client, "25*82")
    after = client.get("/api/v1/stats", headers=AUTH).json()
    for key in ("schema_version", "groups", "members", "relations", "relation_observations", "import_batches"):
        assert after[key] == before[key]


@pytest.mark.parametrize("prefix,index", [("%", 16), ("%_", 16), ("_", 17), ('"A', 18), ("A\\", 19)])
def test_name_prefix_is_literal(named_qq_client, prefix, index):
    rendered = query(named_qq_client, "25*82 --name " + json.dumps(prefix))
    assert "共 1 条" in rendered and f"QQ 25{index:05d}82｜" in rendered
