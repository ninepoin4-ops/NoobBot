"""统计计算 — 从 astrbot 移植，纯 Python 无外部依赖"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime
from skills.group_report.models import (
    AnalyzedMessage, UserActivityStats, GroupReport,
    SummaryTopic, UserTitle, GoldenQuote, QualityReview,
)


def compute_statistics(messages: list[AnalyzedMessage]) -> dict:
    """计算基础群聊统计指标"""
    if not messages:
        return {
            "total_messages": 0,
            "total_characters": 0,
            "participant_count": 0,
            "most_active_period": "无",
            "peak_hours": [],
            "hourly_activity": {},
        }

    hour_counts: dict[int, int] = defaultdict(int)
    participants: set[int] = set()
    total_chars = 0

    for msg in messages:
        participants.add(msg.sender_id)
        dt = datetime.fromtimestamp(msg.timestamp)
        hour_counts[dt.hour] += 1
        total_chars += len(msg.content)

    # 找最活跃时段
    if hour_counts:
        peak_hour = max(hour_counts, key=hour_counts.get)
        end_hour = (peak_hour + 1) % 24
        most_active = f"{peak_hour:02d}:00-{end_hour:02d}:00"
        sorted_hours = sorted(hour_counts.items(), key=lambda x: -x[1])
        peak_hours = [h for h, _ in sorted_hours[:3]]
    else:
        most_active = "无"
        peak_hours = []

    return {
        "total_messages": len(messages),
        "total_characters": total_chars,
        "participant_count": len(participants),
        "most_active_period": most_active,
        "peak_hours": peak_hours,
        "hourly_activity": dict(hour_counts),
    }


def analyze_user_activity(
    messages: list[AnalyzedMessage],
    bot_self_ids: list[int] | None = None,
) -> dict[int, UserActivityStats]:
    """分析每个用户的活跃度"""
    bot_ids = set(bot_self_ids or [])
    users: dict[int, UserActivityStats] = defaultdict(UserActivityStats)

    for msg in messages:
        if msg.sender_id in bot_ids:
            continue
        uid = msg.sender_id
        u = users[uid]
        u.nickname = msg.sender_name
        u.message_count += 1
        u.char_count += len(msg.content)

        dt = datetime.fromtimestamp(msg.timestamp)
        u.hours[dt.hour] += 1

        # 检测 CQ 标记
        if "[图片]" in msg.content:
            u.emoji_count += 1
        if "[表情]" in msg.content:
            u.emoji_count += 1

    return dict(users)


def get_top_users(
    user_activity: dict[int, UserActivityStats],
    limit: int = 10,
) -> list[dict]:
    """按消息数排序取前 N 名"""
    sorted_users = sorted(
        user_activity.items(),
        key=lambda x: x[1].message_count,
        reverse=True,
    )
    result = []
    for uid, stats in sorted_users[:limit]:
        result.append({
            "user_id": str(uid),
            "name": stats.nickname or str(uid),
            "message_count": stats.message_count,
            "char_count": stats.char_count,
            "emoji_count": stats.emoji_count,
            "reply_count": stats.reply_count,
        })
    return result


def build_user_summaries(
    user_activity: dict[int, UserActivityStats],
    top_n: int = 15,
) -> list[dict]:
    """构建 LLM 用户称号分析用的用户摘要"""
    sorted_users = sorted(
        user_activity.items(),
        key=lambda x: x[1].message_count,
        reverse=True,
    )
    summaries = []
    for uid, stats in sorted_users:
        if stats.message_count < 3:
            continue
        avg_chars = round(stats.char_count / stats.message_count, 1) if stats.message_count else 0
        emoji_ratio = round(stats.emoji_count / stats.message_count, 2) if stats.message_count else 0
        night_hours = sum(v for h, v in stats.hours.items() if h < 6)
        night_ratio = round(night_hours / stats.message_count, 2) if stats.message_count else 0
        reply_ratio = round(stats.reply_count / stats.message_count, 2) if stats.message_count else 0

        if len(summaries) >= top_n:
            break
        summaries.append({
            "name": stats.nickname or str(uid),
            "user_id": str(uid),
            "message_count": stats.message_count,
            "avg_chars": avg_chars,
            "emoji_ratio": emoji_ratio,
            "night_ratio": night_ratio,
            "reply_ratio": reply_ratio,
        })
    return summaries


def prepare_messages_for_llm(messages: list[AnalyzedMessage]) -> str:
    """将消息格式化为 LLM 易读格式：[HH:MM] [用户ID]: 内容"""
    lines = []
    for msg in messages:
        dt = datetime.fromtimestamp(msg.timestamp)
        time_str = dt.strftime("%H:%M")
        lines.append(f"[{time_str}] [{msg.sender_id}]: {msg.content}")
    return "\n".join(lines)
