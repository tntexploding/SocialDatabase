"""兼容旧用法的命令行入口。"""

from social_database.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
