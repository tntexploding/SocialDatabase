"""命令行分派和退出码测试。"""

from social_database import cli
from social_database.migrations import DatabaseVersionError


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


def test_import_forwards_force_flag(monkeypatch):
    captured = {}

    def fake_import(path, database, *, force):
        captured.update(path=path, database=database, force=force)

    monkeypatch.setattr(cli, "import_xlsx", fake_import)

    assert (
        cli.main(
            [
                "import",
                "source.xlsx",
                "--db",
                "custom.db",
                "--force",
            ]
        )
        == 0
    )
    assert captured["path"].name == "source.xlsx"
    assert captured["database"] == "custom.db"
    assert captured["force"] is True


def test_stats_and_import_history_commands(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "get_database_stats",
        lambda database: {"database": database},
    )
    monkeypatch.setattr(
        cli,
        "format_database_stats",
        lambda stats, output: f"stats:{stats['database']}:{output}",
    )
    monkeypatch.setattr(
        cli,
        "list_import_batches",
        lambda database, *, limit: [{"database": database, "limit": limit}],
    )
    monkeypatch.setattr(
        cli,
        "format_import_batches",
        lambda batches, output: (
            f"imports:{batches[0]['database']}:{batches[0]['limit']}:{output}"
        ),
    )

    assert cli.main(["stats", "--db", "custom.db", "--format", "text"]) == 0
    assert (
        cli.main(
            [
                "imports",
                "--db",
                "custom.db",
                "--limit",
                "5",
                "--format",
                "text",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "stats:custom.db:text" in output
    assert "imports:custom.db:5:text" in output


def test_newer_database_version_is_reported_cleanly(monkeypatch, capsys):
    def fail_with_newer_version(_database):
        raise DatabaseVersionError("数据库版本 99 高于程序支持的 1")

    monkeypatch.setattr(cli, "get_database_stats", fail_with_newer_version)

    assert cli.main(["stats", "--db", "future.db"]) == 1
    assert "数据库版本 99" in capsys.readouterr().err
