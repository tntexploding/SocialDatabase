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

不得提交：

- data/input/ 中的真实 Excel。
- data/database/ 中的数据库。
- data/output/ 中的查询导出。
- 虚拟环境、缓存、覆盖率输出、构建产物、日志和临时文件。

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

1. 更新 config.py 中的 REQUIRED_COLUMNS。
2. 更新 models.py 的表模型。
3. 更新 importer.py 的标准化和 upsert 字段。
4. 按需求更新 search.py。
5. 在 migrations.py 中增加下一个顺序 schema 迁移。
6. 更新数据格式、架构文档和测试。

create_all 不会给已有表增加列，因此不能省略迁移步骤。迁移完成后必须更新
CURRENT_SCHEMA_VERSION，并同时测试空数据库和旧版本数据库升级。

## 测试规则

- 使用 pytest 的 tmp_path 创建 xlsx 和 SQLite 文件。
- 测试不得读写 data/ 中的真实资源。
- 不在项目根目录保存测试数据库、输出快照或重复备份。
- 一次完整测试应覆盖解析、导入、重复更新、回滚语义、搜索、导出、备份、
  健康检查和 CLI 退出码。

运行：

~~~powershell
python -m pytest
~~~

GitHub Actions 在 Python 3.10、3.12 和 3.14 上执行相同命令，并先运行
`python -m pip check`。本地开发只需使用一个受支持版本；涉及兼容性或依赖
声明的改动应以 CI 矩阵结果为准。

## 提交前检查

~~~powershell
python -m pytest
git diff --check
git status --short
~~~

测试成功后再提交。提交完成时，git status 应保持干净。
