"""技能管理 API — 列表、触发词编辑、启用/禁用。"""
from __future__ import annotations
from aiohttp import web

from skills.manager import skill_manager


async def skills_list_handler(request: web.Request) -> web.Response:
    """GET /api/skills — 技能列表（含触发词、启停状态）。"""
    try:
        return web.json_response({"ok": True, "data": skill_manager.list_skills()})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def skills_set_triggers_handler(request: web.Request) -> web.Response:
    """POST /api/skills/{name}/triggers — 设置自定义触发词。

    body: {"triggers": ["生图", "画图"]} 或 {"triggers": []} 清除覆盖回退默认
    """
    name = request.match_info["name"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "无效 JSON"}, status=400)

    triggers = body.get("triggers")
    if not isinstance(triggers, list):
        return web.json_response({"ok": False, "error": "triggers 必须是列表"}, status=400)
    # 规范化：去空白、去空串
    triggers = [t.strip() for t in triggers if isinstance(t, str) and t.strip()]

    skill_manager.set_triggers(name, triggers)

    # 持久化到 config.yaml
    bot = request.app["bot"]
    try:
        bot.config.setdefault("skills", {}).setdefault(name, {})["triggers"] = triggers
        from webui.hot_reload import persist_config
        persist_config(bot.config)
    except Exception as e:
        return web.json_response({"ok": True, "warning": f"内存已生效但持久化失败: {e}"})

    return web.json_response({"ok": True, "message": f"已更新技能 [{name}] 触发词"})


async def skills_toggle_handler(request: web.Request) -> web.Response:
    """POST /api/skills/{name}/toggle — 启用/禁用技能。body: {"enabled": bool}"""
    name = request.match_info["name"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "无效 JSON"}, status=400)

    enabled = bool(body.get("enabled"))
    skill_manager.set_enabled(name, enabled)

    # 持久化
    bot = request.app["bot"]
    try:
        bot.config.setdefault("skills", {}).setdefault(name, {})["enabled"] = enabled
        from webui.hot_reload import persist_config
        persist_config(bot.config)
    except Exception as e:
        return web.json_response({"ok": True, "warning": f"内存已生效但持久化失败: {e}"})

    action = "启用" if enabled else "禁用"
    return web.json_response({"ok": True, "message": f"已{action}技能 [{name}]"})
