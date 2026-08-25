# SocialDatabase AstrBot 插件

本目录是可独立复制到 AstrBot 的 `astrbot_plugin_socialdatabase` 插件。它只支持
OneBot v11 的 `aiocqhttp` 适配器，并且只在管理员执行命令时采集；后台任务只
负责重试已经持久化的批次，不会自行定时获取群成员。

## 安装

1. 使用 AstrBot 4.17 或更高的 4.x 版本，并配置可用的 `aiocqhttp` 平台。
2. 把整个 `astrbot_plugin_socialdatabase` 目录复制到 AstrBot 的
   `data/plugins/` 下；不要只复制 `main.py`。
3. 在 AstrBot WebUI 重载插件。AstrBot 会依据本目录的 `requirements.txt`
   安装 `aiohttp`。
4. 在插件配置中填写 SocialDatabase 的 `server_url` 和 `api_token`。
   公网服务必须使用 HTTPS。

批次队列位于 AstrBot 的
`data/plugin_data/astrbot_plugin_socialdatabase/`，不会写入插件源码目录，
因此插件更新或重装不会覆盖待发送数据。请把该数据目录纳入 AstrBot 的备份。

## 管理员命令

- `/socialdb_collect`：采集当前群，每群生成一个 JSON v1 批次。
- `/socialdb_collect_all`：顺序采集机器人已加入的全部群，每群独立入队。
- `/socialdb_flush`：忽略退避时间，立即尝试当前队列中的一轮批次。
- `/socialdb_status`：显示 pending、rejected、令牌是否已配置及最近上传状态；
  不显示令牌值。

采集命令在返回成功前先把批次原子写入 `pending/`。同一文件在网络超时、401、
403、429 或服务端故障后继续使用原 `producer + batch_id`，不会生成新的观察
批次。服务返回 200/201 后删除本地 pending 文件，不额外制造成功归档；服务端
导入历史是成功记录的权威来源。

400、409、413、415 和 422 表示该批次不能靠原样重试恢复，会移动到
`rejected/` 并保留错误。修正原因后应基于原始采集事实人工生成新批次；不要
直接修改已被服务接受过的稳定批次 ID 对应内容。

## 数据语义

插件只提交本次 OneBot 返回的成员。SocialDatabase 对数据做非破坏合并：新批次
没有出现某个历史成员，不代表退群，不会删除关系或清空最后已知资料。当前插件
也不生成成员状态或完整快照声明。

## 令牌轮换

服务端先同时接受新、旧令牌后，再把插件 `api_token` 更新为新值并重载插件。
执行 `/socialdb_flush`，确认 pending 归零后，服务端撤销旧令牌。若顺序操作失误，
401 只会触发持久重试，不会丢弃批次。
