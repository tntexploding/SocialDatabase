# 搜索性能基线

## 目的与运行方式

0.5.0 开发阶段使用实际数据库规模评估分页搜索，而不是凭数据量猜测是否需要
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

0.5.0 将引入 FTS5 trigram 搜索路径，但不会直接删除现有 `LIKE` 实现：

1. 长度至少为 3 个 Unicode 字符的查询进入 trigram 索引候选路径。
2. 少于 3 个字符的查询继续使用当前 `LIKE`，因为 trigram 无法直接匹配短词。
3. FTS5 不可用、索引未就绪或兼容性检查失败时回退到 `LIKE`。
4. 索引查询必须与当前字段过滤、通配符转义和“命中用户后返回全部群组”语义
   保持结果一致。
5. 采用 schema 迁移、可重建流程和健康检查保证索引不会与业务表静默失步。

当前查询为保护 `%`、`_` 和反斜杠而使用 `LIKE ... ESCAPE`。SQLite 官方文档
说明 trigram 不能优化带 `ESCAPE` 子句的 `LIKE`，因此加速路径应使用安全构造
的 FTS5 `MATCH` 查询，原查询仅作为回退。外部内容表还要求应用确保索引与
内容表一致，正式设计必须提供初始化重建和持续同步，而不能只创建触发器。

参考：[SQLite FTS5 trigram 与外部内容表](https://www.sqlite.org/fts5.html)。

## FTS5 实现验收目标

- 在临时数据库上逐场景比较 FTS5 与 `LIKE` 的用户集合，覆盖字段过滤、短词、
  `%`、`_`、反斜杠和 Unicode。
- 当前数据规模下 nickname p95 不超过 200 ms，any p95 不超过 250 ms。
- user_id、group_id 和 group_name p95 不高于 100 ms。
- 导入后索引同步可验证，失败时不影响业务表事务和历史关系。
- 重跑本页基准并记录索引大小、构建时间和查询结果，再决定是否默认启用。
