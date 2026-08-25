# 架构说明

## 数据流

~~~text
xlsx 工作簿 / JSON 文件 / HTTP JSON v1
    │
    ▼
格式适配器与标准记录批次
    │
    ▼
外部批次身份 / 规范化哈希去重
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
同一事务内重建 FTS5（失败则标记回退）
    │
    ▼
按字段匹配并按用户分页
    │
    ▼
加载命中用户的全部群组资料
    │
    ▼
CLI JSON、文本、文件导出 / HTTP 响应
~~~

## 模块职责

- benchmark.py：以只读连接运行不包含成员明细的搜索性能基准。
- api.py：认证、请求边界、HTTP 路由和响应状态。
- cli.py：参数解析、交互循环、错误信息和进程退出码。
- config.py：默认数据库路径、必要表头和展示常量。
- exporter.py：把完整搜索结果原子写入 JSON、CSV 或 xlsx。
- fts_prototype.py：在系统临时目录重建 FTS5，并与 LIKE 对照完整用户集合。
- importer.py：标准记录批次、Excel 适配、批内归并与通用 upsert。
- json_importer.py：版本化 JSON v1 验证、标准化与来源元数据解析。
- maintenance.py：数据库健康检查与 SQLite 一致性在线备份。
- migrations.py：SQLite schema 版本和旧数据库兼容迁移。
- models.py：SQLAlchemy 表定义、SQLite 连接和外键启用。
- output.py：生成 ASCII 安全 JSON，并兼容终端不支持的文本字符。
- reporting.py：数据库规模统计和导入批次查询。
- search.py：匹配查询、用户聚合和输出格式化。
- search_index.py：正式 FTS5 schema、全量同步、路由参数和健康状态。
- service.py：环境配置、单 worker Uvicorn 启动和隐私日志默认值。

## 数据表

### groups

group_id 是主键，group_name 保存最近一次导入的非空名称。

### members

user_id 是主键。成员的群内属性不放在此表，避免不同群组之间相互覆盖。

### member_group_info

user_id 与 group_id 构成复合主键。nickname、card、join_time、
last_sent_time 和 title，以及 sex、age、area、level、qq_level、
title_expire_time、unfriendly、card_changeable、is_robot、
shut_up_timestamp、role 都按成员在某个群组中的来源观察保存。把可能看似
全局的资料继续放在关系层，可以避免不同群组、不同采集时点互相覆盖。

### import_batches

记录成功导入的来源类型、格式版本、生产方、外部批次 ID、文件名、SHA-256、
采集/导入 UTC 时间及合并统计。schema 4 对 `producer + external_batch_id`
建立唯一索引；相同身份不同内容在写入前拒绝。没有外部 ID 时继续按来源类型和
哈希去重；文件来源仍可强制导入并关联原批次。

### relation_observations

记录每条 user_id + group_id 关系首次和最近出现的导入批次。它只更新
最近观察位置，不会因后续数据源缺少该成员而删除关系。

### search_index_state 与 member_search

`search_index_state` 是 schema 2 新增的单例状态表，记录索引格式、状态、关系
数和更新时间。`member_search` 是 contentful FTS5 trigram 虚拟表，一条
成员—群组关系对应一个搜索文档；它复制群名和关系字段以支持字段限定与任意
字段包含匹配。两者都是可重建的派生结构，不作为成员历史的权威来源。

## 导入一致性

导入分为三个阶段：

1. 由 xlsx 或 JSON 适配器完整读取、校验并生成 `ParsedRecords`；格式错误时
   不会打开数据库写入。
2. 在一个数据库事务中写入群组、成员和关系。任意写入失败时整体回滚。
3. 业务内容确有变化时，在同一外层事务的 savepoint 中全量重建 FTS5。成功
   时业务与索引共同提交；失败只回滚索引 savepoint、标记状态并启用 LIKE，
   不丢弃已经验证的业务合并。

所有格式随后进入同一个 `import_parsed_records` 入口。同一批数据中的重复
关系先在内存中归并；后出现的非空字段覆盖先前值。写入已有数据库时，新非空
值覆盖旧值，空值不会清除旧值。

项目采用历史聚合语义，而不是当前成员同步语义。任何导入都不会因为某条
关系未出现在新文件中而删除它。

## HTTP 运行模型

FastAPI 路由复用现有导入、搜索、统计和维护函数，不建立第二套业务逻辑。
请求体在内存中校验，不写上传临时文件。Bearer 认证覆盖全部成员数据接口；
存活和就绪探针只公开服务状态。

服务固定单 Uvicorn worker，以进程内锁串行写入。SQLite 使用 WAL 和 30 秒忙
等待支持写入期间的并发读取。若未来需要多个容器或多进程写入，必须先引入
集中写入队列或更换数据库，不能依赖当前进程锁。

## 搜索语义

搜索先找出指定字段（或任意字段）包含关键字的用户 ID，再加载这些用户的
全部 member_group_info 记录。分页以去重后的用户 ID 为单位，因此同一用户
不会因为群组数量不同而占用多条分页名额。输入中的百分号、下划线和反斜杠
会被转义。

schema 2 起使用混合路由：长度至少为 3 且字段不是群 ID 时先用安全引用的 FTS5
trigram `MATCH` 缩小关系候选，再联结权威业务表并执行与原实现相同的已转义
LIKE 条件。该复核消除 FTS5 Unicode 大小写折叠与 SQLite 默认 LIKE 规则之间
的差异。短词和群 ID 直接使用 LIKE；索引状态未就绪、FTS5 运行时不可用或
MATCH 执行失败时，同一次查询也自动回退 LIKE。命中用户后加载全部群组以及
用户分页语义不变；分页 JSON 的 `backend` 会报告本次实际路径。详见
[performance.md](performance.md)。

## 维护与文件写入

健康检查组合使用 SQLite `integrity_check`、外键检查、schema 版本、关系观察
覆盖数量、FTS5 `integrity-check` 以及业务表/索引的逐行内容对照。打开兼容
旧数据库时仍遵循统一迁移流程，因此检查前可能先完成非破坏性升级。

备份使用 SQLite Online Backup API 从只读源连接复制到目标目录的临时文件，
通过完整性检查并关闭连接后再原子替换最终路径。搜索导出采用同样的
“同目录临时文件—原子替换”方式，避免留下看似完整的半成品。

## 数据库兼容

项目只支持 SQLite，并使用 PRAGMA user_version 管理 schema 版本。
Base.metadata.create_all 只负责创建缺失表，既有结构调整必须在
migrations.py 中提供顺序迁移。0.3 的首次升级只新增追踪表，并为原有关系
建立 legacy 基线批次；schema 2 新增搜索索引状态并在运行时支持时初始化
FTS5；schema 3 增加完整来源字段和批次来源元数据；schema 4 增加外部批次
身份和唯一索引。缺少 FTS5 不会阻止数据库升级，搜索保持 LIKE 可用。初始化
会先只读拒绝未来 schema，再执行 `create_all` 和顺序迁移。
