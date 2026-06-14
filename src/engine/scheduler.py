"""定时任务调度 — Bot 主动发起群聊"""
from __future__ import annotations
import asyncio
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Coroutine
from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler


class Scheduler:
    """主动活跃调度器"""

    def __init__(self):
        self._scheduler = AsyncIOScheduler(timezone=timezone.utc)
        self._tasks: dict[str, dict] = {}
        self._send_fn: Callable[[int, str], Coroutine] | None = None

    def set_send_fn(self, fn: Callable[[int, str], Coroutine]):
        self._send_fn = fn

    async def start(self):
        self._scheduler.start()
        logger.info("调度器已启动")

    async def stop(self):
        self._scheduler.shutdown(wait=False)
        logger.info("调度器已停止")

    # ── 内置活跃任务 ──

    def add_active_greeting(self, group_id: int, hour: int = 9):
        """每天早上 hour 点在群里发问候"""
        self._scheduler.add_job(
            self._greeting_job,
            trigger="cron",
            hour=hour,
            minute=0,
            kwargs={"group_id": group_id},
            id=f"greeting_{group_id}",
            replace_existing=True,
        )
        logger.info(f"已添加群[{group_id}]的{hour}点问候任务")

    async def _greeting_job(self, group_id: int):
        if self._send_fn:
            greetings = [
                "早上好呀~ 今天也是充满活力的一天！",
                "早安！有什么新鲜事吗？",
                "大家早上好！今天也要开心！",
            ]
            msg = random.choice(greetings)
            await self._send_fn(group_id, msg)

    def add_activity_reminder(self, group_id: int, interval_hours: int = 4):
        """每隔 interval_hours 小时主动活跃"""
        self._scheduler.add_job(
            self._activity_job,
            trigger="interval",
            hours=interval_hours,
            kwargs={"group_id": group_id},
            id=f"activity_{group_id}",
            replace_existing=True,
        )

    async def _activity_job(self, group_id: int):
        if self._send_fn and random.random() < 0.5:
            prompts = [
                "大家在聊什么呢？带我一个~",
                "今天群里好安静呀",
            ]
            msg = random.choice(prompts)
            await self._send_fn(group_id, msg)

    # ── 动态任务 ──

    def add_delayed_reply(self, group_id: int, message: str, delay: float):
        """延时回复"""
        async def job():
            if self._send_fn:
                await self._send_fn(group_id, message)
        self._scheduler.add_job(
            job,
            trigger="date",
            run_date=datetime.fromtimestamp(time.time() + delay, tz=timezone.utc),
            id=f"delay_{group_id}_{uuid.uuid4().hex}",
        )

    def add_reminder(self, group_id: int, message: str, seconds_from_now: int):
        """定时提醒"""
        async def job():
            if self._send_fn:
                await self._send_fn(group_id, f"⏰ 提醒：{message}")
        self._scheduler.add_job(
            job,
            trigger="date",
            run_date=datetime.fromtimestamp(time.time() + seconds_from_now, tz=timezone.utc),
            id=f"remind_{group_id}_{uuid.uuid4().hex}",
        )
        logger.info(f"已创建提醒: [{group_id}] 在{seconds_from_now}秒后")
