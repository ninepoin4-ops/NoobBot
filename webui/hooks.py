"""事件钩子辅助 — 给 Bot 核心代码用的薄封装。

设计目的：让 bot.py / client.py / activator.py 里的 emit 调用只有一行，
且永远不会抛异常影响主流程。如果 event_bus 不可用（如 WebUI 被禁用），
所有 emit 静默无操作。
"""
from __future__ import annotations
from webui.events import event_bus


async def emit(event_type: str, **data):
    """异步发射事件（在 async 函数里用）。"""
    await event_bus.emit(event_type, data)


def emit_sync(event_type: str, **data):
    """同步发射事件（在同步函数里用，如 activator 内部）。"""
    event_bus.emit_nowait(event_type, data)
