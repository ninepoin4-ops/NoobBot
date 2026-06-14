"""全局状态 — 避免循环引用"""
from __future__ import annotations
from src.engine.scheduler import Scheduler

scheduler_instance: Scheduler | None = None
# 注意：曾经的 current_group_id 已移除——在并发消息处理下它会读到错误群的 ID。
# 工具若需要当前群上下文，必须从 tool_context() 获取（ContextVar 已正确隔离）。
