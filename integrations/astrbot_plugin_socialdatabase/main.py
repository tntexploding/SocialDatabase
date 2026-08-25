"""AstrBot plugin entry point for explicit OneBot group collection."""

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .batch import create_group_batch
from .queue_store import QueueItem, QueueStore
from .settings import PluginSettings
from .uploader import QueueWorker, UploadClient, UploadResult

PLUGIN_NAME = "astrbot_plugin_socialdatabase"


class Main(Star):
    """Collect one group per batch and deliver it through a durable queue."""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.settings = PluginSettings.from_mapping(config)
        plugin_name = getattr(self, "name", PLUGIN_NAME)
        data_root = (
            Path(get_astrbot_data_path()) / "plugin_data" / plugin_name
        )
        self.store = QueueStore(data_root)
        self.client = UploadClient(self.settings)
        self.worker = QueueWorker(
            self.store,
            self.client,
            self.settings,
            observer=self._observe_upload,
            error_observer=self._observe_worker_error,
        )
        self._worker_task: asyncio.Task | None = None
        self._last_upload = "尚无上传尝试"

    def _observe_upload(
        self,
        item: QueueItem,
        result: UploadResult,
    ) -> None:
        batch_id = item.payload.get("batch_id", item.queue_id)
        self._last_upload = (
            f"{result.disposition} / "
            f"{result.status_code or '-'} / {result.detail[:200]}"
        )
        if result.disposition == "success":
            logger.info(f"SocialDatabase 批次 {batch_id} 已确认")
        elif result.disposition == "reject":
            logger.error(
                f"SocialDatabase 批次 {batch_id} 已移入 rejected: "
                f"{result.detail}"
            )
        else:
            logger.warning(
                f"SocialDatabase 批次 {batch_id} 将重试: "
                f"{result.detail}"
            )

    @staticmethod
    def _observe_worker_error(error: Exception) -> None:
        logger.error(
            f"SocialDatabase 队列轮询异常，将在下个周期重试: "
            f"{type(error).__name__}: {error}"
        )

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        """Start the retry worker only after AstrBot has initialized."""

        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(
                self.worker.run_forever(),
                name="socialdatabase-upload-queue",
            )

    def _routing_parameters(self, event: AstrMessageEvent) -> dict[str, Any]:
        self_id = getattr(event.message_obj, "self_id", None)
        return {"self_id": self_id} if self_id else {}

    async def _call_onebot(
        self,
        event: AstrMessageEvent,
        action: str,
        **parameters,
    ) -> Any:
        return await asyncio.wait_for(
            event.bot.call_action(
                action,
                **parameters,
                **self._routing_parameters(event),
            ),
            timeout=self.settings.request_timeout_seconds,
        )

    @staticmethod
    def _onebot_group_id(group_id: Any) -> int | str:
        text = str(group_id).strip()
        return int(text) if text.isdigit() else text

    async def _collect_group(
        self,
        event: AstrMessageEvent,
        group_id: Any,
        *,
        known_group_name: Any = None,
    ) -> tuple[str, int, int]:
        onebot_group_id = self._onebot_group_id(group_id)
        group_name = known_group_name
        if group_name is None:
            info = await self._call_onebot(
                event,
                "get_group_info",
                group_id=onebot_group_id,
                no_cache=self.settings.no_cache,
            )
            if not isinstance(info, Mapping):
                raise ValueError("get_group_info 未返回对象")
            group_name = info.get("group_name")
        members = await self._call_onebot(
            event,
            "get_group_member_list",
            group_id=onebot_group_id,
        )
        if not isinstance(members, Sequence) or isinstance(
            members,
            (str, bytes),
        ):
            raise ValueError("get_group_member_list 未返回数组")
        member_objects = [
            member for member in members if isinstance(member, Mapping)
        ]
        payload, skipped = create_group_batch(
            group_id=group_id,
            group_name=group_name,
            members=member_objects,
            producer=self.settings.producer,
        )
        skipped += len(members) - len(member_objects)
        self.store.enqueue(payload)
        self.worker.wake()
        return payload["batch_id"], len(payload["records"]), skipped

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.command("socialdb_collect")
    async def collect_current_group(self, event: AstrMessageEvent):
        """采集当前群成员并持久化为一个待发送批次。"""

        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("请在群聊中执行此命令。")
            return
        try:
            batch_id, records, skipped = await self._collect_group(
                event,
                group_id,
            )
        except Exception as exc:
            logger.exception("SocialDatabase 当前群采集失败")
            yield event.plain_result(f"采集失败：{type(exc).__name__}: {exc}")
            return
        yield event.plain_result(
            f"已持久化批次 {batch_id}：{records} 条有效记录，"
            f"跳过 {skipped} 条；后台将自动上传。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.command("socialdb_collect_all")
    async def collect_all_groups(self, event: AstrMessageEvent):
        """逐群采集机器人已加入的全部群，每群生成一个独立批次。"""

        try:
            groups = await self._call_onebot(event, "get_group_list")
        except Exception as exc:
            logger.exception("SocialDatabase 群列表读取失败")
            yield event.plain_result(
                f"读取群列表失败：{type(exc).__name__}: {exc}"
            )
            return
        if not isinstance(groups, Sequence) or isinstance(groups, (str, bytes)):
            yield event.plain_result("读取群列表失败：接口未返回数组。")
            return

        queued = records = skipped = failed = 0
        for group in groups:
            if not isinstance(group, Mapping) or not group.get("group_id"):
                failed += 1
                continue
            try:
                _, group_records, group_skipped = await self._collect_group(
                    event,
                    group["group_id"],
                    known_group_name=group.get("group_name"),
                )
            except Exception:
                failed += 1
                logger.exception(
                    f"SocialDatabase 群 {group.get('group_id')} 采集失败"
                )
                continue
            queued += 1
            records += group_records
            skipped += group_skipped
        yield event.plain_result(
            f"采集完成：已持久化 {queued} 个群、{records} 条记录，"
            f"跳过 {skipped} 条，失败 {failed} 个群。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.command("socialdb_flush")
    async def flush_queue(self, event: AstrMessageEvent):
        """忽略退避时间，立即处理一轮待发送批次。"""

        result = await self.worker.run_once(include_deferred=True)
        yield event.plain_result(
            f"本轮尝试 {result.attempted} 个批次："
            f"成功 {result.sent}，待重试 {result.retried}，"
            f"拒绝 {result.rejected}。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.command("socialdb_status")
    async def queue_status(self, event: AstrMessageEvent):
        """显示不包含令牌的队列与最近上传状态。"""

        counts = self.store.counts()
        token_state = "已配置" if self.settings.api_token else "未配置"
        yield event.plain_result(
            f"SocialDatabase 队列：pending={counts['pending']}，"
            f"rejected={counts['rejected']}；令牌{token_state}；"
            f"最近状态：{self._last_upload}"
        )

    async def terminate(self):
        """Stop retry work and release the shared HTTP session."""

        self.worker.stop()
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        await self.client.close()
