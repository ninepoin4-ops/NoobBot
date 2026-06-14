"""OneBot 协议数据模型 + Bot 内部模型"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from enum import Enum


# ── OneBot v11 事件 ──

@dataclass
class Sender:
    # 只保留 Bot 实际用到的字段。OneBot v11 上报的 sender 还含
    # sex/age/area/level/title 等，用 Sender(**dict) 解构会 TypeError，
    # 所以一律走 from_dict() 显式取字段，忽略未声明字段。
    user_id: int = 0
    nickname: str = ""
    card: str = ""  # 群名片
    role: str = "member"  # owner / admin / member

    @classmethod
    def from_dict(cls, d: dict | None) -> "Sender | None":
        """从 OneBot sender dict 安全构造，忽略未声明字段。"""
        if not d:
            return None
        try:
            return cls(
                user_id=int(d.get("user_id", 0) or 0),
                nickname=str(d.get("nickname", "") or ""),
                card=str(d.get("card", "") or ""),
                role=str(d.get("role", "member") or "member"),
            )
        except (TypeError, ValueError):
            return None

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
