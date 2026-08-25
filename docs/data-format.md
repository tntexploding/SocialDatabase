# Excel 数据格式

## 文件要求

- 文件扩展名必须是 .xlsx。
- 程序读取工作簿中的全部工作表。
- 每个工作表的第一行是表头。
- 所有工作表都必须包含完整的必要表头。
- schema 3 已知扩展列存在时会写入数据库。
- 仍可包含未知额外列，未知列不会写入数据库。

## 必要列

| 列名 | 是否允许空值 | 写入位置 |
| --- | --- | --- |
| group_id | 数据行不允许 | groups、member_group_info |
| user_id | 数据行不允许 | members、member_group_info |
| nickname | 允许 | member_group_info |
| card | 允许 | member_group_info |
| join_time | 允许 | member_group_info |
| last_sent_time | 允许 | member_group_info |
| title | 允许 | member_group_info |
| group_name | 允许 | groups |

## 可选扩展列

以下列来自现有 AstrBot/OneBot 工作簿。它们全部写入
`member_group_info`，但缺少任意扩展列表头不会阻止旧工作簿导入。

| 列名 | 写入语义 |
| --- | --- |
| sex | 最近观察到的非空来源值 |
| age | 最近观察到的非空来源值 |
| area | 最近观察到的非空来源值 |
| level | 最近观察到的非空群成员等级 |
| qq_level | 最近观察到的非空 QQ 等级 |
| title_expire_time | 最近观察到的非空头衔到期值 |
| unfriendly | 最近观察到的非空标记 |
| card_changeable | 最近观察到的非空标记 |
| is_robot | 最近观察到的非空标记 |
| shut_up_timestamp | 最近观察到的非空禁言截止值 |
| role | 最近观察到的非空群角色 |

表头缺列属于文件格式错误，会终止整次导入。某一数据行缺少 group_id 或
user_id 时只跳过该行。

## 值转换

- 空单元格转换为 null。
- 字符串会去除首尾空白。
- 日期和时间转换为 ISO 格式文本。
- 其他单元格通过字符串形式保存。

时间字段目前作为文本存储和搜索，不执行时区换算或日期范围查询。

数字和布尔扩展值也转换为稳定文本。这样可以忠实保留不同数据生产方的原始
表达，并保持现有“空值不覆盖”规则；结构化筛选可在服务化阶段另行增加。

## 标准 JSON v1

外部适配器可以输出 UTF-8 JSON：

~~~json
{
  "schema_version": 1,
  "producer": "astrbot",
  "batch_id": "astrbot-20260825-001",
  "source_name": "optional-batch-name",
  "observed_at_utc": "2026-08-25T02:00:00Z",
  "records": [
    {
      "group_id": "example-group",
      "user_id": "example-user",
      "nickname": "Example",
      "role": "member",
      "group_name": "Example Group"
    }
  ]
}
~~~

- `schema_version` 当前必须为 `1`。
- `producer` 标识数据生产方，不等同于导入机器或文件名。
- `batch_id` 可省略；推荐由生产方为每次真实采集生成稳定且不重复的标识，最长
  128 个字符。
- `observed_at_utc` 必须是带时区的 ISO 8601 时间，入库时统一换算为 UTC。
- `source_name` 可省略，默认使用 JSON 文件名。
- `records` 使用与 xlsx 相同的 19 个已知字段；每条有效记录必须具有
  `group_id` 和 `user_id`。
- 缺少任一 ID 的记录按数据质量问题跳过；非对象记录和不支持的格式版本会
  拒绝整个批次。
- 提供 `batch_id` 时，`producer + batch_id` 相同的请求按同一批次处理；若其
  内容不同则拒绝，不能使用 `--force` 绕过身份冲突。
- 文件导入未提供 `batch_id` 时按文件 SHA-256 跳过；HTTP 导入则使用不受键
  顺序和空白影响的规范化 JSON SHA-256。

机器可读契约见 [import-batch-v1.schema.json](import-batch-v1.schema.json)。
AstrBot 插件可以继续生成兼容 xlsx，也可以生成此 JSON；核心程序不导入或
依赖 AstrBot 包。插件侧批次组织与重试约定见
[astrbot-adapter.md](astrbot-adapter.md)。

## 合并规则

- group_id 相同：视为同一群组，最近的非空 group_name 生效。
- user_id 相同：视为同一成员。
- user_id 与 group_id 同时相同：视为同一条群成员关系。
- 重复关系中的新非空字段覆盖旧字段；新空值不删除已有信息。
- 新数据源中没有出现的历史成员或群组关系保持不变。
- 内容相同的 xlsx 默认根据 SHA-256 跳过，可使用 --force 强制处理。
- 首次和最近观察时间优先采用来源提供的采集时间；未提供时使用导入时间。

项目不把新文件视为完整成员快照，不执行退群成员删除。未来即使增加
在群状态，也应使用状态或观察批次表达，而不是硬删除历史关系。

## 文件存放

真实 Excel 文件放在 data/input/。此目录内容受 .gitignore 保护，不应强制
加入 Git。自动化测试不得使用真实成员数据，应在 pytest 临时目录生成最小
工作簿。
