# SocialDatabase 与 AstrBot 插件真实环境联合调试

本文用于验证以下正式版本在真实 AstrBot、OneBot 和 HTTP 环境中的完整数据流：

- SocialDatabase `v0.8.0`
- `astrbot_plugin_socialdatabase` `v0.8.1`
- AstrBot `>=4.17,<5`
- OneBot v11 `aiocqhttp` 平台适配器

联合调试只验证管理员按需采集、持久队列、HTTP 上传、幂等导入和非破坏合并。
插件不会自动定时采集，SocialDatabase 也不会根据一次批次中缺少成员推断退群。

## 使用规则

1. 首次调试使用成员较少、允许测试的群，不要直接执行全群采集。
2. 在仓库外复制本文件并填写结果；不要修改已检出的版本或把测试记录提交到 Git。
3. 不在记录、截图或问题报告中包含 API 令牌、完整请求体、成员资料、真实群号或
   完整 `batch_id`。需要关联批次时只保留末尾 8 个字符。
4. 服务端调试前执行一致性备份；队列文件只读查看，不直接编辑或删除。
5. 临时提取的 JSON 只放在系统临时目录，完成幂等验证后立即删除。

## 环境记录

请在仓库外的副本中填写：

| 项目 | 结果 |
| --- | --- |
| 调试时间和时区 |  |
| SocialDatabase 版本 | `v0.8.0` |
| 插件版本 | `v0.8.1` |
| AstrBot 版本 |  |
| aiocqhttp/OneBot 实现与版本 |  |
| SocialDatabase 部署方式 | Python / Docker / 生产 Compose |
| AstrBot 部署方式 |  |
| 服务地址（只记录域名，不含令牌） |  |
| 测试群成员数量级 |  |
| 调试负责人 |  |

测试结果统一填写 `PASS`、`FAIL` 或 `BLOCKED`。失败时保留发生时间、脱敏错误和
复现步骤，不要反复采集制造更多批次。

## 1. 服务端基线

### 1.1 确认版本和容器状态

在 SocialDatabase 主机执行：

~~~bash
git describe --tags --exact-match
docker compose --env-file deploy/production.env \
  -f deploy/compose.production.yaml ps
~~~

如果不是生产 Compose 部署，只需记录实际启动命令和 `v0.8.0` 标签。预期服务
只有一个 API 容器/进程写入 SQLite，不使用多个 Uvicorn worker 共享数据库。

### 1.2 创建调试前备份

生产 Compose：

~~~bash
docker compose --env-file deploy/production.env \
  -f deploy/compose.production.yaml exec social-database \
  social-database backup --db /data/members.db
~~~

非容器部署：

~~~bash
social-database backup --db /path/to/members.db
~~~

记录备份成功信息，并按现有运维流程将备份复制到容器卷和项目目录之外。不要在
服务运行时直接复制或覆盖 `members.db`。

### 1.3 从 AstrBot 所在主机检查 HTTP

以下命令从 Caddy 允许的 AstrBot 出口执行。令牌通过无回显输入读取，不写入
脚本或 shell 历史：

~~~bash
export SOCIALDB_URL="https://database.example.com"
read -r -s -p "SocialDatabase API token: " SOCIALDB_TOKEN
echo
curl --fail --silent --show-error "$SOCIALDB_URL/health/live"
curl --fail --silent --show-error "$SOCIALDB_URL/health/ready"
curl --fail --silent --show-error \
  -H "Authorization: Bearer $SOCIALDB_TOKEN" \
  "$SOCIALDB_URL/api/v1/stats"
curl --fail --silent --show-error \
  -H "Authorization: Bearer $SOCIALDB_TOKEN" \
  "$SOCIALDB_URL/api/v1/imports?limit=5"
~~~

预期：两个探针返回 200，受认证接口返回 200，统计中的 `schema_version` 为 4。
记录调试前的 `groups`、`members`、`relations`、`relation_observations` 和
`import_batches` 计数，不记录最近批次的成员或群标识。

## 2. 插件安装、启动和热重载

1. 在 AstrBot WebUI 使用以下仓库地址安装插件：

   ~~~text
   https://github.com/tntexploding/astrbot_plugin_socialdatabase
   ~~~

2. 确认 WebUI 显示插件版本 `0.8.1`，平台适配器是 `aiocqhttp`。
3. 配置 `server_url`、`api_token` 和稳定的 `producer`；`server_url` 只填根地址，
   不附加 `/api/v1/imports/json`。
4. 重载插件，在管理员会话执行 `/socialdb_status`。
5. 再执行一次 WebUI 热重载并重复 `/socialdb_status`。

预期：两次状态命令都能响应；令牌显示“已配置”但不显示值；初次安装时
`pending=0`、`rejected=0`；日志没有重复后台任务、未关闭会话或导入错误。

## 3. 单群正常链路

1. 在选定的测试群中，以 AstrBot 管理员身份执行 `/socialdb_collect`。
2. 只执行一次并等待命令返回。记录返回的记录数、跳过数，以及脱敏后的批次
   ID 末 8 位。
3. 执行 `/socialdb_status`；如仍有 pending，等待一个重试间隔后再检查一次，
   必要时只执行一次 `/socialdb_flush`。
4. 调用 `/api/v1/imports?limit=5` 和 `/api/v1/stats`。

预期：

- 采集命令先完成持久入队，再提示后台上传。
- 最终 `pending=0`、`rejected=0`，最近上传状态为 HTTP 200 或 201 成功。
- 服务端最新批次的 `producer` 与配置一致，`external_batch_id` 末 8 位与插件
  返回一致，`source_rows` 等于插件报告的有效记录数。
- `groups`、`members`、`relations` 和 `relation_observations` 只增不减。

