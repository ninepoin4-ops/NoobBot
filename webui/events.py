"""进程内事件总线 — WebUI 实时推送的核心基础设施。

设计要点：
- 订阅者用 asyncio.Queue，队列满即丢事件，绝不阻塞 Bot 主流程
- emit() 是 async 的（事件钩子多在 async 上下文调用），内部捕获所有异常
- 提供 emit_nowait() 给同步上下文（如 loguru sink）使用
- 维护最近 N 条事件的历史快照，新订阅者连接时先收到快照
"""
from __future__ import annotations
import asyncio
import time
from collections import deque
from typing import Any

from loguru import logger


# 最近保留多少条历史事件（供新连接的快照）
_HISTORY_SIZE = 100
# 每个订阅者队列容量（满了直接丢最旧）
_QUEUE_MAXSIZE = 500


class EventBus:
    """进程内事件总线。"""

    def __init__(self, history_size: int = _HISTORY_SIZE):
        self._subscribers: set[asyncio.Queue] = set()
        self._history: deque[dict] = deque(maxlen=history_size)
        self._bot: Any = None  # 延迟绑定，用于快照生成
        self._counter: int = 0  # 累计事件数（前端展示用）

    def bind_bot(self, bot):
        """绑定 bot 实例，用于生成连接时的仪表盘快照。"""
        self._bot = bot

    @property
    def bot(self):
        return self._bot

    @property
    def event_count(self) -> int:
        return self._counter

    def subscribe(self) -> asyncio.Queue:
        """订阅事件流，返回一个 Queue。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q)

    async def emit(self, event_type: str, data: dict):
        """异步广播事件。所有异常内部消化，绝不抛给调用方。"""
        try:
            self._dispatch(event_type, data)
        except Exception as e:
            logger.debug(f"event_bus.emit 失败（已忽略）: {e}")

    def emit_nowait(self, event_type: str, data: dict):
        """同步广播事件（用于 loguru sink 等同步回调）。"""
        try:
            self._dispatch(event_type, data)
        except Exception as e:
            logger.debug(f"event_bus.emit_nowait 失败（已忽略）: {e}")

    def _dispatch(self, event_type: str, data: dict):
        event = {
            "type": event_type,
            "data": data,
            "ts": time.time(),
            "seq": self._counter + 1,
        }
        self._counter += 1
        self._history.append(event)

        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # 慢客户端：丢最旧的一条腾位置
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass

    def snapshot(self, limit: int = 30) -> list[dict]:
        """返回最近 N 条历史事件（新连接快速回放）。"""
        items = list(self._history)
        return items[-limit:]


# 全局单例
event_bus = EventBus()
