"""命令行分派和退出码测试。"""

from social_database import cli
from social_database.migrations import DatabaseVersionError


def test_help_returns_success(capsys):
    assert cli.main(["help"]) == 0
    assert "social-database" in capsys.readouterr().out


def test_search_combines_multiple_keyword_parts(monkeypatch):
    captured = {}

    def fake_search(
        keyword,
        db_path,
        output_format,
        *,
        field,
        page,
        page_size,
    ):
        captured.update(
            keyword=keyword,
            db_path=db_path,
            output_format=output_format,
            field=field,
            page=page,
            page_size=page_size,
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
            "--field",
            "nickname",
            "--page",
            "2",
            "--page-size",
            "25",
        ]
    )

    assert exit_code == 0
    assert captured == {
        "keyword": "two words",
        "db_path": "custom.db",
        "output_format": "text",
        "field": "nickname",
        "page": 2,
        "page_size": 25,
    }


def test_missing_database_returns_error_without_creating_file(
    tmp_path,
    capsys,
):
    database = tmp_path / "missing.db"

    assert cli.main(["search", "anything", "--db", str(database)]) == 1
    assert "数据库不存在" in capsys.readouterr().err
    assert not database.exists()


def test_interactive_search_keeps_full_results(monkeypatch, capsys):
    responses = iter(["Alice", "q"])
    captured = {}

    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    def fake_search(
        keyword,
        db_path,
        output_format,
        *,
        field,
        page=None,
        page_size=None,
    ):
        captured.update(
            keyword=keyword,
            db_path=db_path,
            output_format=output_format,
            field=field,
            page=page,
            page_size=page_size,
        )

    monkeypatch.setattr(cli, "search_and_print", fake_search)

    cli.interactive_mode("custom.db", "text", field="nickname")

    assert captured == {
        "keyword": "Alice",
        "db_path": "custom.db",
        "output_format": "text",
        "field": "nickname",
        "page": None,
        "page_size": None,
    }
    assert "退出" in capsys.readouterr().out


def test_import_forwards_source_metadata(monkeypatch):
    captured = {}

    def fake_import(
        path,
        database,
        *,
        force,
        producer,
        observed_at_utc,
    ):
        captured.update(
            path=path,
            database=database,
            force=force,
            producer=producer,
            observed_at_utc=observed_at_utc,
        )

    monkeypatch.setattr(cli, "import_xlsx", fake_import)

    assert (
        cli.main(
            [
                "import",
                "source.xlsx",
                "--db",
                "custom.db",
                "--force",
                "--producer",
                "astrbot",
                "--observed-at",
                "2026-08-25T10:00:00+08:00",
            ]
        )
        == 0
    )
    assert captured["path"].name == "source.xlsx"
    assert captured["database"] == "custom.db"
    assert captured["force"] is True
    assert captured["producer"] == "astrbot"
    assert captured["observed_at_utc"] == "2026-08-25T10:00:00+08:00"


def test_import_json_forwards_arguments(monkeypatch):
    captured = {}

    def fake_import(path, database, *, force):
        captured.update(path=path, database=database, force=force)

    monkeypatch.setattr(cli, "import_json", fake_import)

    assert (
        cli.main(
            [
                "import-json",
                "batch.json",
                "--db",
                "custom.db",
                "--force",
            ]
        )
        == 0
    )
    assert captured["path"].name == "batch.json"
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


def test_check_uses_distinct_unhealthy_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "check_database",
        lambda database: {"healthy": False, "database": database},
    )
    monkeypatch.setattr(
        cli,
        "format_database_check",
        lambda report, output: f"check:{report['database']}:{output}",
    )

    assert cli.main(["check", "--db", "custom.db", "--format", "text"]) == 2
    assert "check:custom.db:text" in capsys.readouterr().out


def test_reindex_uses_distinct_degraded_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "reindex_database",
        lambda database: {"ready": False, "database": database},
    )
    monkeypatch.setattr(
        cli,
        "format_reindex_result",
        lambda result, output: f"reindex:{result['database']}:{output}",
    )

    assert cli.main(["reindex", "--db", "custom.db", "--format", "text"]) == 2
    assert "reindex:custom.db:text" in capsys.readouterr().out


def test_backup_and_export_arguments_are_forwarded(monkeypatch, capsys):
    captured = {}

    def fake_backup(database, destination, *, overwrite):
        captured["backup"] = (database, destination, overwrite)
        return {"backup_path": destination}

    def fake_export(
        keyword,
        output_path,
        database,
        *,
        field,
        output_format,
        overwrite,
    ):
        captured["export"] = (
            keyword,
            output_path,
            database,
            field,
            output_format,
            overwrite,
        )
        return {"output_path": str(output_path)}

    monkeypatch.setattr(cli, "backup_database", fake_backup)
    monkeypatch.setattr(cli, "format_backup_result", lambda result, output: output)
    monkeypatch.setattr(cli, "export_search_results", fake_export)
    monkeypatch.setattr(cli, "format_export_result", lambda result: "exported")

    assert (
        cli.main(
            [
                "backup",
                "backup.db",
                "--db",
                "custom.db",
                "--overwrite",
                "--format",
                "text",
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "export",
                "two",
                "words",
                "--output",
                "results.csv",
                "--db",
                "custom.db",
                "--field",
                "card",
                "--export-format",
                "csv",
                "--overwrite",
            ]
        )
        == 0
    )

    assert captured["backup"] == ("custom.db", "backup.db", True)
    export = captured["export"]
    assert export[0] == "two words"
    assert export[1].name == "results.csv"
    assert export[2:] == ("custom.db", "card", "csv", True)
    assert "exported" in capsys.readouterr().out


def test_serve_reads_sensitive_settings_from_environment(monkeypatch):
    calls = []
    monkeypatch.setenv(
        "SOCIAL_DATABASE_API_TOKEN",
        "cli-test-token-0123456789",
    )
    monkeypatch.setenv(
        "SOCIAL_DATABASE_PREVIOUS_API_TOKEN",
        "cli-previous-token-0123456789",
    )
    monkeypatch.setattr(
        "social_database.service.run_service",
        lambda settings: calls.append(settings),
    )

    result = cli.main(
        [
            "serve",
            "--db",
            "service.db",
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
            "--no-docs",
        ]
    )

    assert result == 0
    assert len(calls) == 1
    settings = calls[0]
    assert settings.db_path == "service.db"
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000
    assert settings.docs_enabled is False
    assert settings.access_log is False
    assert settings.previous_api_token == "cli-previous-token-0123456789"
