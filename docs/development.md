# 开发指南

## 初始化

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m social_database help
~~~

支持的最低 Python 版本由 pyproject.toml 的 requires-python 定义。

## Git 跟踪范围

应提交：

- social_database/ 下的源码。
- tests/ 下不含真实数据的测试。
- .github/workflows/ 下的 CI 配置。
- docs/、README.md 和许可证。
- pyproject.toml 与依赖清单。
- data/ 中的说明文件和目录占位文件。
- Dockerfile、compose.yaml、`.env.example` 和服务依赖清单。

不得提交：

- data/input/ 中的真实 Excel。
- data/database/ 中的数据库。
- data/output/ 中的查询导出。
- 虚拟环境、缓存、覆盖率输出、构建产物、日志和临时文件。
- `.env`、API 令牌和任何云服务器部署密钥。

不要使用 git add -f 绕过数据目录规则。需要共享示例时，应先匿名化，并作为
小型 fixture 放入 tests/fixtures/。

本地输入统一放在 data/input/，默认数据库和备份放在 data/database/，临时
查询导出放在 data/output/。需要长期保留或跨机器同步的生产备份应放在仓库
外部，并由独立备份策略管理；任何这些资源都不作为源码提交。

## 修改流程

1. 明确数据格式或行为变化。
2. 修改对应模块，保持 CLI 只负责参数与展示。
3. 为行为变化增加或更新测试。
4. 同步 README 和 docs。
5. 运行完整测试，再检查 git diff 与 git status。

## 增加字段

增加导入字段时按以下顺序处理：

1. 判断字段是旧格式必需列还是兼容可选列，再更新 config.py 中的
   REQUIRED_COLUMNS、SOURCE_COLUMNS 和 RELATION_FIELDS。
2. 更新 models.py 的表模型。
3. 更新 importer.py 的标准化和 upsert 字段。
4. 按需求更新 search.py。
5. 若字段可搜索，同步更新 search_index.py 的列、重建 SQL 和一致性检查。
6. 在 migrations.py 中增加下一个顺序 schema 迁移。
7. 更新数据格式、架构文档和测试。

create_all 不会给已有表增加列，因此不能省略迁移步骤。迁移完成后必须更新
CURRENT_SCHEMA_VERSION，并同时测试空数据库和旧版本数据库升级。

## 增加数据源格式

- 格式适配器只负责文件校验、字段标准化和来源元数据解析。
- 适配器必须生成 `ParsedRecords` 与 `ImportSource`，随后调用统一的
  `import_parsed_records`；不得复制 upsert 或批次去重逻辑。
- 新格式必须有显式格式版本和匿名化 fixture，并更新机器可读契约。
- 数据源缺席不能触发关系删除或成员状态变化。
- 采集器及 AstrBot 依赖不得加入核心运行依赖。

## 测试规则

- 使用 pytest 的 tmp_path 创建 xlsx 和 SQLite 文件。
- 测试不得读写 data/ 中的真实资源。
- 不在项目根目录保存测试数据库、输出快照或重复备份。
- 一次完整测试应覆盖解析、导入、重复更新、回滚语义、搜索、导出、备份、
  FTS5 同步与 LIKE 回退、健康检查、HTTP 认证/幂等和 CLI 退出码。

运行：

~~~powershell
python -m pytest
~~~

GitHub Actions 在 Python 3.10、3.12 和 3.14 上执行相同命令，并先运行
`python -m pip check`；Python 3.14 任务还会在运行器临时目录构建 wheel 和
sdist。本地开发只需使用一个受支持版本；涉及兼容性或依赖声明的改动应以
CI 矩阵结果为准。

## 性能基准规则

~~~powershell
python -m social_database.benchmark --db data/database/members.db
~~~

- 基准必须使用只读连接，不触发迁移或创建索引。
- 报告不得包含查询词、用户 ID、群 ID、昵称或其他成员字段。
- 默认只预热 1 次并计时 5 次，避免用大量重复查询制造伪精度。
- 正式基线运行前后核对数据库 SHA-256；自动测试仍只使用 `tmp_path`。
- 搜索实现变化后使用相同参数复测，并把环境和结果记录到
  [performance.md](performance.md)。

FTS5 候选实现先通过隔离原型验证：

~~~powershell
python -m social_database.fts_prototype --db data/database/members.db
~~~

- 源数据库必须使用 SQLite `mode=ro` 打开，不得触发迁移或写入。
- 临时索引只能创建在系统临时目录，命令退出后必须自动删除。
- 报告只保留字段、关键字长度、命中数量和耗时，不保存关键字或成员明细。
- 正式运行前后核对源数据库 SHA-256；通配符、Unicode、短词回退和重复
  重建使用 `tmp_path` 中的合成数据库测试。
- 原型只测“匹配用户 ID 集合”阶段；接入正式搜索后仍要重跑完整分页基准。

正式索引修改还必须验证：业务事务回滚时索引同步一并回滚；索引 savepoint
失败时业务数据仍提交且状态转为回退；健康检查能发现同数量但内容不同的
漂移；`reindex` 可以恢复一致状态。测试只使用 `tmp_path` 中的小型数据库。

## HTTP 与容器规则

- API 路由只负责认证、请求边界和状态码，必须复用现有导入、搜索、统计与维护
  函数。
- 测试使用 FastAPI TestClient 和 `tmp_path` 数据库，不启动真实监听端口。
- 默认不记录请求体、Authorization 或搜索查询参数；开启访问日志必须由部署者
  显式选择。
- SQLite 服务保持单 worker，写入通过进程内锁串行；不要在 Docker 或 Uvicorn
  配置中绕过。
- Docker 构建上下文不得包含 data、`.env`、测试数据库或虚拟环境；容器必须以
  非 root 用户运行并把数据库放在 `/data` 持久卷。

## 提交前检查

~~~powershell
python -m pytest
git diff --check
git status --short
~~~

测试成功后再提交。提交完成时，git status 应保持干净。

## 稳定版本检查

开发依赖中的 `build` 用于生成标准 wheel 和 sdist。稳定版本只进行一次集中
验证，分发包和独立安装目标必须写入系统临时目录，不在仓库创建 `dist/`：

~~~powershell
$releaseTemp = Join-Path ([System.IO.Path]::GetTempPath()) `
    ("social-database-release-" + [guid]::NewGuid())
python -m build --outdir (Join-Path $releaseTemp "dist")
~~~

构建后还需从仓库外的工作目录验证 wheel 安装和命令行入口，并核对版本、包
内容、许可证、实际数据库健康与前后哈希。确认临时路径位于系统临时目录后
再清理。完整顺序和发布记录见 [release-checklist.md](release-checklist.md)。
