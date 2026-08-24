# SocialDatabase

SocialDatabase 是一个面向本地使用的群成员数据整理工具。它从固定表头的
Excel 工作簿读取成员信息，合并到 SQLite 数据库，并提供命令行和交互式
多字段搜索。

项目默认不提交任何真实成员数据、Excel 文件或生成的数据库。

## 功能

- 读取一个 xlsx 文件中的全部工作表。
- 在导入前校验每个工作表的必要表头。
- 按 user_id、group_id 和 user_id + group_id 三个层次去重。
- 重复导入时更新非空资料，并同步最新的非空群名称。
- 在用户 ID、群 ID、群名、昵称、群名片、头衔和时间字段中搜索。
- 搜索命中任意资料后，返回该用户的全部群组资料。
- 支持 JSON、文本和持续交互三种使用方式。

## 环境要求

- Python 3.10 或更高版本
- SQLite 3（Python 标准库内置）

运行依赖：

- SQLAlchemy 2.x
- openpyxl 3.x

完整安装说明见 [docs/installation.md](docs/installation.md)。

## 快速开始

在 Windows PowerShell 中：

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m social_database help
~~~

macOS 或 Linux 使用 source .venv/bin/activate 激活虚拟环境。

### 导入数据

把私有 xlsx 文件放入 data/input/，然后执行：

~~~powershell
python -m social_database import data/input/data.xlsx
~~~

默认数据库路径是 data/database/members.db。可以显式指定其他位置：

~~~powershell
python -m social_database import data/input/data.xlsx --db D:\data\members.db
~~~

### 搜索数据

~~~powershell
python -m social_database search 关键词
python -m social_database search 关键词 --format text
python -m social_database search 关键词 --db D:\data\members.db
~~~

关键字中的百分号和下划线按普通字符搜索，不会被解释为 SQL 通配符。

### 交互式搜索

~~~powershell
python -m social_database interactive
~~~

输入 quit、exit 或 q 退出。

根目录的 main.py 保留了旧用法兼容：

~~~powershell
python main.py help
~~~

安装项目包后，也可以直接使用 social-database 命令。

## Excel 数据要求

每个工作表的第一行必须包含以下列：

| 列名 | 含义 |
| --- | --- |
| group_id | 群组唯一标识 |
| user_id | 成员唯一标识 |
| nickname | 成员昵称 |
| card | 群名片 |
| join_time | 入群时间 |
| last_sent_time | 最后发言时间 |
| title | 群头衔 |
| group_name | 群名称 |

允许存在其他列，程序只导入上述字段。缺少必要表头时整次导入失败；
数据行缺少 user_id 或 group_id 时该行被跳过。详细约定见
[docs/data-format.md](docs/data-format.md)。

## 项目结构

~~~text
SocialDatabase/
├── social_database/       Python 包和核心业务逻辑
│   ├── cli.py             命令行入口
│   ├── config.py          默认配置
│   ├── importer.py        Excel 解析和数据库 upsert
│   ├── models.py          SQLAlchemy 数据模型
│   └── search.py          搜索和结果格式化
├── data/
│   ├── input/             私有 xlsx 输入，不进入 Git
│   └── database/          生成的 SQLite 数据库，不进入 Git
├── docs/                  安装、架构、数据和开发文档
├── tests/                 自动化测试
├── main.py                旧入口兼容层
├── pyproject.toml         包元数据和工具配置
├── requirements.txt       运行依赖
└── requirements-dev.txt   开发与测试依赖
~~~

## 数据模型

数据库包含三张表：

1. groups：群组 ID 与群名称。
2. members：成员 ID。
3. member_group_info：成员在特定群中的昵称、名片、时间和头衔。

member_group_info 使用 user_id + group_id 复合主键。导入在单个事务中
完成；失败时不会留下部分写入。SQLite 连接会启用外键约束。

架构和数据流说明见 [docs/architecture.md](docs/architecture.md)。

## 开发

安装开发依赖后，完整测试命令为：

~~~powershell
python -m pytest
~~~

测试必须使用 pytest 提供的临时目录，不应写入 data/ 或项目根目录。
提交范围、资源存放和数据库结构变更规则见
[docs/development.md](docs/development.md)。

## License

项目采用 [MIT License](LICENSE)。
