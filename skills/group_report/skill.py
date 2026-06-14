"""群日报技能 — 注册/生成/发送"""
from __future__ import annotations
import asyncio
import os
from datetime import datetime
from loguru import logger

from skills.base import Skill
from skills.group_report.models import parse_onebot_message
from skills.group_report.analyzer import GroupAnalyzer
from skills.group_report.renderer import generate_report_image


class GroupReportSkill(Skill):
    """每日群聊分析报告"""
    name = "group_report"
    description = "群日报：生成今日群聊话题、称号、金句、质量锐评"
    triggers = ["群日报", "今日总结", "日报", "总结报告"]

    def __init__(self):
        super().__init__()
        self.analyzer: GroupAnalyzer | None = None

    async def run(self, bot, group_id: int, user_id: int,
                  message: str, params: dict | None = None) -> str | None:
        """触发群日报"""
        if not self.analyzer:
            self.analyzer = GroupAnalyzer(bot.llm)
            bot_self_id = bot.config.get("napcat", {}).get("self_id", 0)
            if bot_self_id:
                self.analyzer.set_bot_self_ids([bot_self_id])
            logger.info(f"群日报技能首次初始化，Bot自身ID: {bot_self_id}")

        await self._generate_and_send(bot, group_id)
        return None  # 已通过 napcat 直接发送

    async def _generate_and_send(self, bot, group_id: int) -> None:
        if not self.analyzer:
            await bot.send_group_msg_by_id(group_id, "❌ 分析器未初始化")
            return

        logger.info(f"[群{group_id}] 开始拉取历史消息...")
        await bot.send_group_msg_by_id(group_id, "📊 正在生成今日群聊报告，请稍候...")

        # 1. 拉取历史消息（最多200条）
        all_raw = await bot.napcat.get_group_msg_history(
            group_id=group_id, count=200
        )
        if not all_raw:
            await bot.send_group_msg_by_id(
                group_id, "❌ 拉取消息失败，请确认Bot有查看历史消息的权限"
            )
            return

        # 2. 转换 + 去重 + 按日过滤
        now = datetime.now()
        today_start = int(datetime(now.year, now.month, now.day).timestamp())

        seen_ids = set()
        messages = []
        old_count = 0
        for raw in all_raw:
            mid = raw.get("message_id", 0)
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            ts = raw.get("time", 0)
            if ts < today_start:
                old_count += 1
                continue
            msg = parse_onebot_message(raw)
            if msg and msg.content.strip():
                messages.append(msg)

        logger.info(f"[群{group_id}] 获取 {len(messages)} 条（今日），过滤掉 {old_count} 条历史消息")

        if len(messages) < 10:
            await bot.send_group_msg_by_id(
                group_id, f"📭 消息较少（{len(messages)}条），不足以生成有意义的报告"
            )
            return

        logger.info(f"[群{group_id}] 获取 {len(messages)} 条，开始分析...")

        # 3. 分析
        report = await self.analyzer.analyze(messages, group_id)
        if not report:
            await bot.send_group_msg_by_id(group_id, "❌ 分析失败")
            return

        # 4. 生成图片报告
        logger.info(f"[群{group_id}] 分析完成，生成图片...")
        img_path = await asyncio.to_thread(generate_report_image, report)
        logger.info(f"[群{group_id}] 图片已保存: {img_path}")

        # 5. 发送图片（base64 内嵌，兼容 NapCat）
        import base64
        with open(img_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        cq_img = f"[CQ:image,file=base64://{b64}]"
        await bot.send_group_msg_by_id(group_id, cq_img)

    def _format_text(self, r: "GroupReport") -> str:
        lines = []
        lines.append(f"📊 群聊日报 — {r.date}")
        lines.append(f"{'='*28}")
        lines.append("")
        lines.append(f"📈 数据概览")
        lines.append(f"  总消息: {r.total_messages}条 | 总字符: {r.total_characters}")
        lines.append(f"  发言人数: {r.participant_count}人")
        lines.append(f"  最活跃: {r.most_active_period}")
        lines.append("")

        lines.append("🏆 今日话痨排行榜")
        for i, u in enumerate(r.user_activity_ranking[:8], 1):
            t = u.get("title", "")
            ts = f" [{t}]" if t else ""
            lines.append(f"  {i}. {u['name']}{ts} — {u['message_count']}条")
        lines.append("")

        if r.topics:
            lines.append("💬 今日热门话题")
            for t in r.topics:
                cs = "、".join(t.contributors[:4]) if t.contributors else ""
                c = f" (参与: {cs})" if cs else ""
                lines.append(f"  🔸 {t.topic}{c}")
                if t.detail:
                    lines.append(f"    {t.detail}")
            lines.append("")

        if r.user_titles:
            lines.append("🎭 今日群友画像")
            for t in r.user_titles[:6]:
                lines.append(f"  {t.name}: [{t.title}] ({t.mbti})")
                if t.reason:
                    lines.append(f"    → {t.reason}")
            lines.append("")

        if r.golden_quotes:
            lines.append("🌟 今日金句")
            for q in r.golden_quotes:
                lines.append(f'  💬 "{q.content}"')
                lines.append(f"    — {q.sender} · {q.reason}")
            lines.append("")

        if r.quality_review:
            q = r.quality_review
            lines.append("🎯 聊天质量锐评")
            lines.append(f"  {q.title}")
            if q.subtitle:
                lines.append(f"  {q.subtitle}")
            for d in q.dimensions:
                lines.append(f"  ▸ {d.name} ({d.percentage:.0f}%): {d.comment}")
            lines.append(f"  📌 {q.summary}")

        return "\n".join(lines)

    @staticmethod
    def _split_report(text: str, max_len: int = 1800) -> list[str]:
        if len(text) <= max_len:
            return [text]
        segs = []
        lines = text.split("\n")
        cur = ""
        section_heads = {"🏆", "💬", "🎭", "🌟", "🎯"}
        for line in lines:
            key = line[:1]  # emoji 是单字符
            if key in section_heads and cur:
                segs.append(cur.strip())
                cur = line + "\n"
            elif len(cur) + len(line) > max_len:
                segs.append(cur.strip())
                cur = line + "\n"
            else:
                cur += line + "\n"
        if cur.strip():
            segs.append(cur.strip())
        return segs


skill_instance = GroupReportSkill()
