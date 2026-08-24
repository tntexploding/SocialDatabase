"""数据库检查与备份测试。"""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from social_database.config import REQUIRED_COLUMNS
from social_database.importer import import_xlsx
from social_database.maintenance import (
    backup_database,
    check_database,
    format_backup_result,
    format_database_check,
)


def record(**values):
    result = {column: None for column in REQUIRED_COLUMNS}
    result.update(values)
    return result


def create_database(tmp_path, workbook_factory):
    workbook = workbook_factory(
        tmp_path / "source.xlsx",
        {
            "Members": [
                record(
                    group_id="g-1",
                    user_id="u-1",
                    nickname="Alice",
                    group_name="Group",
                )
            ]
        },
    )
    database = tmp_path / "members.db"
    import_xlsx(workbook, database)
    return database


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_database_check_detects_missing_observations(
    tmp_path,
    workbook_factory,
):
    database = create_database(tmp_path, workbook_factory)

    healthy = check_database(database)

    assert healthy["healthy"] is True
    assert healthy["relations"] == 1
    assert healthy["relation_observations"] == 1
    assert healthy["search_index"]["healthy"] is True
    assert json.loads(format_database_check(healthy))["healthy"] is True
    assert "状态: 健康" in format_database_check(healthy, "text")
    assert "搜索索引:" in format_database_check(healthy, "text")

    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM relation_observations")
    connection.commit()
    connection.close()

    unhealthy = check_database(database)
    assert unhealthy["healthy"] is False
    assert unhealthy["missing_relation_observations"] == 1


def test_backup_is_consistent_and_does_not_modify_source(
    tmp_path,
    workbook_factory,
):
    database = create_database(tmp_path, workbook_factory)
    target = tmp_path / "backups" / "members.db"
    source_hash = file_hash(database)

    result = backup_database(database, target)

    assert target.is_file()
    assert file_hash(database) == source_hash
    assert result["sha256"] == file_hash(target)
    assert result["integrity"] == "ok"
    assert check_database(target)["healthy"] is True
    assert "完整性: ok" in format_backup_result(result, "text")
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))

    with pytest.raises(FileExistsError, match="备份文件已存在"):
        backup_database(database, target)

    overwritten = backup_database(database, target, overwrite=True)
    assert overwritten["sha256"] == file_hash(target)
