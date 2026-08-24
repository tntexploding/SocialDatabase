# SocialDatabase

SocialDatabase 是一个面向本地使用的群成员数据整理工具。它从固定表头的
Excel 工作簿读取成员信息，合并到 SQLite 数据库，并提供命令行和交互式
多字段搜索。

项目默认不提交任何真实成员数据、Excel 文件或生成的数据库。

## 功能

- 读取一个 xlsx 文件中的全部工作表。
- 在导入前校验每个工作表的必要表头。
- 使用文件 SHA-256 识别并跳过已经成功导入的数据源。
- 按 user_id、group_id 和 user_id + group_id 三个层次去重。
- 重复导入时更新非空资料，并同步最新的非空群名称。
- 保存每次成功导入的来源、时间、数据质量和合并统计。
- 永久保留已收集的成员关系，不因后续数据源缺失而删除。
- 在用户 ID、群 ID、群名、昵称、群名片、头衔和时间字段中搜索。
- 可限定单个字段，并按用户分页；命中后仍返回该用户的全部群组资料。
- 支持 JSON、CSV 和 xlsx 搜索结果导出。
- 提供 SQLite 完整性检查、关系追踪覆盖检查和一致性在线备份。
- 支持 JSON、文本和持续交互三种查询方式。

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

相同文件内容默认只导入一次。确认需要重新处理时使用：

~~~powershell
python -m social_database import data/input/data.xlsx --force
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
python -m social_database search Alice --field nickname --page 2 --page-size 100
~~~

关键字中的百分号和下划线按普通字符搜索，不会被解释为 SQL 通配符。
`--field` 可使用 `any`、`user_id`、`group_id`、`group_name`、
`nickname`、`card`、`title`、`join_time` 或 `last_sent_time`。分页单位是
用户；某个用户命中后，其已有的全部群组关系会一并返回。

### 交互式搜索

~~~powershell
python -m social_database interactive
~~~

输入 quit、exit 或 q 退出。

### 查看数据库和导入历史

~~~powershell
python -m social_database stats
python -m social_database stats --format text
python -m social_database imports --limit 10
python -m social_database imports --limit 10 --format text
~~~

### 导出搜索结果

~~~powershell
python -m social_database export Alice --field nickname --output data/output/alice.json
python -m social_database export Alice --output data/output/alice.csv
python -m social_database export Alice --output data/output/alice.xlsx
~~~

默认根据输出扩展名选择格式。JSON 保留“用户—群组”层级，CSV 和 xlsx
按一条成员—群组关系一行展开。已有文件不会被静默覆盖；确认覆盖时增加
`--overwrite`。

### 检查与备份数据库

~~~powershell
python -m social_database check
python -m social_database check --format text
python -m social_database backup
python -m social_database backup D:\backup\members.db
~~~

`check` 健康时退出码为 0，发现完整性、外键或关系观察覆盖异常时为 2；普通
命令错误为 1。`backup` 使用 SQLite 在线备份接口，默认写入数据库同级的
`backups/` 目录。恢复步骤与维护约定见
[docs/operations.md](docs/operations.md)。

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
│   ├── exporter.py        JSON、CSV 和 xlsx 导出
│   ├── importer.py        Excel 解析和数据库 upsert
│   ├── maintenance.py     数据库检查与一致性备份
│   ├── migrations.py      SQLite schema 版本与兼容迁移
│   ├── models.py          SQLAlchemy 数据模型
│   ├── reporting.py       数据库统计和导入批次查询
│   └── search.py          搜索和结果格式化
├── data/
│   ├── input/             私有 xlsx 输入，不进入 Git
│   ├── database/          生成的 SQLite 数据库和默认备份，不进入 Git
│   └── output/            本地导出结果，不进入 Git
├── .github/workflows/     多 Python 版本 CI
├── docs/                  安装、架构、数据和开发文档
├── tests/                 自动化测试
├── main.py                旧入口兼容层
├── pyproject.toml         包元数据和工具配置
├── requirements.txt       运行依赖
└── requirements-dev.txt   开发与测试依赖
~~~

## 数据模型

数据库包含五张表：

1. groups：群组 ID 与群名称。
2. members：成员 ID。
3. member_group_info：成员在特定群中的昵称、名片、时间和头衔。
4. import_batches：成功导入的数据源、时间、哈希和统计。
5. relation_observations：成员关系首次和最近出现的批次。

member_group_info 使用 user_id + group_id 复合主键。导入在单个事务中
完成；失败时不会留下部分写入。SQLite 连接会启用外键约束。新版本首次
打开旧数据库时会执行只新增追踪表、不删除业务数据的兼容迁移。

架构和数据流说明见 [docs/architecture.md](docs/architecture.md)。
当前范围和远期服务化计划见 [docs/roadmap.md](docs/roadmap.md)。

## 开发

安装开发依赖后，完整测试命令为：

~~~powershell
python -m pytest
~~~

测试必须使用 pytest 提供的临时目录，不应写入 data/ 或项目根目录。
提交范围、资源存放和数据库结构变更规则见
[docs/development.md](docs/development.md)。
推送和拉取请求会在 Python 3.10、3.12 与 3.14 上运行同一测试套件。

## License

项目采用 [MIT License](LICENSE)。
