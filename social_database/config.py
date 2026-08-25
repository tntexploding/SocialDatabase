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

# xlsx 文件中必须存在的旧版表头。扩展字段保持可选，确保旧工作簿仍可导入。
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

# 当前 AstrBot/OneBot 工作簿中已经出现、但 0.5.0 尚未保存的字段。
OPTIONAL_COLUMNS = [
    "sex",
    "age",
    "area",
    "level",
    "qq_level",
    "title_expire_time",
    "unfriendly",
    "card_changeable",
    "is_robot",
    "shut_up_timestamp",
    "role",
]

# 标准来源记录中的稳定列顺序，与现有工作簿表头保持一致。
SOURCE_COLUMNS = [
    "group_id",
    "user_id",
    "nickname",
    "card",
    "sex",
    "age",
    "area",
    "level",
    "qq_level",
    "join_time",
    "last_sent_time",
    "title_expire_time",
    "unfriendly",
    "card_changeable",
    "is_robot",
    "shut_up_timestamp",
    "role",
    "title",
    "group_name",
]

# 除群组名称外，其余来源属性都按成员—群组关系保存。
RELATION_FIELDS = tuple(
    column
    for column in SOURCE_COLUMNS
    if column not in ("group_id", "user_id", "group_name")
)

# 文本输出的展示常量
SEARCH_TEXT_SEPARATOR = "=" * 50
SEARCH_UNKNOWN_VALUE = "-"

# 交互模式配置
SEARCH_PROMPT = "搜索> "
SEARCH_EXIT_COMMANDS = ("quit", "exit", "q")
