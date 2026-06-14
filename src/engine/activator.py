"""主动活跃引擎 — 决定是否插话"""
from __future__ import annotations
import random
import time
import re
from collections import defaultdict, deque
from dataclasses import replace as dc_replace
from typing import Callable
from loguru import logger

from src.models.schemas import (
    GroupMessage, EngagementDecision, ReplyAction,
)


class Activator:
    """
    回复决策链。链式检查，任一条件通过即决定回复。
    设计来源 ChatLuna 的 allow_reply.ts 决策树。
    """

    def __init__(self, config: dict):
        ec = config["engagement"]
        cc = config["cooldown"]

        self.bot_names = ec["bot_names"]
        self.name_match_mode = ec.get("name_match_mode", "contains")
        self.random_freq = ec["random_reply_frequency"]
        # 群黑名单（默认空 = 全部启用）。group_id 用 int 存储，匹配时统一转 int。
        self.disabled_groups: set[int] = set(
            int(g) for g in ec.get("disabled_groups", []) if g
        )

        # 冷却
        self.global_cooldown = cc["global_cooldown"]
        self.group_cooldown = cc["group_cooldown"]
        self.user_cooldown = cc["user_cooldown"]

        # 限流
        rl = cc["rate_limit"]
        self.rate_window = rl["window"]
        self.rate_max = rl["max_count"]
        self.rate_strategy = rl["strategy"]

        # 运行时状态
        self._last_global_time: float = 0.0
        self._last_group_time: dict[int, float] = {}
        self._last_user_time: dict[int, float] = {}
        self._rate_buckets: dict[int, list[float]] = {}  # group_id -> [timestamps]
        self._recent_bot_messages: dict[int, deque[int]] = defaultdict(
            lambda: deque(maxlen=200)
        )

        # 外部插件可以注册自己的检查器
        self._passive_checks: list[Callable[[GroupMessage], float]] = []

    def register_passive_check(self, fn: Callable[[GroupMessage], float]):
        """注册被动触发检查器，返回优先级 (0=不触发, 越高越可能)"""
        self._passive_checks.append(fn)

    # ── 主决策入口 ──

    async def decide(self, msg: GroupMessage) -> EngagementDecision:
        """
        对一条群消息做出回复决策。
        链式检查，返回 EngagementDecision。
        """
        decision = EngagementDecision(should_reply=False, reason="no_match")

        # 0. 空消息检查
        if not msg.raw_message.strip():
            return decision

        # 1. 套娃检测 (Bot 不回自己)
        if msg.user_id == msg.self_id:
            return decision

        # 2. 冷却检查
        now = time.time()

        # 全局冷却
        if now - self._last_global_time < self.global_cooldown:
            return dc_replace(decision, reason="global_cooldown")

        # 群冷却
        last_group = self._last_group_time.get(msg.group_id, 0)
        if now - last_group < self.group_cooldown:
            return dc_replace(decision, reason="group_cooldown")

        # 用户冷却
        last_user = self._last_user_time.get(msg.user_id, 0)
        if now - last_user < self.user_cooldown:
            return dc_replace(decision, reason="user_cooldown")

        content = msg.raw_message
        content_clean = self._strip_cq_codes(content)

        # 3. @提及检查
        at_pattern = f"[CQ:at,qq={msg.self_id}]"
        if at_pattern in content:
            return self._apply_rate_limit(
                msg, self._make_reply("at_mention", priority=10), now
            )

        # 4. 引用回复检查 (消息中包含 reply)
        if self._is_reply_to_bot(content, msg.group_id):
            # 别人引用了 Bot 的消息
            return self._apply_rate_limit(
                msg, self._make_reply("quote_reply", priority=9), now
            )

        # 5. Bot 名字检查
        name_score = self._check_bot_name(content_clean)
        if name_score > 0:
            return self._apply_rate_limit(
                msg,
                self._make_reply(f"name_match(score={name_score})", priority=8),
                now,
            )

        # 6. 关键词触发 (可配置)
        # TODO: 从配置加载关键词列表

        # 7. 随机回复
        if self.random_freq > 0 and random.random() < self.random_freq:
            return self._apply_rate_limit(
                msg, self._make_reply("random_engagement", priority=3), now
            )

        # 8. 被动检测插件
        for check in self._passive_checks:
            priority = check(msg)
            if priority > 0:
                return self._apply_rate_limit(
                    msg, self._make_reply("passive_trigger", priority=priority), now
                )

        return decision

    # ── 辅助方法 ──

    def _make_reply(self, reason: str, priority: int) -> EngagementDecision:
        return EngagementDecision(
            should_reply=True,
            reason=reason,
            priority=priority,
            action=ReplyAction.REPLY,
        )

    def _apply_rate_limit(
        self, msg: GroupMessage, decision: EngagementDecision, now: float
    ) -> EngagementDecision:
        """只对真正要回复的消息计入限流。

        采用"预留 + 提交"两阶段：decide() 时只判断是否超限，不真正扣额度；
        实际发送成功后由 bot 调用 commit_reply() 提交。
        这样 LLM 失败 / 回复为空被跳过时不会浪费配额。
        """
        if not decision.should_reply:
            return decision

        if self._check_rate_limit(msg.group_id, now, consume=False):
            return decision

        if self.rate_strategy == "discard":
            return EngagementDecision(should_reply=False, reason="rate_limited")

        return dc_replace(
            decision,
            action=ReplyAction.DELAYED,
            delay=max(decision.delay, 1.0),
            reason=f"{decision.reason}+rate_stall",
        )

    def commit_reply(self, group_id: int, now: float | None = None):
        """实际成功发送一条回复后调用，正式扣减限流额度。

        会先清理窗口外的过期时间戳，再校验是否已达上限——避免 scheduler
        主动消息等额外调用方把 bucket 追加到无限大，让限流形同虚设。
        """
        now = now if now is not None else time.time()
        bucket = self._rate_buckets.setdefault(group_id, [])
        # 清理窗口外过期项（与 _check_rate_limit 同一套清理逻辑）
        cutoff = now - self.rate_window
        bucket[:] = [t for t in bucket if t > cutoff]
        # 已达上限则不追加（相当于这条发送不计数，避免超额堆积）；
        # decide() 在返回前已用 consume=False 做过判断，正常不会到这里，
        # 这只是对 scheduler 主动消息等"绕过 decide"路径的防御。
        if len(bucket) >= self.rate_max:
            logger.debug(
                f"群 {group_id} commit_reply 时已达限流上限 {self.rate_max}，"
                "本次发送不计入额度"
            )
            return
        bucket.append(now)

    def remember_bot_message(self, group_id: int, message_id: int | str | None):
        """记录 Bot 发出的消息 ID，用于判断后续 quote reply。"""
        if message_id is None:
            return
        try:
            self._recent_bot_messages[group_id].append(int(message_id))
        except (TypeError, ValueError):
            logger.debug(f"忽略无法识别的消息 ID: {message_id}")

    def _is_reply_to_bot(self, content: str, group_id: int) -> bool:
        match = re.search(r"\[CQ:reply,id=(-?\d+)\]", content)
        if not match:
            return False
        try:
            reply_id = int(match.group(1))
        except ValueError:
            return False
        return reply_id in self._recent_bot_messages.get(group_id, ())

    def _check_bot_name(self, content: str) -> float:
        """检查消息中是否包含 Bot 名称，返回匹配强度 0~1"""
        if not content:
            return 0.0

        if self.name_match_mode == "prefix":
            for name in self.bot_names:
                if content.startswith(name):
                    return 1.0
        else:  # contains
            for name in self.bot_names:
                if name in content:
                    return 0.8  # 包含式匹配强度稍低
        return 0.0

    def _check_rate_limit(self, group_id: int, now: float,
                          consume: bool = True) -> bool:
        """Fixed Window 限流检查，返回 True=允许。

        consume=True 时同时扣额度（旧行为）；consume=False 时仅判断。
        """
        bucket = self._rate_buckets.setdefault(group_id, [])
        # 真正就地修改：切片赋值保留同一 list 对象，避免局部变量失效，
        # 也避免每条消息都新建 list 造成的开销
        cutoff = now - self.rate_window
        bucket[:] = [t for t in bucket if t > cutoff]

        if len(bucket) >= self.rate_max:
            return False

        if consume:
            bucket.append(now)
        return True

    def _strip_cq_codes(self, raw: str) -> str:
        """去掉 CQ 码（不会动了），提取纯文本"""
        return re.sub(r'\[CQ:[^\]]+\]', '', raw).strip()

    # ── 群启停管理 ──

    def is_group_enabled(self, group_id: int) -> bool:
        """群是否启用（黑名单模式：不在 disabled_groups 即启用）。"""
        return int(group_id) not in self.disabled_groups

    def set_group_enabled(self, group_id: int, enabled: bool):
        """启用/禁用某群（更新内存黑名单，不负责持久化）。"""
        gid = int(group_id)
        if enabled:
            self.disabled_groups.discard(gid)
        else:
            self.disabled_groups.add(gid)

    def update_cooldown(self, msg: GroupMessage):
        """回复后更新冷却时间"""
        now = time.time()
        self._last_global_time = now
        self._last_group_time[msg.group_id] = now
        self._last_user_time[msg.user_id] = now

    # ── 状态 ──

    def get_status(self) -> dict:
        return {
            "global_cooldown_remaining": max(0, self.global_cooldown - (time.time() - self._last_global_time)),
            "active_groups": len(self._last_group_time),
            "rate_limited_groups": [
                gid for gid, bucket in self._rate_buckets.items()
                if len(bucket) >= self.rate_max * 0.8
            ],
        }
