"""命令行分派和退出码测试。"""

from social_database import cli


def test_help_returns_success(capsys):
    assert cli.main(["help"]) == 0
    assert "social-database" in capsys.readouterr().out


def test_search_combines_multiple_keyword_parts(monkeypatch):
    captured = {}

    def fake_search(keyword, db_path, output_format):
        captured.update(
            keyword=keyword,
            db_path=db_path,
            output_format=output_format,
        )

    monkeypatch.setattr(cli, "search_and_print", fake_search)

    exit_code = cli.main(
        [
            "search",
            "two",
            "words",
            "--db",
            "custom.db",
            "--format",
            "text",
        ]
    )

    assert exit_code == 0
    assert captured == {
        "keyword": "two words",
        "db_path": "custom.db",
        "output_format": "text",
    }


def test_missing_database_returns_error_without_creating_file(
    tmp_path,
    capsys,
):
    database = tmp_path / "missing.db"

    assert cli.main(["search", "anything", "--db", str(database)]) == 1
    assert "数据库不存在" in capsys.readouterr().err
    assert not database.exists()
