"""群成员数据管理系统命令行入口。"""

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from .config import DB_PATH, SEARCH_EXIT_COMMANDS, SEARCH_OUTPUT_FORMAT, SEARCH_PROMPT
from .exporter import (
    EXPORT_FORMATS,
    export_search_results,
    format_export_result,
)
from .importer import import_xlsx
from .maintenance import (
    DatabaseMaintenanceError,
    backup_database,
    check_database,
    format_backup_result,
    format_database_check,
)
from .migrations import DatabaseVersionError
from .output import safe_print
from .reporting import (
    format_database_stats,
    format_import_batches,
    get_database_stats,
    list_import_batches,
)
from .search import DEFAULT_PAGE_SIZE, SEARCH_FIELD_NAMES, search_and_print


def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器。"""

    parser = argparse.ArgumentParser(
        prog="social-database",
        description="导入、检索、导出并维护 SQLite 群成员数据。",
    )
    subparsers = parser.add_subparsers(dest="command")

    import_parser = subparsers.add_parser("import", help="导入 xlsx 数据")
    import_parser.add_argument("xlsx_path", type=Path, help="xlsx 文件路径")
    import_parser.add_argument(
        "--db",
        default=DB_PATH,
        help=f"SQLite 数据库路径（默认: {DB_PATH}）",
    )
    import_parser.add_argument(
        "--force",
        action="store_true",
        help="即使文件内容已导入也强制处理",
    )

    search_parser = subparsers.add_parser("search", help="搜索已有数据库")
    search_parser.add_argument(
        "keyword",
        nargs="+",
        help="搜索关键字；包含空格时可直接输入多个词",
    )
    search_parser.add_argument("--db", default=DB_PATH, help="SQLite 数据库路径")
    search_parser.add_argument(
        "--field",
        choices=SEARCH_FIELD_NAMES,
        default="any",
        help="限定搜索字段",
    )
    search_parser.add_argument("--page", type=int, default=1, help="页码")
    search_parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="每页用户数",
    )
    search_parser.add_argument(
        "--format",
        choices=("json", "text"),
        default=SEARCH_OUTPUT_FORMAT,
        dest="output_format",
        help="输出格式",
    )

    interactive_parser = subparsers.add_parser(
        "interactive",
        help="进入交互式搜索",
    )
    interactive_parser.add_argument(
        "--db",
        default=DB_PATH,
        help="SQLite 数据库路径",
    )
    interactive_parser.add_argument(
        "--format",
        choices=("json", "text"),
        default=SEARCH_OUTPUT_FORMAT,
        dest="output_format",
        help="输出格式",
    )
    interactive_parser.add_argument(
        "--field",
        choices=SEARCH_FIELD_NAMES,
        default="any",
        help="限定搜索字段",
    )
    subparsers.add_parser("help", help="显示帮助")

    stats_parser = subparsers.add_parser("stats", help="查看数据库统计")
    stats_parser.add_argument("--db", default=DB_PATH, help="SQLite 数据库路径")
    stats_parser.add_argument(
        "--format",
        choices=("json", "text"),
        default=SEARCH_OUTPUT_FORMAT,
        dest="output_format",
        help="输出格式",
    )

    imports_parser = subparsers.add_parser(
        "imports",
        help="查看最近成功导入批次",
    )
    imports_parser.add_argument("--db", default=DB_PATH, help="SQLite 数据库路径")
    imports_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="返回的最大批次数量",
    )
    imports_parser.add_argument(
        "--format",
        choices=("json", "text"),
        default=SEARCH_OUTPUT_FORMAT,
        dest="output_format",
        help="输出格式",
    )

    check_parser = subparsers.add_parser("check", help="检查数据库健康状态")
    check_parser.add_argument("--db", default=DB_PATH, help="SQLite 数据库路径")
    check_parser.add_argument(
        "--format",
        choices=("json", "text"),
        default=SEARCH_OUTPUT_FORMAT,
        dest="output_format",
        help="输出格式",
    )

    backup_parser = subparsers.add_parser("backup", help="创建一致性数据库备份")
    backup_parser.add_argument(
        "destination",
        nargs="?",
        help="备份路径；省略时写入数据库同级 backups 目录",
    )
    backup_parser.add_argument("--db", default=DB_PATH, help="SQLite 数据库路径")
    backup_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允许覆盖指定的已有备份文件",
    )
    backup_parser.add_argument(
        "--format",
        choices=("json", "text"),
        default=SEARCH_OUTPUT_FORMAT,
        dest="output_format",
        help="输出格式",
    )

    export_parser = subparsers.add_parser("export", help="导出搜索结果")
    export_parser.add_argument(
        "keyword",
        nargs="+",
        help="搜索关键字",
    )
    export_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="输出文件路径",
    )
    export_parser.add_argument("--db", default=DB_PATH, help="SQLite 数据库路径")
    export_parser.add_argument(
        "--field",
        choices=SEARCH_FIELD_NAMES,
        default="any",
        help="限定搜索字段",
    )
    export_parser.add_argument(
        "--export-format",
        choices=EXPORT_FORMATS,
        default=None,
        help="导出格式；默认根据文件扩展名判断",
    )
    export_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允许覆盖已有导出文件",
    )
    return parser


def interactive_mode(
    db_path: str | Path = DB_PATH,
    output_format: str = SEARCH_OUTPUT_FORMAT,
    *,
    field: str = "any",
) -> None:
    """持续读取关键字，直到收到退出命令。"""

    safe_print("进入交互式搜索模式（输入 quit、exit 或 q 退出）\n")
    while True:
        try:
            keyword = input(SEARCH_PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            safe_print("\n退出。")
            return

        if keyword.lower() in SEARCH_EXIT_COMMANDS:
            safe_print("退出。")
            return
        if keyword:
            search_and_print(
                keyword,
                db_path,
                output_format,
                field=field,
            )


def main(argv: Sequence[str] | None = None) -> int:
    """运行命令并返回适合 shell 使用的退出码。"""

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in (None, "help"):
        parser.print_help()
        return 0

    try:
        if args.command == "import":
            import_xlsx(args.xlsx_path, args.db, force=args.force)
        elif args.command == "search":
            search_and_print(
                " ".join(args.keyword),
                args.db,
                args.output_format,
                field=args.field,
                page=args.page,
                page_size=args.page_size,
            )
        elif args.command == "interactive":
            interactive_mode(
                args.db,
                args.output_format,
                field=args.field,
            )
        elif args.command == "stats":
            safe_print(
                format_database_stats(
                    get_database_stats(args.db),
                    args.output_format,
                )
            )
        elif args.command == "imports":
            safe_print(
                format_import_batches(
                    list_import_batches(args.db, limit=args.limit),
                    args.output_format,
                )
            )
        elif args.command == "check":
            report = check_database(args.db)
            safe_print(format_database_check(report, args.output_format))
            return 0 if report["healthy"] else 2
        elif args.command == "backup":
            result = backup_database(
                args.db,
                args.destination,
                overwrite=args.overwrite,
            )
            safe_print(format_backup_result(result, args.output_format))
        elif args.command == "export":
            result = export_search_results(
                " ".join(args.keyword),
                args.output,
                args.db,
                field=args.field,
                output_format=args.export_format,
                overwrite=args.overwrite,
            )
            safe_print(format_export_result(result))
    except (
        DatabaseMaintenanceError,
        DatabaseVersionError,
        FileNotFoundError,
        OSError,
        SQLAlchemyError,
        ValueError,
        sqlite3.Error,
    ) as exc:
        safe_print(f"错误: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
