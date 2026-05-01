"""群成员数据管理系统 - 主入口"""

import os
import sys

from config import DB_PATH, SEARCH_EXIT_COMMANDS, SEARCH_OUTPUT_FORMAT, SEARCH_PROMPT
from importer import import_xlsx
from search import search_and_print


def print_help():
    print("""
群成员数据管理系统
==================
在项目根目录下运行以下命令：
用法:
    python main.py import <xlsx文件路径>    导入 xlsx 数据到数据库
    python main.py search <关键字>          搜索数据库（默认 JSON 输出）
    python main.py interactive              进入交互式搜索模式
    python main.py help                     显示帮助
""")


def interactive_mode():
    """交互式搜索模式"""
    print("进入交互式搜索模式 (输入 'quit' 或 'exit' 退出)\n")

    while True:
        try:
            keyword = input(SEARCH_PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            break

        if keyword.lower() in SEARCH_EXIT_COMMANDS:
            print("退出。")
            break

        if not keyword:
            continue

        search_and_print(keyword, DB_PATH, SEARCH_OUTPUT_FORMAT)


def main():
    if len(sys.argv) < 2:
        print_help()
        return

    command = sys.argv[1].lower()

    if command == "import":
        if len(sys.argv) < 3:
            print("请指定 xlsx 文件路径。")
            print("用法: python main.py import <xlsx文件路径>")
            return
        filepath = sys.argv[2]
        if not os.path.exists(filepath):
            print(f"文件不存在: {filepath}")
            return
        import_xlsx(filepath, DB_PATH)

    elif command == "search":
        if len(sys.argv) < 3:
            print("请指定搜索关键字。")
            print("用法: python main.py search <关键字>")
            return
        keyword = sys.argv[2]
        search_and_print(keyword, DB_PATH, SEARCH_OUTPUT_FORMAT)

    elif command == "interactive":
        interactive_mode()

    elif command == "help":
        print_help()

    else:
        print(f"未知命令: {command}")
        print_help()


if __name__ == "__main__":
    main()
