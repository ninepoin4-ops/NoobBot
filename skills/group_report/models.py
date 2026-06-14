"""数据模型：日报分析结果"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from collections import defaultdict
from typing import Any


@dataclass
class AnalyzedMessage:
    """一条待分析的群消息"""
    message_id: int
    sender_id: int
    sender_name: str
    content: str
    timestamp: int


@dataclass
class SummaryTopic:
    """LLM 提取的话题"""
    topic: str
    contributors: list[str]
    detail: str
    contributor_ids: list[str] = field(default_factory=list)


@dataclass
class UserTitle:
    """LLM 生成的用户称号"""
    name: str
    user_id: str
    title: str
    mbti: str
    reason: str


@dataclass
class GoldenQuote:
    """LLM 筛选的金句"""
    content: str
    sender: str
    reason: str
    user_id: str = ""


@dataclass
class QualityDimension:
    """聊天质量的一个维度"""
    name: str
    percentage: float
    comment: str
    color: str = "#607d8b"


@dataclass
class QualityReview:
    """聊天质量锐评"""
    title: str
    subtitle: str
    dimensions: list[QualityDimension]
    summary: str


@dataclass
class UserActivityStats:
    """用户活跃度统计"""
    message_count: int = 0
    char_count: int = 0
    emoji_count: int = 0
    nickname: str = ""
    hours: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    reply_count: int = 0


@dataclass
class GroupReport:
    """完整的群日报"""
    group_id: int
    date: str
    total_messages: int
    total_characters: int
    participant_count: int
    most_active_period: str
    peak_hours: list[int]
    user_activity_ranking: list[dict]
    topics: list[SummaryTopic]
    user_titles: list[UserTitle]
    golden_quotes: list[GoldenQuote]
    quality_review: QualityReview | None = None


def parse_onebot_message(raw: dict) -> AnalyzedMessage | None:
    """从 OneBot 消息 dict 转为 AnalyzedMessage"""
    # 只关心群消息，其余类型直接拒绝
    if raw.get("message_type") != "group":
        return None

    user_id = raw.get("user_id", 0)
    sender = raw.get("sender", {}) or {}
    nickname = sender.get("card", "") or sender.get("nickname", "") or str(user_id)

    # 提取纯文本（去掉 CQ 码）
    raw_msg = raw.get("raw_message", "")
    text = _strip_cq_codes(raw_msg)

    return AnalyzedMessage(
        message_id=raw.get("message_id", 0),
        sender_id=user_id,
        sender_name=nickname,
        content=text,
        timestamp=raw.get("time", 0),
    )


def _strip_cq_codes(text: str) -> str:
    """去掉 CQ 码，仅保留纯文本"""
    # 替换各种 CQ 码
    text = re.sub(r'\[CQ:image,[^\]]*\]', '[图片]', text)
    text = re.sub(r'\[CQ:at[^\]]*\]', '', text)  # 兼容 @all 和 @qq=xxx,name=xxx
    text = re.sub(r'\[CQ:reply,[^\]]*\]', '', text)
    text = re.sub(r'\[CQ:face,[^\]]*\]', '[表情]', text)
    text = re.sub(r'\[CQ:[^\]]*\]', '', text)
    return text.strip()
