# HTTP API

## 安装与启动

HTTP 组件是可选依赖：

~~~powershell
python -m pip install -r requirements-server.txt
$env:SOCIAL_DATABASE_API_TOKEN = "replace-with-a-random-token-at-least-16-chars"
python -m social_database serve
~~~

令牌只从环境变量读取，不提供命令行参数，避免出现在进程参数中。服务默认监听
`127.0.0.1:8000`、启用 `/docs`，并关闭 Uvicorn 访问日志。成员数据接口都要求：

~~~text
Authorization: Bearer <SOCIAL_DATABASE_API_TOKEN>
~~~

常用环境变量：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SOCIAL_DATABASE_API_TOKEN` | 无 | 必填，至少 16 个字符 |
| `SOCIAL_DATABASE_DB_PATH` | 项目默认数据库 | SQLite 路径 |
| `SOCIAL_DATABASE_HOST` | `127.0.0.1` | 监听地址 |
| `SOCIAL_DATABASE_PORT` | `8000` | 监听端口 |
| `SOCIAL_DATABASE_MAX_REQUEST_BYTES` | `67108864` | JSON 请求最大字节数 |
| `SOCIAL_DATABASE_MAX_RECORDS` | `200000` | 单批最大记录数 |
| `SOCIAL_DATABASE_DOCS` | `true` | 是否提供 `/docs` |
| `SOCIAL_DATABASE_ACCESS_LOG` | `false` | 是否开启可能包含查询词的访问日志 |

CLI 可以用 `serve --db`、`--host`、`--port`、`--no-docs` 和 `--access-log`
覆盖非敏感选项。服务固定使用一个 Uvicorn worker；不要用外部命令启动多个
worker 共同写同一个 SQLite 文件。

## 接口

无需认证且不返回数据库信息：

- `GET /health/live`：进程存活。
- `GET /health/ready`：启动迁移和 WAL 初始化已经完成。

需要 Bearer 令牌：

- `GET /api/v1/health`：完整 SQLite、外键、观察记录和 FTS5 检查。
- `GET /api/v1/stats`：数据库规模与最近批次。
- `GET /api/v1/imports?limit=20`：最近导入批次。
- `GET /api/v1/search?q=<关键词>&field=any&page=1&page_size=50`：按用户分页搜索。
- `POST /api/v1/imports/json`：直接提交标准 JSON v1 对象。

HTTP 响应不包含服务端数据库路径。完整健康检查可能遍历 FTS5 内容，只用于
运维诊断；容器探针使用轻量的 `/health/ready`。

## 导入示例

~~~powershell
$headers = @{
    Authorization = "Bearer $env:SOCIAL_DATABASE_API_TOKEN"
}
Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8000/api/v1/imports/json `
    -Headers $headers `
    -ContentType "application/json" `
    -InFile data/input/batch.json
~~~

推荐每次采集提供不重复的 `batch_id`。相同 `producer + batch_id` 和相同内容
再次提交返回 200 且 `duplicate=true`；相同身份对应不同内容返回 409。没有
`batch_id` 时，服务使用与 JSON 键顺序和空白无关的规范化 SHA-256 去重。新批次
成功创建返回 201。

其他常见状态码：

- 400：JSON 格式或批次契约错误。
- 401：令牌缺失或错误。
- 413：请求体超过配置限制。
- 415：请求不是 JSON 媒体类型。
- 422：查询参数或搜索字段错误。
- 503：数据库不可用或完整健康检查未通过。

## 运行边界

- 导入请求在接收过程中累计计算大小，超过配置限制时立即终止；JSON 解码和
  数据库合并在线程池执行，不阻塞异步服务循环，也不保存上传副本。
- 进程内写锁串行处理批次；读取可以由 FastAPI 线程池并发执行。
- SQLite 使用 WAL、30 秒忙等待和单 worker。长导入仍是同步请求，调用方应设置
  合理超时并使用同一 `batch_id` 安全重试。
- 默认访问日志关闭，因为标准 Uvicorn 访问日志会包含搜索查询参数。
- Bearer 令牌不替代 HTTPS。公网部署必须通过反向代理提供 TLS，并限制直接
  访问容器端口。
