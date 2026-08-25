# Docker 部署

## 快速启动

复制环境模板并生成至少 16 个字符的随机令牌：

~~~powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
~~~

把生成值写入 `.env` 的 `SOCIAL_DATABASE_API_TOKEN`，然后执行：

~~~powershell
docker compose config
docker compose build
docker compose up -d
docker compose ps
~~~

Compose 默认只把服务映射到宿主机 `127.0.0.1:8000`。数据库保存在命名卷
`social-database-data`，删除或重建容器不会删除数据。`.env`、真实数据库和
输入文件均不进入镜像或 Git。

镜像以非 root 用户运行，根文件系统只读，只有 `/data` 持久卷和 `/tmp`
临时文件系统可写。启动时自动检查并升级受支持的 schema，然后启用 WAL；
`/health/ready` 通过后容器才会标记为健康。

## 运维命令

~~~powershell
docker compose logs --tail 100 social-database
docker compose exec social-database social-database stats --db /data/members.db
docker compose exec social-database social-database check --db /data/members.db
docker compose exec social-database social-database backup --db /data/members.db
docker compose down
~~~

默认备份写入卷内 `/data/backups/`。长期备份还应由宿主机或云平台把该目录复制
到独立存储；单独保留同一卷中的备份不能防止卷丢失。

## 使用已有数据库

生产环境若已有 `members.db`，建议把宿主机仓库外目录（例如
`/srv/socialdatabase`）绑定为 `/data`，并在首次启动容器前把数据库和备份放入
该目录。不要在服务运行时直接覆盖数据库文件。路径替换和候选检查继续遵循
[operations.md](operations.md) 的停写、备份、替换、复查顺序。

## 升级

1. 对当前数据库执行 `backup`，并把备份复制到卷外。
2. 拉取新代码并运行 `docker compose build`。
3. 运行 `docker compose up -d`；启动阶段会执行顺序 schema 迁移。
4. 等待容器健康，再调用受认证的 `/api/v1/health`。
5. 若启动失败，保留日志和数据库，不要反复重建卷；使用升级前备份建立恢复
   候选。

服务固定单 worker。需要多个 API 实例或高写入吞吐量时，应先设计集中写入队列
或更换数据库，不要让多个容器直接写同一个 SQLite 文件。

## 公网部署

Compose 的默认回环绑定适合让宿主机上的 Nginx、Caddy 或其他反向代理转发。
反向代理负责 HTTPS、请求超时和来源访问控制；不要直接把 8000 端口暴露到
公网。令牌通过部署平台密钥或权限受限的 `.env` 注入，并按运维计划轮换。

仓库的 `deploy/compose.production.yaml` 与 `deploy/Caddyfile` 提供可直接检查的
Caddy 基线：API 只加入内部网络，Caddy 自动 HTTPS 并按来源 CIDR 放行；还支持
当前/旧令牌的短期双窗口轮换。域名、云防火墙、可信代理、备份外带和完整命令
见 [production-deployment.md](production-deployment.md)。
