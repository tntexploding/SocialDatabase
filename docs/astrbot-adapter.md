# AstrBot 数据适配约定

## 边界

SocialDatabase 核心包不加载 AstrBot、不调用机器人接口，也不管理机器人凭据。
仓库从 0.8.0 起在 `integrations/astrbot_plugin_socialdatabase/` 独立维护一个
`aiocqhttp` 插件；其 AstrBot 与 aiohttp 依赖不进入核心运行依赖。插件负责按需
采集、持久排队和上传，本项目核心负责校验、幂等、非破坏合并、检索与维护。
手动 xlsx 与 JSON 文件导入始终保留为独立后备路径。

## 0.8.0 内置插件

把完整的 `integrations/astrbot_plugin_socialdatabase` 目录复制到 AstrBot 的
`data/plugins/`，在 WebUI 重载后配置：

- `server_url`：SocialDatabase 根地址，公网必须为 HTTPS。
- `api_token`：与服务当前令牌一致；留空时仍允许采集入队，但不上传。
- `producer`：保持稳定，默认 `astrbot-socialdatabase`。
- HTTP/OneBot 超时、重试间隔和每轮处理上限。

管理员命令：

- `/socialdb_collect`：采集当前群。
- `/socialdb_collect_all`：逐群采集机器人已加入的全部群。
- `/socialdb_flush`：忽略当前退避时间，立即尝试一轮。
- `/socialdb_status`：查看 pending、rejected 和最近状态，不显示令牌。

插件不会定时采集。后台任务只发送已经写入 AstrBot
`data/plugin_data/astrbot_plugin_socialdatabase/` 的批次。完整安装、队列与
故障处理见插件目录的 `README.md`。

## 推荐 JSON 方式

插件按“一群一个批次”生成 UTF-8 JSON 对象，避免单次 HTTP 请求无限增长：

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

完整结构见 [import-batch-v1.schema.json](import-batch-v1.schema.json)。插件先
在 AstrBot 插件数据目录写同目录临时文件并原子替换为 pending 文件，确认 HTTP
成功后才删除；SocialDatabase 不会读到半成品，也不会在仓库中留下上传文件。

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

上传返回 200/201 时安全出队；400、409、413、415、422 转入 `rejected/`；
401、403、429、服务端错误、网络失败和超时保留原 payload 并指数退避。成功
批次不在插件侧反复存档，服务端 `import_batches` 是成功历史的权威来源。

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
