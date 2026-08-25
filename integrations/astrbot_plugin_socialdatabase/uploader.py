"""Async HTTP delivery and retry scheduling for queued batches."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .queue_store import QueueItem, QueueStore
from .settings import PluginSettings

Sender = Callable[
    [str, str, dict[str, Any], int, bool],
    Awaitable[tuple[int, str]],
]
Observer = Callable[[QueueItem, "UploadResult"], None]
ErrorObserver = Callable[[Exception], None]

SUCCESS_STATUSES = {200, 201}
REJECT_STATUSES = {400, 409, 413, 415, 422}


@dataclass(frozen=True)
class UploadResult:
    disposition: str
    status_code: int | None
    detail: str


@dataclass(frozen=True)
class CycleResult:
    attempted: int = 0
    sent: int = 0
    retried: int = 0
    rejected: int = 0


class UploadClient:
    """Send one batch, without changing its durable identity."""

    def __init__(
        self,
        settings: PluginSettings,
        *,
        sender: Sender | None = None,
    ) -> None:
        self.settings = settings
        self._sender = sender
        self._session = None

    async def _aiohttp_sender(
        self,
        endpoint: str,
        token: str,
        payload: dict[str, Any],
        timeout_seconds: int,
        no_cache: bool,
    ) -> tuple[int, str]:
        try:
            import aiohttp
        except ImportError as exc:
            raise RuntimeError("插件依赖 aiohttp 未安装") from exc

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if no_cache:
            headers["Cache-Control"] = "no-store"
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with self._session.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=timeout,
        ) as response:
            return response.status, (await response.text())[:1000]

    async def send(self, payload: dict[str, Any]) -> UploadResult:
        if not self.settings.api_token:
            return UploadResult("retry", None, "api_token 尚未配置")
        sender = self._sender or self._aiohttp_sender
        try:
            status, response_text = await sender(
                self.settings.import_endpoint,
                self.settings.api_token,
                payload,
                self.settings.request_timeout_seconds,
                self.settings.no_cache,
            )
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"[:1000]
            return UploadResult("retry", None, detail)

        detail = f"HTTP {status}"
        if response_text:
            detail = f"{detail}: {response_text}"
        if status in SUCCESS_STATUSES:
            return UploadResult("success", status, detail)
        if status in REJECT_STATUSES:
            return UploadResult("reject", status, detail)
        return UploadResult("retry", status, detail)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


class QueueWorker:
    """Process due queue entries serially with persisted exponential backoff."""

    def __init__(
        self,
        store: QueueStore,
        client: UploadClient,
        settings: PluginSettings,
        *,
        observer: Observer | None = None,
        error_observer: ErrorObserver | None = None,
        max_retry_delay_seconds: int = 3600,
    ) -> None:
        self.store = store
        self.client = client
        self.settings = settings
        self.observer = observer
        self.error_observer = error_observer
        self.max_retry_delay_seconds = max_retry_delay_seconds
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._stopping = False

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stopping = True
        self._wake.set()

    def _retry_delay(self, attempts_before_send: int) -> int:
        exponent = min(attempts_before_send, 8)
        return min(
            self.settings.retry_interval_seconds * (2**exponent),
            self.max_retry_delay_seconds,
        )

    async def run_once(self, *, include_deferred: bool = False) -> CycleResult:
        async with self._lock:
            attempted = sent = retried = rejected = 0
            items = self.store.pending_items(
                limit=self.settings.max_attempts_per_cycle,
                include_deferred=include_deferred,
            )
            for item in items:
                result = await self.client.send(item.payload)
                attempted += 1
                if result.disposition == "success":
                    self.store.acknowledge(item)
                    sent += 1
                elif result.disposition == "reject":
                    self.store.reject(item, result.detail)
                    rejected += 1
                else:
                    self.store.retry(
                        item,
                        result.detail,
                        delay_seconds=self._retry_delay(item.attempts),
                    )
                    retried += 1
                if self.observer is not None:
                    try:
                        self.observer(item, result)
                    except Exception:
                        pass
            return CycleResult(attempted, sent, retried, rejected)

    async def run_forever(self) -> None:
        while not self._stopping:
            try:
                await self.run_once()
            except Exception as exc:
                if self.error_observer is not None:
                    try:
                        self.error_observer(exc)
                    except Exception:
                        pass
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=self.settings.retry_interval_seconds,
                )
            except TimeoutError:
                pass
            self._wake.clear()