## 4. 服务中断、持久队列和热重载恢复

本步骤再次使用同一个测试群，只产生一个新批次。

1. 暂停 SocialDatabase API，保留数据库卷和 Caddy：

   ~~~bash
   docker compose --env-file deploy/production.env \
     -f deploy/compose.production.yaml stop social-database
   ~~~

2. 执行一次 `/socialdb_collect`，再执行 `/socialdb_status`。
3. 确认 `pending>=1` 且 `rejected=0`。记录 pending 数量，不提交队列内容。
4. 在 WebUI 热重载插件，再次执行 `/socialdb_status`，确认 pending 数量未减少。
   如果准备执行第 5 节，请在继续恢复服务前先按该节说明只读提取 payload。
5. 启动服务并等待 `/health/ready`：

   ~~~bash
   docker compose --env-file deploy/production.env \
     -f deploy/compose.production.yaml start social-database
   ~~~

6. 执行一次 `/socialdb_flush`，随后执行 `/socialdb_status`。
7. 调用最近导入接口，确认该批次只出现一次。

预期：服务中断和插件热重载都不会丢失或拒绝批次；服务恢复后原
`producer + batch_id` 上传成功，最终 pending 归零。此步骤同时验证插件
`initialize()` 会在热重载后重新启动上传任务。

## 5. 幂等重放

只有能够安全读取 AstrBot 插件数据目录时执行本步骤。在第 4 步恢复服务前，将
一个 pending 队列文件中的 `payload` 只读提取到系统临时目录；不要修改原文件：

~~~bash
JOINT_TMP="$(mktemp -d)"
PENDING_FILE='<AstrBot-data-root>/plugin_data/astrbot_plugin_socialdatabase/pending/<file>.json'
python -c \
  'import json,sys; print(json.dumps(json.load(open(sys.argv[1], encoding="utf-8"))["payload"], ensure_ascii=False))' \
  "$PENDING_FILE" >"$JOINT_TMP/payload.json"
~~~

待插件正常上传并清空 pending 后，原样重放一次：

~~~bash
curl --silent --show-error --output "$JOINT_TMP/response.json" \
  --write-out '%{http_code}\n' \
  -H "Authorization: Bearer $SOCIALDB_TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary "@$JOINT_TMP/payload.json" \
  "$SOCIALDB_URL/api/v1/imports/json"
rm -rf -- "$JOINT_TMP"
unset SOCIALDB_TOKEN
~~~

预期 HTTP 状态为 200，响应包含 `duplicate=true`；`import_batches`、关系和观察
记录计数不增加。若无法访问队列目录，将本项记为 `BLOCKED`，不要为了测试直接
编辑队列或制造冲突批次。

## 6. 全群采集

仅在单群、离线恢复和幂等测试通过后执行一次 `/socialdb_collect_all`。

预期：命令报告的成功群数、记录数、跳过数和失败群数合理；每个群形成独立批次；
最终 pending 归零且 rejected 不增加。若群数量较多，使用 `/socialdb_status`
观察自然排空，不频繁执行 `/socialdb_flush`。

## 7. 非破坏性历史保留

只在专用测试群和测试账号上执行，不要求真实成员为了测试退群。

1. 第一次采集时确认测试账号存在，并通过受认证搜索接口按 `user_id` 查询其群
   关系。
2. 让该测试账号暂时离开测试群，再采集同一群一次。
3. 再次查询同一 `user_id`。

查询时从终端读取账号，不把值写入脚本：

~~~bash
read -r -p "Test user ID: " TEST_USER_ID
curl --fail --silent --show-error --get \
  -H "Authorization: Bearer $SOCIALDB_TOKEN" \
  --data-urlencode "q=$TEST_USER_ID" \
  --data-urlencode "field=user_id" \
  "$SOCIALDB_URL/api/v1/search"
unset TEST_USER_ID
~~~

预期：第二个批次可以缺少该账号，但原成员—群组关系仍存在，最后已知资料不被
清空，`relations` 和 `relation_observations` 总数不减少。若没有可安全退出的测试
账号，将本项记为 `BLOCKED`，不要在正式群制造成员变更。

## 8. 最终健康、备份与结果

1. 确认 `/socialdb_status` 为 `pending=0`、`rejected=0`。
2. 调用 `/api/v1/health`，预期返回 200 且 `healthy=true`。
3. 记录最终统计计数并再执行一次一致性备份。
4. 确认两个代码仓库仍位于正式标签且 `git status --short` 无输出。

| 编号 | 验证项 | 结果 | 脱敏证据/备注 |
| --- | --- | --- | --- |
| 1 | 服务探针、认证和 schema 4 |  |  |
| 2 | 插件安装、启动和两次热重载 |  |  |
| 3 | 单群采集、上传和服务端落库 |  |  |
| 4 | 服务中断、持久队列和恢复 |  |  |
| 5 | 相同批次幂等重放 |  |  |
| 6 | 全群逐批采集 |  |  |
| 7 | 批次缺席不删除历史关系 |  |  |
| 8 | 最终健康检查与备份 |  |  |

## 失败反馈最小集合

发生失败后停止重复采集，提供以下脱敏信息即可继续定位：

- 上表环境版本和失败编号。
- `/socialdb_status` 的完整文字；其中不应包含令牌。
- 失败前后 2 分钟内的插件与服务端错误日志，删除成员资料、群号、URL 查询词和
  Authorization 内容。
- HTTP 状态码、发生时间，以及批次 ID 末 8 位。
- `/health/ready`、`/api/v1/health` 和统计计数，不提供数据库文件或真实 JSON。

不要发送 API 令牌、pending/rejected 文件、数据库、原始 OneBot 响应或未脱敏
截图。需要进一步复现时，再单独生成最小匿名批次。
