"""OneBot 协议数据模型 + Bot 内部模型"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from enum import Enum


# ── OneBot v11 事件 ──

@dataclass
class Sender:
    # user_id 给默认值 0：某些 OneBot 事件的 sender 字段可能缺 user_id，
    # 用 Sender(**dict) 解构时会 TypeError，默认值让它能优雅降级
    user_id: int = 0
    nickname: str = ""
    card: str = ""  # 群名片
    role: str = "member"  # owner / admin / member

@dataclass
class GroupMessage:
    """群消息"""
    post_type: str = "message"
    message_type: str = "group"
    self_id: int = 0
    group_id: int = 0
    user_id: int = 0
    sender: Sender | None = None
    raw_message: str = ""
    message_id: int = 0
    message_seq: int = 0
    time: int = 0

@dataclass
class PrivateMessage:
    """私聊消息"""
    post_type: str = "message"
    message_type: str = "private"
    self_id: int = 0
    user_id: int = 0
    raw_message: str = ""
    message_id: int = 0
    time: int = 0


# ── Bot 内部模型 ──

class ReplyAction(Enum):
    REPLY = "reply"
    SILENT = "silent"
    DELAYED = "delayed"

@dataclass
class EngagementDecision:
    """活跃引擎的决策结果"""
    should_reply: bool = False
    reason: str = ""
    priority: int = 0       # 越高越应该回复
    action: ReplyAction = ReplyAction.SILENT
    delay: float = 0.0      # 延迟回复的秒数

@dataclass
class ConversationTurn:
    """一轮对话"""
    role: str               # user / assistant / system
    content: str
    time: float = 0.0
    metadata: dict = field(default_factory=dict)

@dataclass
class MemoryEntry:
    """一条长期记忆"""
    id: str = ""
    content: str = ""
    timestamp: float = 0.0
    group_id: int = 0
    user_id: int = 0
    metadata: dict = field(default_factory=dict)
    embedding: list[float] | None = None

@dataclass
class ToolCall:
    """工具调用"""
    name: str
    arguments: dict[str, Any]
    result: str = ""
