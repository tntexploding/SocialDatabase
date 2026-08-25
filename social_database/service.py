"""HTTP 服务配置与 Uvicorn 启动入口。"""

import os
from dataclasses import dataclass
from pathlib import Path

from .config import DB_PATH

DEFAULT_MAX_REQUEST_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_RECORDS = 200_000


def _environment_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是整数") from exc


def _environment_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"环境变量 {name} 必须是布尔值")


@dataclass(frozen=True)
class ServiceSettings:
    """服务启动和请求边界配置。"""

    db_path: str
    api_token: str
    host: str = "127.0.0.1"
    port: int = 8000
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    max_records: int = DEFAULT_MAX_RECORDS
    docs_enabled: bool = True
    access_log: bool = False
    previous_api_token: str | None = None

    def __post_init__(self) -> None:
        if not self.api_token or len(self.api_token) < 16:
            raise ValueError("HTTP API 令牌至少需要 16 个字符")
        previous = (self.previous_api_token or "").strip() or None
        if previous is not None and len(previous) < 16:
            raise ValueError("HTTP API 旧令牌至少需要 16 个字符")
        object.__setattr__(self, "previous_api_token", previous)
        if not self.host.strip():
            raise ValueError("HTTP 监听地址不能为空")
        if self.port < 1 or self.port > 65535:
            raise ValueError("HTTP 端口必须在 1 到 65535 之间")
        if self.max_request_bytes < 1:
            raise ValueError("HTTP 请求大小限制必须大于 0")
        if self.max_records < 1:
            raise ValueError("HTTP 批次记录数限制必须大于 0")

    @property
    def accepted_api_tokens(self) -> tuple[str, ...]:
        """Return current and temporary previous tokens for rotation."""

        if (
            self.previous_api_token is None
            or self.previous_api_token == self.api_token
        ):
            return (self.api_token,)
        return (self.api_token, self.previous_api_token)

    @classmethod
    def from_environment(
        cls,
        *,
        db_path: str | Path | None = None,
        host: str | None = None,
        port: int | None = None,
        docs_enabled: bool | None = None,
        access_log: bool | None = None,
    ) -> "ServiceSettings":
        """读取环境变量，并允许 CLI 对非敏感选项进行覆盖。"""

        token = os.getenv("SOCIAL_DATABASE_API_TOKEN", "").strip()
        previous_token = os.getenv(
            "SOCIAL_DATABASE_PREVIOUS_API_TOKEN",
            "",
        ).strip() or None
        resolved_db = str(
            db_path
            if db_path is not None
            else os.getenv("SOCIAL_DATABASE_DB_PATH", DB_PATH)
        )
        resolved_host = (
            host
            if host is not None
            else os.getenv("SOCIAL_DATABASE_HOST", "127.0.0.1")
        )
        resolved_port = (
            port
            if port is not None
            else _environment_int("SOCIAL_DATABASE_PORT", 8000)
        )
        resolved_docs = (
            docs_enabled
            if docs_enabled is not None
            else _environment_bool("SOCIAL_DATABASE_DOCS", True)
        )
        resolved_access_log = (
            access_log
            if access_log is not None
            else _environment_bool("SOCIAL_DATABASE_ACCESS_LOG", False)
        )
        return cls(
            db_path=resolved_db,
            api_token=token,
            previous_api_token=previous_token,
            host=resolved_host,
            port=resolved_port,
            max_request_bytes=_environment_int(
                "SOCIAL_DATABASE_MAX_REQUEST_BYTES",
                DEFAULT_MAX_REQUEST_BYTES,
            ),
            max_records=_environment_int(
                "SOCIAL_DATABASE_MAX_RECORDS",
                DEFAULT_MAX_RECORDS,
            ),
            docs_enabled=resolved_docs,
            access_log=resolved_access_log,
        )


def run_service(settings: ServiceSettings) -> None:
    """以前台单进程模式运行 HTTP 服务。"""

    try:
        import uvicorn
        from .api import create_app
    except ImportError as exc:
        raise RuntimeError(
            "HTTP 服务依赖未安装；请安装 social-database[server]"
        ) from exc

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        workers=1,
        access_log=settings.access_log,
    )
