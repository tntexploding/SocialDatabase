# 数据库维护与导出

## 路径约定

未指定 `--db` 时使用 `data/database/members.db`。本地输入放在
`data/input/`，数据库及默认备份放在 `data/database/`，临时查询导出放在
`data/output/`；三类运行数据都不进入 Git。生产数据库和长期备份建议放在
仓库外部。

所有通过项目数据模型打开数据库的命令都会应用受支持的顺序迁移。因而对旧
数据库执行检查、搜索或导出时，可能先发生一次非破坏性 schema 升级；备份
命令则原样复制源数据库，不提前改变其 schema。

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

健康时退出码为 0；完成检查但发现异常时为 2；数据库不存在、版本过新等命令
错误为 1。自动化脚本应区分后两种情况。

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

## 搜索结果导出

~~~powershell
python -m social_database export 关键词 --output data/output/result.json
python -m social_database export 关键词 --field card --output data/output/result.csv
python -m social_database export 关键词 --output data/output/result.xlsx
~~~

JSON 保留每个用户及其全部群组资料；CSV 和 xlsx 将每条成员—群组关系展开为
一行。格式默认由 `.json`、`.csv` 或 `.xlsx` 扩展名决定，也可使用
`--export-format` 显式指定。导出同样使用临时文件和原子替换，已有文件必须
通过 `--overwrite` 明确允许覆盖。
