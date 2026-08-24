# 搜索性能基线

## 目的与运行方式

0.5.0 发布前使用实际数据库规模评估分页搜索，而不是凭数据量猜测是否需要
全文索引。基准通过 SQLite 只读 URI 打开已迁移数据库，不执行 schema 迁移，
也不在报告中保存查询关键字、用户 ID、群 ID、昵称或其他成员资料。

~~~powershell
python -m social_database.benchmark `
  --db data/database/members.db `
  --warmups 1 `
  --iterations 5 `
  --page-size 50
~~~

每个场景先预热一次，再记录五次完整 `search_page` 调用。这里的 p95 使用
nearest-rank 计算；五个样本下等于本轮最大值，适合快速回归比较，不作为线上
服务 SLA。

## 2026-08-24 基线

- 系统：Windows、CPython 3.14.7。
- SQLite：3.50.4，已编译 `ENABLE_FTS5`。
- 数据库：28,151,808 字节，210 个群、66,038 个成员、83,580 条关系。
- schema：1。
- 运行参数：预热 1 次、计时 5 次、每页 50 个用户。
- 昵称长度：中位数 4、p95 11、最大值 36；同时测量典型值与最长值。
- 运行前后数据库 SHA-256 相同。

| 场景 | 字段 | 词长 | 命中用户 | 中位数 | p95 |
| --- | --- | ---: | ---: | ---: | ---: |
| user_id_selective | user_id | 10 | 1 | 13.916 ms | 14.739 ms |
| group_id_broad | group_id | 9 | 2,894 | 18.392 ms | 19.402 ms |
| group_name_broad | group_name | 15 | 2,894 | 45.018 ms | 47.775 ms |
| nickname_typical_selective | nickname | 4 | 1 | 369.042 ms | 429.284 ms |
| any_typical_selective | any | 4 | 1 | 638.890 ms | 673.170 ms |
| nickname_long_selective | nickname | 36 | 1 | 439.297 ms | 493.862 ms |
| any_long_selective | any | 36 | 1 | 600.225 ms | 707.078 ms |
| any_miss_typical | any | 4 | 0 | 602.100 ms | 657.236 ms |

初始工程目标是字段限定查询 p95 不超过 250 ms、任意字段查询 p95 不超过
500 ms。ID 和群字段满足目标；典型长度与最长昵称都不满足字段目标，任意字段
查询也全部超标，且未命中查询仍需扫描全部候选字段。

## FTS5 决策

0.5.0 引入 FTS5 trigram 搜索路径，但不会直接删除现有 `LIKE` 实现：

1. 长度至少为 3 个 Unicode 字符的查询进入 trigram 索引候选路径。
2. 少于 3 个字符的查询继续使用当前 `LIKE`，因为 trigram 无法直接匹配短词。
3. FTS5 不可用、索引未就绪或兼容性检查失败时回退到 `LIKE`。
4. `MATCH` 只缩小关系候选；候选必须联结权威业务表并通过原有已转义 LIKE
   条件复核，保持 SQLite 默认的 ASCII/Unicode 大小写语义。
5. 索引查询必须与当前字段过滤、通配符转义和“命中用户后返回全部群组”语义
   保持结果一致。
6. 采用 schema 迁移、可重建流程和健康检查保证索引不会与业务表静默失步。

当前查询为保护 `%`、`_` 和反斜杠而使用 `LIKE ... ESCAPE`。SQLite 官方文档
说明 trigram 不能直接优化带 `ESCAPE` 子句的 `LIKE`，因此先使用安全构造
的 FTS5 `MATCH` 缩小候选，再对较小候选集执行原 LIKE 条件；原查询同时保留
为完整回退。外部内容表还要求应用确保索引与内容表一致，正式设计必须提供
初始化重建和持续同步，而不能只创建触发器。

