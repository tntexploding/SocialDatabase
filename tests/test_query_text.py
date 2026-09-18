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
from social_database.search import SearchPage
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
    assert _parse_query_text(text) == expected


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
    assert _parse_query_text("sd查 " + command) == (keyword, 1, None)
    assert _parse_query_text("sd查 " + command + " 13") == (keyword, 1, 13)
    assert _parse_query_text("sd查 " + command + " --page 2") == (keyword, 2, None)


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
