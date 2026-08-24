"""群成员数据管理系统命令行入口。"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import DB_PATH, SEARCH_EXIT_COMMANDS, SEARCH_OUTPUT_FORMAT, SEARCH_PROMPT
from .importer import import_xlsx
from .migrations import DatabaseVersionError
from .reporting import (
    format_database_stats,
    format_import_batches,
    get_database_stats,
    list_import_batches,
)
from .search import search_and_print


def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器。"""

    parser = argparse.ArgumentParser(
        prog="social-database",
        description="从 xlsx 导入群成员数据，并在 SQLite 中进行搜索。",
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
    return parser


def interactive_mode(
    db_path: str | Path = DB_PATH,
    output_format: str = SEARCH_OUTPUT_FORMAT,
) -> None:
    """持续读取关键字，直到收到退出命令。"""

    print("进入交互式搜索模式（输入 quit、exit 或 q 退出）\n")
    while True:
        try:
            keyword = input(SEARCH_PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            return

        if keyword.lower() in SEARCH_EXIT_COMMANDS:
            print("退出。")
            return
        if keyword:
            search_and_print(keyword, db_path, output_format)


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
            )
        elif args.command == "interactive":
            interactive_mode(args.db, args.output_format)
        elif args.command == "stats":
            print(
                format_database_stats(
                    get_database_stats(args.db),
                    args.output_format,
                )
            )
        elif args.command == "imports":
            print(
                format_import_batches(
                    list_import_batches(args.db, limit=args.limit),
                    args.output_format,
                )
            )
    except (
        DatabaseVersionError,
        FileNotFoundError,
        OSError,
        ValueError,
    ) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
