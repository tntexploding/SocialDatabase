# 数据库维护与导出

## 路径约定

未指定 `--db` 时使用 `data/database/members.db`。本地输入放在
`data/input/`，数据库及默认备份放在 `data/database/`，临时查询导出放在
`data/output/`；三类运行数据都不进入 Git。生产数据库和长期备份建议放在
仓库外部。

所有通过项目数据模型打开数据库的命令都会应用受支持的顺序迁移。程序会先
只读检查 schema 版本；高于当前程序支持版本的数据库不会执行 `create_all`
或任何迁移。因而对受支持的旧
数据库执行检查、搜索或导出时，可能先发生一次非破坏性 schema 升级；备份
命令则原样复制源数据库，不提前改变其 schema。schema 2 会创建可重建的
contentful FTS5 索引并增加数据库体积，重要旧库建议先执行一次 `backup`。
schema 3 只增加允许空值的来源字段和批次元数据列，不删除已有数据。

## 健康检查

~~~powershell
python -m social_database check
python -m social_database check --db D:\data\members.db --format text
~~~

检查内容包括：

- `PRAGMA integrity_check` 的 SQLite 页面与索引完整性结果。
- `PRAGMA foreign_key_check` 的外键违规。
- 当前 schema 版本是否与程序支持版本一致。
- 每条成员—群组关系是否具有关系观察记录。
- FTS5 内部完整性、索引行数以及全部索引内容是否与业务表一致。

健康时退出码为 0；完成检查但发现异常时为 2；数据库不存在、版本过新等命令
错误为 1。自动化脚本应区分后两种情况。

FTS5 运行时不可用但状态可安全回退时，检查会标记搜索为降级而不判定业务
数据库损坏。索引过期、缺失或与业务表内容不一致时会判定异常并返回 2。

## 搜索索引重建

~~~powershell
python -m social_database reindex
python -m social_database reindex --db D:\data\members.db --format text
~~~

索引在导入确有业务变化时于同一外层事务内重建。重建使用 savepoint：失败
不会撤销已经验证的业务合并，而是把索引标记为不可用或过期，使查询自动
回退 LIKE。`reindex` 用于健康检查发现内容漂移、索引文件异常，或更换到支持
FTS5 的 SQLite 运行时后恢复加速。重建成功退出码为 0，仍需回退时为 2。

## 一致性备份

~~~powershell
# 默认写入 data/database/backups/ 下的 UTC 时间戳文件
python -m social_database backup

# 指定目标
python -m social_database backup D:\backup\members.db

# 明确允许替换已有目标
python -m social_database backup D:\backup\members.db --overwrite
~~~

备份通过 SQLite Online Backup API 从只读源连接创建，不直接复制一个可能正在
写入的数据库文件。程序先写同目录临时文件，执行完整性检查并关闭连接，再
原子替换最终路径；成功摘要包含文件大小和 SHA-256。

## 恢复流程

恢复会替换当前数据库，因此项目暂不提供自动 `restore` 命令。建议按以下步骤
操作：

1. 把选定备份复制为一个恢复候选文件，不直接修改唯一的归档副本。
2. 对候选文件运行 `check --db <候选文件>`；旧备份会在候选副本上完成迁移。
3. 停止所有正在导入或写入目标数据库的进程。
4. 使用 `backup` 再保存一次当前数据库，然后把候选文件替换到配置的数据库
   路径。
5. 对恢复后的正式路径再次运行 `check`，再恢复正常使用。

## 0.5.0 备份恢复演练

2026-08-24 使用实际 schema 2 数据库完成了不替换正式库的恢复候选演练：

- 通过项目 `backup` 命令把源库在线备份到仓库外的系统临时目录，再复制为
  独立恢复候选，未直接修改唯一归档。
- 归档和恢复候选分别通过 `check`：83,580 条关系都有观察记录，FTS5
  83,580/83,580 行，内部完整性和内容一致性均健康。
- 两份文件在检查前后的 SHA-256 各自不变且彼此一致，源数据库哈希也未变化。
- 演练生成的归档、候选和临时目录已删除，没有写入项目数据目录或 Git 范围。

本次演练验证到“可生成一致归档并验证候选”这一安全边界。实际替换正式库仍
应按上方步骤停写、保存当前库并由维护者人工确认。稳定版本的完整门槛见
[release-checklist.md](release-checklist.md)。

## 0.6.0 schema 3 迁移记录

2026-08-25 在正式本地库升级前创建并保留
`data/database/backups/members-pre-0.6.0-schema2.db`。独立临时候选和正式库
随后均完成 schema 2→3 迁移与健康检查：83,580 条关系和观察记录保持不变，
FTS5 行数、内部完整性和内容一致性全部健康。临时候选已删除；保留的 schema
2 备份未被迁移，可用于明确的人工回滚。

## 搜索结果导出

~~~powershell
python -m social_database export 关键词 --output data/output/result.json
python -m social_database export 关键词 --field card --output data/output/result.csv
python -m social_database export 关键词 --output data/output/result.xlsx
~~~

JSON 保留每个用户及其全部群组资料；CSV 和 xlsx 将每条成员—群组关系展开为
一行。schema 3 导出包含全部 19 个来源字段和首次/最近观察批次及时间。格式
默认由 `.json`、`.csv` 或 `.xlsx` 扩展名决定，也可使用
`--export-format` 显式指定。导出同样使用临时文件和原子替换，已有文件必须
通过 `--overwrite` 明确允许覆盖。
