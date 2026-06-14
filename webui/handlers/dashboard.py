"""仪表盘 API — 聚合各模块状态供首页概览展示。"""
from __future__ import annotations
import time
from aiohttp import web

from webui.events import event_bus


def _safe_call(fn, *args, default=None, **kwargs):
    """安全调用 bot 子模块方法，失败返回 default。"""
    try:
        return fn(*args, **kwargs)
    except Exception:
        return default


def build_dashboard(bot) -> dict:
    """聚合 Bot 各模块状态，返回仪表盘数据。"""
    # NapCat 连接状态
    napcat = bot.napcat
    napcat_status = _safe_call(napcat.get_health, default={}) or {}

    # 活跃引擎状态
    activator_status = _safe_call(bot.activator.get_status, default={}) or {}

    # 技能列表
    from skills.manager import skill_manager
    skills = _safe_call(skill_manager.list_skills, default=[]) or []

    # 工具列表
    from src.tools.registry import tool_registry
    enabled_tools = bot.config.get("tools", {}).get("enabled") or []
    all_tools = _safe_call(tool_registry.list_tools, default=[]) or []

    # 调度器任务
    jobs = []
    try:
        for job in bot.scheduler._scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": str(job.func),
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            })
    except Exception:
        pass

    # 记忆统计
    memory_stats = {
        "short_term_groups": 0,
        "long_term_count": 0,
    }
    try:
        memory_stats["short_term_groups"] = len(bot.memory.buffer._group_contexts)
    except Exception:
        pass
    try:
        if bot.memory.vector._enabled:
            memory_stats["long_term_count"] = bot.memory.vector._collection.count()
    except Exception:
        pass

    return {
        "bot_name": bot.bot_name,
        "uptime_seconds": int(napcat_status.get("uptime_seconds", 0)),
        "event_count": event_bus.event_count,
        "napcat": napcat_status,
        "activator": activator_status,
        "skills": skills,
        "tools": {
            "enabled": enabled_tools,
            "available": all_tools,
        },
        "scheduler_jobs": jobs,
        "memory": memory_stats,
        "server_time": time.time(),
    }


async def dashboard_handler(request: web.Request) -> web.Response:
    """GET /api/dashboard — 仪表盘聚合数据。"""
    bot = request.app["bot"]
    try:
        data = build_dashboard(bot)
        return web.json_response({"ok": True, "data": data})
    except Exception as e:
        return web.json_response(
            {"ok": False, "error": str(e)}, status=500
        )
