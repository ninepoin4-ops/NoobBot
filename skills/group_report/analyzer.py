"""分析器 — 调用 LLM 分析话题/称号/金句/质量"""
from __future__ import annotations
import asyncio
import json
import re
from datetime import datetime
from loguru import logger

from skills.group_report.models import (
    SummaryTopic, UserTitle, GoldenQuote, QualityReview, QualityDimension,
    GroupReport, AnalyzedMessage,
)
from skills.group_report.statistics import (
    compute_statistics, analyze_user_activity, get_top_users,
    build_user_summaries, prepare_messages_for_llm,
)
from skills.group_report.prompts import (
    TOPIC_ANALYSIS_PROMPT, USER_TITLE_PROMPT,
    GOLDEN_QUOTE_PROMPT, CHAT_QUALITY_PROMPT,
)


class GroupAnalyzer:
    """群聊分析器 — 纯数据统计 + LLM 分析"""

    def __init__(self, llm_client):
        self.llm = llm_client
        self.bot_self_ids: list[int] = []

    def set_bot_self_ids(self, ids: list[int]):
        self.bot_self_ids = ids

    async def analyze(
        self, messages: list[AnalyzedMessage], group_id: int
    ) -> GroupReport | None:
        """完整分析流程"""
        if not messages:
            return None

        # 1. 基础统计
        stats = compute_statistics(messages)
        user_activity = analyze_user_activity(messages, self.bot_self_ids)
        top_users = get_top_users(user_activity)

        date_str = datetime.fromtimestamp(messages[0].timestamp).strftime("%Y-%m-%d")

        # 2. 并发执行 LLM 分析
        msg_text = prepare_messages_for_llm(messages)
        user_data_list = build_user_summaries(user_activity)
        # 注意：reply_ratio 字段尚未实现可靠检测，暂不提供给 LLM，避免误导
        user_data_text = "\n".join(
            f"- {u['name']} (ID:{u['user_id']}): 发言{u['message_count']}条, "
            f"平均{u['avg_chars']}字, 表情比例{u['emoji_ratio']}, "
            f"夜间比例{u['night_ratio']}"
            for u in user_data_list
        )

        results = await asyncio.gather(
            self._analyze_topics(msg_text, max_topics=8),
            self._analyze_quotes(msg_text, max_quotes=5),
            self._analyze_chat_quality(msg_text),
            self._analyze_user_titles(user_data_text),
            return_exceptions=True,
        )

        topics = self._unwrap_result(results[0], list, [])
        quotes = self._unwrap_result(results[1], list, [])
        quality = self._unwrap_result(results[2], (QualityReview, type(None)), None)
        titles = self._unwrap_result(results[3], list, [])

        # 质量锐评失败时给一个明确占位，避免渲染空白让用户困惑
        if quality is None:
            quality = QualityReview(
                title="今日群聊",
                subtitle="（锐评生成失败）",
                dimensions=[],
                summary="LLM 分析本次未返回结果，下次再试~",
            )

        # 3. 回填信息
        id_to_name = {}
        for msg in messages:
            if str(msg.sender_id) not in id_to_name:
                id_to_name[str(msg.sender_id)] = msg.sender_name

        # 回填称号到 top_users
        title_map = {t.user_id: t for t in titles}
        for user in top_users:
            if user["user_id"] in title_map:
                user["title"] = title_map[user["user_id"]].title

        # 回填金句发送者昵称
        for q in quotes:
            if q.user_id in id_to_name:
                q.sender = id_to_name[q.user_id]

        # 回填话题 contributors 昵称
        for t in topics:
            names = []
            for cid in t.contributor_ids:
                if cid in id_to_name:
                    names.append(id_to_name[cid])
            if names:
                t.contributors = names

        return GroupReport(
            group_id=group_id,
            date=date_str,
            total_messages=stats["total_messages"],
            total_characters=stats["total_characters"],
            participant_count=stats["participant_count"],
            most_active_period=stats["most_active_period"],
            peak_hours=stats["peak_hours"],
            user_activity_ranking=top_users,
            topics=topics,
            user_titles=titles,
            golden_quotes=quotes,
            quality_review=quality,
        )

    @staticmethod
    def _unwrap_result(result, expected_type, default):
        if isinstance(result, Exception):
            logger.warning(f"LLM 分析任务异常: {result}")
            return default
        if isinstance(result, expected_type):
            return result
        return default

    async def _call_llm_json(self, prompt: str) -> dict | None:
        """调用 LLM，期望 JSON 返回"""
        messages = [
            {
                "role": "system",
                "content": "你是一个群聊数据分析专家。严格按要求的 JSON 格式返回，不要有多余的文字。",
            },
            {"role": "user", "content": prompt},
        ]
        try:
            text = await self.llm.chat_raw(messages, temperature=0.3, max_tokens=4096)
            json_str = self._extract_json(text)
            if json_str:
                return json.loads(json_str)
            logger.warning(f"LLM 返回非JSON: {text[:200]}")
            return None
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            return None

    @staticmethod
    def _extract_json(text: str) -> str | None:
        """从文本中抽取第一个 JSON 块（支持多重嵌套）"""
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if m:
            return m.group(1).strip()
        # 从第一个 { 到 raw_decode 定位的结束位置，精确切片
        start = text.find("{")
        if start == -1:
            return None
        try:
            end = json.JSONDecoder().raw_decode(text, start)[1]
            return text[start:end].strip()
        except (json.JSONDecodeError, ValueError):
            pass
        # 不再用贪婪正则 r'\{[\s\S]*\}' 兜底：它会吞掉两个 JSON 之间的闲聊，
        # 反而引入解析失败。raw_decode 已是最可靠的边界定位，失败就放弃。
        return None

    async def _analyze_topics(self, msg_text: str, max_topics: int = 8) -> list[SummaryTopic]:
        prompt = TOPIC_ANALYSIS_PROMPT.format(messages=msg_text[:12000], max_topics=max_topics)
        data = await self._call_llm_json(prompt)
        if not data or "topics" not in data:
            return []
        results = []
        for t in data["topics"]:
            contributors = t.get("contributors", [])
            results.append(SummaryTopic(
                topic=t.get("topic", "未知话题"),
                contributors=contributors,
                detail=t.get("detail", ""),
                contributor_ids=contributors,
            ))
        return results

    async def _analyze_quotes(self, msg_text: str, max_quotes: int = 5) -> list[GoldenQuote]:
        prompt = GOLDEN_QUOTE_PROMPT.format(messages=msg_text[:12000], max_quotes=max_quotes)
        data = await self._call_llm_json(prompt)
        if not data or "quotes" not in data:
            return []
        results = []
        for q in data["quotes"]:
            # prompt 现在要求返回 user_id（数字 ID）；旧版字段 sender 兼容
            raw_uid = q.get("user_id") or q.get("sender") or ""
            # 归一化为字符串，便于后续与 id_to_name 的 key（数字字符串）匹配
            try:
                uid_str = str(int(raw_uid)) if str(raw_uid).strip() else str(raw_uid)
            except (TypeError, ValueError):
                uid_str = str(raw_uid)
            results.append(GoldenQuote(
                content=q.get("content", ""),
                sender=str(raw_uid),  # 暂存，回填时替换成昵称；匹配失败保留原值
                reason=q.get("reason", ""),
                user_id=uid_str,
            ))
        return results

    async def _analyze_chat_quality(self, msg_text: str) -> QualityReview | None:
        prompt = CHAT_QUALITY_PROMPT.format(messages=msg_text[:12000])
        data = await self._call_llm_json(prompt)
        if not data:
            return None
        colors = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7", "#DDA0DD", "#98D8C8", "#F7DC6F"]
        dimensions = [
            QualityDimension(
                name=d.get("name", ""),
                percentage=d.get("percentage", 0),
                comment=d.get("comment", ""),
                color=colors[i % len(colors)],
            )
            for i, d in enumerate(data.get("dimensions", []))
        ]
        return QualityReview(
            title=data.get("title", "今日群聊"),
            subtitle=data.get("subtitle", ""),
            dimensions=dimensions,
            summary=data.get("summary", ""),
        )

    async def _analyze_user_titles(self, user_data_text: str) -> list[UserTitle]:
        if not user_data_text.strip():
            return []
        prompt = USER_TITLE_PROMPT.format(user_data=user_data_text[:8000])
        data = await self._call_llm_json(prompt)
        if not data or "users" not in data:
            return []
        return [
            UserTitle(
                name=u.get("name", ""),
                user_id=u.get("user_id", ""),
                title=u.get("title", ""),
                mbti=u.get("mbti", ""),
                reason=u.get("reason", ""),
            )
            for u in data["users"]
        ]