参考：[SQLite FTS5 trigram 与外部内容表](https://www.sqlite.org/fts5.html)。

## 2026-08-24 隔离原型结果

原型通过 SQLite `mode=ro` 打开源数据库，把 83,580 条成员—群组关系流式
复制到系统临时目录中的 contentful FTS5 trigram 表。原型退出后删除临时
数据库；运行前后源库 SHA-256 均为
`70617AF170C16B7855F133320AAD2CF3AEBAB293816B2FF1DCC8DF4480A543F8`。

- 构建时间：1,790.275 ms。
- 临时索引文件：40,181,760 字节，约为 28,151,808 字节源库的 1.43 倍。
- 参数：预热 1 次、计时 5 次。
- 语义：8 个长度至少为 3 的场景，其完整去重用户 ID 集合全部与 `LIKE`
  相同；2 字符昵称场景按设计只走 `LIKE`。
- 隐私：报告未包含查询词、用户 ID、群 ID、昵称或其他成员资料。

下表只测“找出匹配用户 ID 集合”阶段，不含随后加载用户全部群组资料，
因此不能与上一节完整 `search_page` 基线直接比较绝对耗时。

| 场景 | 字段 | LIKE 中位数 | FTS5 中位数 | FTS5 p95 | 结果 |
| --- | --- | ---: | ---: | ---: | --- |
| user_id_selective | user_id | 6.044 ms | 0.346 ms | 0.400 ms | 一致 |
| group_id_broad | group_id | 8.791 ms | 9.862 ms | 10.176 ms | 一致 |
| group_name_broad | group_name | 22.662 ms | 10.358 ms | 10.656 ms | 一致 |
| nickname_typical_selective | nickname | 192.785 ms | 0.167 ms | 0.355 ms | 一致 |
| any_typical_selective | any | 254.829 ms | 0.111 ms | 0.140 ms | 一致 |
| nickname_long_selective | nickname | 243.709 ms | 0.493 ms | 0.646 ms | 一致 |
| any_long_selective | any | 280.067 ms | 1.520 ms | 1.845 ms | 一致 |
| any_miss_typical | any | 282.339 ms | 0.095 ms | 0.148 ms | 一致 |
| nickname_short_fallback | nickname | 206.545 ms | — | — | LIKE 回退 |

结论是进入“混合接入”阶段，而不是把所有字段统一切到 FTS5：

1. 少于 3 个字符继续走 `LIKE`。
2. 群 ID 的现有查询已经很快，本轮 FTS5 反而慢约 12%，继续走 `LIKE`。
3. group_name、nickname 和 any 进入 FTS5 候选；user_id 可在正式完整分页
   基准后决定是否切换。
4. contentful 原型文件明显大于源库。正式 schema 设计应在兼容受支持的
   SQLite 版本前提下比较 contentful、contentless 或映射表方案，并保证
   导入事务、重建和健康检查的一致性。

## 2026-08-24 schema 2 正式接入结果

正式实现采用兼容面更广的 contentful FTS5 表。schema 1 数据库迁移前先用
SQLite Online Backup API 创建一致性备份，随后迁移至 schema 2：

- 210 个群、66,038 个成员、83,580 条关系和 83,580 条观察记录全部保留。
- FTS5 状态为 `ready`，内部完整性通过，83,580 个索引文档与业务表逐行一致。
- 数据库从 28,151,808 增至 66,629,632 字节，增加 38,477,824 字节。
- 完整分页基准前后 SHA-256 均为
  `D009EBFE9201755ADB7FD97CD53BBB610DC0736BD05CD61C9FA2E38A38F0FB2F`。

下表使用与初始基线完全相同的 1 次预热、5 次计时和 50 用户分页，测量完整
`search_page`，包括匹配、计数、分页以及加载命中用户的全部群组。

| 场景 | 后端 | 初始 p95 | schema 2 初版 p95 | LIKE 复核后 p95 |
| --- | --- | ---: | ---: | ---: |
| user_id_selective | FTS5 | 14.739 ms | 3.921 ms | 6.420 ms |
| group_id_broad | LIKE | 19.402 ms | 19.906 ms | 20.950 ms |
| group_name_broad | FTS5 | 47.775 ms | 17.375 ms | 37.800 ms |
| nickname_typical_selective | FTS5 | 429.284 ms | 3.131 ms | 4.850 ms |
| any_typical_selective | FTS5 | 673.170 ms | 3.492 ms | 4.900 ms |
| nickname_long_selective | FTS5 | 493.862 ms | 5.046 ms | 5.110 ms |
| any_long_selective | FTS5 | 707.078 ms | 4.433 ms | 6.180 ms |
| any_miss_typical | FTS5 | 657.236 ms | 2.897 ms | 5.060 ms |

所有完整分页场景均达到既定交互目标，因此 schema 2 在索引就绪时默认启用
混合路由。正式路径在 `MATCH` 后对业务表执行 LIKE 复核，避免 FTS5 的
Unicode 大小写折叠产生额外命中。少于 3 个字符、群 ID、索引未就绪和 MATCH
执行异常仍保留 LIKE 回退。导入仅在业务搜索内容变化或索引未就绪时，于同一
外层事务的 savepoint 中全量重建；健康检查会同时验证 FTS5 内部结构及其与
业务表的完整内容。

## FTS5 实现验收目标

- 已在临时数据库上逐场景比较 FTS5 候选复核与 `LIKE` 的用户集合，覆盖字段
  过滤、短词、`%`、`_`、反斜杠、双引号、Unicode 和非 ASCII 大小写。
- 已满足匹配阶段 nickname p95 不超过 200 ms、any p95 不超过 250 ms。
- 已满足匹配阶段 user_id、group_id 和 group_name p95 不高于 100 ms。
- 已验证导入后索引同步；业务事务回滚会同时回滚索引，索引 savepoint 失败
  则业务数据保留并自动回退 LIKE。
- 已重跑完整分页基准、记录正式数据库体积并确认索引就绪时默认启用。
