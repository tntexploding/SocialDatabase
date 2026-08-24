# 架构说明

## 数据流

~~~text
xlsx 工作簿
    │
    ▼
文件哈希与重复批次检查
    │
    ▼
表头校验与单元格标准化
    │
    ▼
按主键归并本批数据
    │
    ▼
SQLite 单事务 upsert
    │
    ▼
导入批次与关系观察记录
    │
    ▼
按字段匹配并按用户分页
    │
    ▼
加载命中用户的全部群组资料
    │
    ▼
JSON、文本或文件导出
~~~

## 模块职责

- benchmark.py：以只读连接运行不包含成员明细的搜索性能基准。
- cli.py：参数解析、交互循环、错误信息和进程退出码。
- config.py：默认数据库路径、必要表头和展示常量。
- exporter.py：把完整搜索结果原子写入 JSON、CSV 或 xlsx。
- importer.py：Excel 解析、数据标准化、批内归并与 upsert。
- maintenance.py：数据库健康检查与 SQLite 一致性在线备份。
- migrations.py：SQLite schema 版本和旧数据库兼容迁移。
- models.py：SQLAlchemy 表定义、SQLite 连接和外键启用。
- output.py：生成 ASCII 安全 JSON，并兼容终端不支持的文本字符。
- reporting.py：数据库规模统计和导入批次查询。
- search.py：匹配查询、用户聚合和输出格式化。

## 数据表

### groups

group_id 是主键，group_name 保存最近一次导入的非空名称。

### members

user_id 是主键。成员的群内属性不放在此表，避免不同群组之间相互覆盖。

### member_group_info

user_id 与 group_id 构成复合主键。nickname、card、join_time、
last_sent_time 和 title 都属于成员在某个群组中的资料。

### import_batches

记录成功导入的来源类型、文件名、SHA-256、UTC 时间、跳过原因及新增、
更新、未变化数量。相同文件哈希默认不重复写入；强制导入会关联原批次。

### relation_observations

记录每条 user_id + group_id 关系首次和最近出现的导入批次。它只更新
最近观察位置，不会因后续数据源缺少该成员而删除关系。

## 导入一致性

导入分为两个阶段：

1. 完整读取并校验工作簿。任何工作表缺少必要表头时不会打开数据库写入。
2. 在一个数据库事务中写入群组、成员和关系。任意写入失败时整体回滚。

同一批数据中的重复关系先在内存中归并；后出现的非空字段覆盖先前值。
写入已有数据库时，新非空值覆盖旧值，空值不会清除旧值。

项目采用历史聚合语义，而不是当前成员同步语义。任何导入都不会因为某条
关系未出现在新文件中而删除它。

## 搜索语义

搜索先找出指定字段（或任意字段）包含关键字的用户 ID，再加载这些用户的
全部 member_group_info 记录。分页以去重后的用户 ID 为单位，因此同一用户
不会因为群组数量不同而占用多条分页名额。输入中的百分号、下划线和反斜杠
会被转义。

当前实现使用 SQLite LIKE 包含匹配。因为模式以通配符开头，普通 B-tree
索引无法消除关系表扫描。0.5.0 基线显示 ID 和群字段仍足够快，但 nickname
与 any 查询已超过交互目标，因此后续将验证 FTS5 trigram；在结果一致性、
短词回退和索引同步完成前，正式搜索仍使用当前实现。详见
[performance.md](performance.md)。

## 维护与文件写入

健康检查组合使用 SQLite `integrity_check`、外键检查、schema 版本和关系观察
覆盖数量。打开兼容的旧数据库时仍遵循统一迁移流程，因此检查前可能先完成
非破坏性升级。

备份使用 SQLite Online Backup API 从只读源连接复制到目标目录的临时文件，
通过完整性检查并关闭连接后再原子替换最终路径。搜索导出采用同样的
“同目录临时文件—原子替换”方式，避免留下看似完整的半成品。

## 数据库兼容

项目只支持 SQLite，并使用 PRAGMA user_version 管理 schema 版本。
Base.metadata.create_all 只负责创建缺失表，既有结构调整必须在
migrations.py 中提供顺序迁移。0.3 的首次升级只新增追踪表，并为原有关系
建立 legacy 基线批次。
