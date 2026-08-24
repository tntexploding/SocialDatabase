"""项目默认配置。

数据路径可以通过 CLI 的 --db 参数覆盖。
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

# 默认数据库文件名，可按需修改。
DB_PATH = str(DATA_DIR / "database" / "members.db")

# 搜索输出的默认格式：json / text
SEARCH_OUTPUT_FORMAT = "json"

# xlsx 文件中必须存在的表头
REQUIRED_COLUMNS = [
    "group_id",
    "user_id",
    "nickname",
    "card",
    "join_time",
    "last_sent_time",
    "title",
    "group_name",
]

# 文本输出的展示常量
SEARCH_TEXT_SEPARATOR = "=" * 50
SEARCH_UNKNOWN_VALUE = "-"

# 交互模式配置
SEARCH_PROMPT = "搜索> "
SEARCH_EXIT_COMMANDS = ("quit", "exit", "q")
