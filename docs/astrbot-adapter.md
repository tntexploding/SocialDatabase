# AstrBot 数据适配约定

## 边界

SocialDatabase 不加载 AstrBot、不调用机器人接口，也不管理机器人凭据。AstrBot
插件只负责采集和生成批次；本项目负责校验、去重、非破坏合并、检索与维护。
两者可以通过兼容 xlsx 或标准 JSON v1 协作，手动 xlsx 始终保留为后备路径。

## 推荐 JSON 方式

一次采集运行生成一个 UTF-8 JSON 对象：

- `schema_version` 固定为 `1`。
- `producer` 使用能识别插件或适配器的稳定名称。
- `batch_id` 推荐使用插件一次采集运行的稳定 ID；重试必须复用，新的采集必须
  生成新值。
- `source_name` 建议使用插件侧批次名称；省略时使用文件名。
- `observed_at_utc` 是本批数据实际采集时间，必须携带时区。
- `records` 可以包含多个群，每条记录使用标准字段名。

字段从 AstrBot/OneBot 输出按名称直接映射：group_id、user_id、nickname、
card、sex、age、area、level、qq_level、join_time、last_sent_time、
title_expire_time、unfriendly、card_changeable、is_robot、
shut_up_timestamp、role、title、group_name。未知字段可保留在 JSON 中，但当前
核心不会入库。

完整结构见 [import-batch-v1.schema.json](import-batch-v1.schema.json)。插件应在
本地先写入临时文件，再原子改名为最终 `.json`，避免 SocialDatabase 读到未
完成的批次。

## xlsx 兼容方式

现有插件可以继续输出每群一个工作表的 xlsx。八个兼容表头必须存在，11 个
扩展表头可选；存在时全部保存。导入时建议补充来源信息：

~~~powershell
python -m social_database import data/input/batch.xlsx `
  --producer astrbot `
  --observed-at 2026-08-25T10:00:00+08:00
~~~

## 合并与重试

- 同一来源类型下，内容 SHA-256 相同的文件默认只导入一次。
- HTTP/JSON 重试优先使用 `producer + batch_id`；相同 ID 内容变化会返回冲突，
  插件必须保留原批次或生成代表新采集的新 ID。
- 需要把同一文件作为新的观察批次时显式使用 `--force`。
- 同批重复的 user_id + group_id 先合并，后出现的非空字段优先。
- 新批次的空值不清除旧值。
- 某关系未出现在批次中不表示退群，不更新其最近观察批次，也绝不删除它。
- 未来只有插件明确提供成员状态或完整快照范围时，才能增加状态事件；该能力
  不属于 JSON v1。

## 调用示例

~~~powershell
python -m social_database import-json data/input/batch.json
python -m social_database imports --limit 5 --format text
python -m social_database check --format text
~~~

服务模式下把同一个 JSON 对象 POST 到 `/api/v1/imports/json`，并携带
`Authorization: Bearer <token>`。成功新建返回 201，安全重复返回 200，身份
冲突返回 409。插件应在网络失败或超时时以相同 `batch_id` 重试，并在本地保留
未确认批次；不要因为一次 HTTP 超时立刻生成新 ID。

导入成功后，`imports` 会显示生产方、格式版本、采集时间和导入时间；搜索与
导出会显示每条关系的首次和最近观察批次及时间。
