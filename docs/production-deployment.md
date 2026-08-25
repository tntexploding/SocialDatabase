# 生产部署与云端联动

`deploy/compose.production.yaml` 提供一套与具体云厂商无关的单机生产基线：
SocialDatabase 只连接内部 Docker 网络，Caddy 是唯一入口，负责自动 HTTPS、
来源 CIDR 限制和反向代理。SQLite 仍保持单容器、单 worker、串行写入。

## 前置条件

- 一台安装 Docker Engine 和 Compose v2 的 Linux 云服务器。
- 一个解析到该服务器公网地址的域名。
- 防火墙允许 80/TCP、443/TCP 和可选的 443/UDP；不要开放 8000。
- AstrBot 出站公网 IP或网段。动态家庭公网地址需要先解决固定出口、VPN 或
  其他可信网络入口，不能把来源限制留空。

Caddy 自动申请证书要求域名解析正确且公网能访问 80/443。示例使用 Caddy
官方镜像的固定稳定版本；升级 Caddy 时先阅读其发行说明并重复配置检查。

## 首次启动

在仓库根目录执行：

~~~bash
cp deploy/production.env.example deploy/production.env
python -c 'import secrets; print(secrets.token_urlsafe(32))'
~~~

编辑不进入 Git 的 `deploy/production.env`：

- `SOCIAL_DATABASE_DOMAIN` 只填写域名，例如 `database.example.com`。
- `SOCIAL_DATABASE_ALLOWED_CIDRS` 填 AstrBot 出口，例如
  `198.51.100.24/32`；多个网段用空格分隔。
- `SOCIAL_DATABASE_API_TOKEN` 填刚生成的随机值。
- `SOCIAL_DATABASE_PREVIOUS_API_TOKEN` 首次部署保持空白。

先解析配置，再启动：

~~~bash
docker compose --env-file deploy/production.env \
  -f deploy/compose.production.yaml config
docker compose --env-file deploy/production.env \
  -f deploy/compose.production.yaml build
docker compose --env-file deploy/production.env \
  -f deploy/compose.production.yaml up -d
docker compose --env-file deploy/production.env \
  -f deploy/compose.production.yaml ps
~~~

从允许的来源检查：

~~~bash
curl --fail https://database.example.com/health/ready
curl --fail \
  -H "Authorization: Bearer <current-token>" \
  https://database.example.com/api/v1/stats
~~~

不在允许范围内的请求由 Caddy 返回 403。API 容器没有宿主机端口映射，即使
反代配置出错也不会直接暴露 8000。

## 来源地址与前置代理

默认 `Caddyfile` 的 `remote_ip` 匹配 TCP 连接的直接来源，因此默认架构要求
Caddy 是公网第一跳。若前面增加 Cloudflare、负载均衡或其他 CDN，Caddy 看到
的是代理地址：此时必须只信任供应商公布的代理网段，配置 Caddy
`trusted_proxies`，再把匹配器改为 `client_ip`。不要仅凭任意请求携带的
`X-Forwarded-For` 放行。

来源网段变化后编辑 `production.env` 并重建 Caddy 容器，使环境变量重新载入：

~~~bash
docker compose --env-file deploy/production.env \
  -f deploy/compose.production.yaml up -d --force-recreate caddy
~~~

## 无停机令牌轮换

假设当前令牌为 `OLD`，新生成令牌为 `NEW`：

1. 在服务端环境中把 `SOCIAL_DATABASE_API_TOKEN` 改为 `NEW`，把
   `SOCIAL_DATABASE_PREVIOUS_API_TOKEN` 改为 `OLD`，只重建 API 容器。
2. 分别用新旧令牌调用 `/api/v1/stats`，确认过渡窗口生效。
3. 在 AstrBot 插件中把 `api_token` 改为 `NEW` 并重载插件。
4. 执行 `/socialdb_flush` 和 `/socialdb_status`，确认 pending 为 0。
5. 清空服务端 `SOCIAL_DATABASE_PREVIOUS_API_TOKEN`，再次重建 API 容器。
6. 确认新令牌仍成功、旧令牌返回 401。

重建 API 容器的命令：

~~~bash
docker compose --env-file deploy/production.env \
  -f deploy/compose.production.yaml up -d --force-recreate social-database
~~~

过渡窗口只用于短期轮换，不应长期保留旧令牌。插件会把 401/403 视为可恢复
配置问题并保留原批次，所以错误顺序不会造成采集数据丢失。

## 备份、升级与恢复

升级前先创建 SQLite 一致性备份：

~~~bash
docker compose --env-file deploy/production.env \
  -f deploy/compose.production.yaml exec social-database \
  social-database backup --db /data/members.db
~~~

命名卷能跨容器重建保留数据，但不能替代异机备份。使用 `docker cp` 或云平台
备份工具，把 `/data/backups/` 中的新备份复制到服务器仓库外和另一存储位置，
再执行升级：

~~~bash
docker compose --env-file deploy/production.env \
  -f deploy/compose.production.yaml build --pull
docker compose --env-file deploy/production.env \
  -f deploy/compose.production.yaml up -d
~~~

等待 healthy 后检查受认证健康接口。恢复时必须先停止写入，复制备份为仓库外
候选，在隔离容器或临时卷中执行 `social-database check`；确认 schema、SQLite、
外键、关系观察和 FTS5 均健康后，才替换正式库并重新检查。不要在服务运行时
覆盖 `members.db`，也不要删除正式卷来“重试”失败升级。文件级恢复的完整顺序
见 [operations.md](operations.md)。

## 当前容量决策

0.8.0 的 AstrBot 侧已经有持久待发送队列，服务端继续同步接收并使用单写锁。
这能提供明确的成功确认和稳定幂等重试，同时避免引入第二套尚无实际负载依据的
任务系统。只有实际监控显示持续排队、请求超时或写入吞吐不足时，才评估集中
服务端任务队列或更换数据库；不得通过多个 API 容器共同写一个 SQLite 文件来
扩容。
