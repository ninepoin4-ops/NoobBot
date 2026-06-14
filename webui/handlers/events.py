"""WebSocket 实时事件流。

连接时先发送 snapshot（最近历史事件 + 当前仪表盘状态），
之后持续推送 event_bus 广播的事件。
"""
from __future__ import annotations
import asyncio

from aiohttp import web
from loguru import logger

from webui.events import event_bus
from webui.handlers.dashboard import build_dashboard


async def ws_events_handler(request: web.Request) -> web.WebSocketResponse:
    """GET /api/ws — WebSocket 事件流。"""
    ws = web.WebSocketResponse(
        heartbeat=30,  # 30 秒心跳，及时清理死连接
        max_msg_size=0,  # 不限制消息大小（只发送，几乎不接收）
    )
    await ws.prepare(request)
    bot = request.app["bot"]

    q = event_bus.subscribe()
    logger.debug(f"WebUI WebSocket 已连接，订阅者数 {len(event_bus._subscribers)}")

    try:
        # 1. 先发初始快照
        try:
            snapshot = {
                "type": "snapshot",
                "data": {
                    "dashboard": build_dashboard(bot),
                    "recent_events": event_bus.snapshot(limit=30),
                },
            }
            await ws.send_json(snapshot)
        except Exception as e:
            logger.debug(f"发送快照失败: {e}")

        # 2. 持续推送事件
        while not ws.closed:
            try:
                # 用 wait_for 让循环能周期性检查 ws.closed
                event = await asyncio.wait_for(q.get(), timeout=10.0)
                await ws.send_json(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
    finally:
        event_bus.unsubscribe(q)
        logger.debug(f"WebUI WebSocket 已断开，订阅者数 {len(event_bus._subscribers)}")

    return ws
