"""共享测试工具。"""

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from openpyxl import Workbook

from social_database.config import REQUIRED_COLUMNS


@pytest.fixture
def workbook_factory():
    """在 pytest 临时目录创建最小工作簿。"""

    def create(
        path: Path,
        sheets: Mapping[str, Sequence[dict]],
        headers: Sequence[str] = REQUIRED_COLUMNS,
    ) -> Path:
        workbook = Workbook()
        for index, (sheet_name, rows) in enumerate(sheets.items()):
            worksheet = workbook.active if index == 0 else workbook.create_sheet()
            worksheet.title = sheet_name
            worksheet.append(list(headers))
            for row in rows:
                worksheet.append([row.get(column) for column in headers])
        workbook.save(path)
        workbook.close()
        return path

    return create
