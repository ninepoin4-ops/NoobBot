"""技能系统 — 基类和协议"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class Skill(ABC):
    """技能基类。所有技能继承此类。"""

    # 触发此技能的关键词列表（匹配消息内容）
    triggers: list[str] = []

    # 技能名称
    name: str = ""

    # 技能描述（供调试/列表用）
    description: str = ""

    @abstractmethod
    async def run(self, bot: Any, group_id: int, user_id: int,
                  message: str, params: dict | None = None) -> str | None:
        """
        执行技能。
        
        参数:
            bot: QQBot 实例
            group_id: 群号
            user_id: 用户 QQ
            message: 原始消息
            params: 从消息中提取的参数（如 prompt）
        
        返回:
            要发送到群里的回复文本（None = 不回复）
        """
        ...
