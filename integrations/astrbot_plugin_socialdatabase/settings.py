"""Validated configuration for the AstrBot integration."""

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class PluginSettings:
    """Runtime settings loaded from AstrBot's plugin configuration."""

    server_url: str = "http://127.0.0.1:8000"
    api_token: str = ""
    producer: str = "astrbot-socialdatabase"
    request_timeout_seconds: int = 120
    retry_interval_seconds: int = 60
    max_attempts_per_cycle: int = 20
    no_cache: bool = True

    def __post_init__(self) -> None:
        normalized_url = self.server_url.strip().rstrip("/")
        parsed = urlsplit(normalized_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("server_url 必须是有效的 HTTP 或 HTTPS 地址")
        if parsed.query or parsed.fragment:
            raise ValueError("server_url 不能包含查询参数或片段")
        object.__setattr__(self, "server_url", normalized_url)

        token = self.api_token.strip()
        if token and len(token) < 16:
            raise ValueError("api_token 留空或至少使用 16 个字符")
        object.__setattr__(self, "api_token", token)

        producer = self.producer.strip()
        if not producer:
            raise ValueError("producer 不能为空")
        object.__setattr__(self, "producer", producer)

        if self.request_timeout_seconds < 1:
            raise ValueError("request_timeout_seconds 必须大于 0")
        if self.retry_interval_seconds < 1:
            raise ValueError("retry_interval_seconds 必须大于 0")
        if self.max_attempts_per_cycle < 1:
            raise ValueError("max_attempts_per_cycle 必须大于 0")

    @property
    def import_endpoint(self) -> str:
        return f"{self.server_url}/api/v1/imports/json"

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "PluginSettings":
        """Build settings from an AstrBotConfig-compatible mapping."""

        return cls(
            server_url=str(config.get("server_url", cls.server_url)),
            api_token=str(config.get("api_token", cls.api_token)),
            producer=str(config.get("producer", cls.producer)),
            request_timeout_seconds=int(
                config.get(
                    "request_timeout_seconds",
                    cls.request_timeout_seconds,
                )
            ),
            retry_interval_seconds=int(
                config.get(
                    "retry_interval_seconds",
                    cls.retry_interval_seconds,
                )
            ),
            max_attempts_per_cycle=int(
                config.get(
                    "max_attempts_per_cycle",
                    cls.max_attempts_per_cycle,
                )
            ),
            no_cache=bool(config.get("no_cache", cls.no_cache)),
        )
