"""群聊管理 API — 列出群、启用/禁用群（黑名单模式）。"""
from __future__ import annotations
from aiohttp import web


async def groups_list_handler(request: web.Request) -> web.Response:
    """GET /api/groups — Bot 加入的所有群，合并启用/禁用状态。

    返回 [{group_id, group_name, member_count, enabled}, ...]。
    需要先连上 NapCat 才能拉到群列表；未连接时返回空列表 + connected=False。
    """
    bot = request.app["bot"]
    try:
        connected = bot.napcat.is_connected()
        if not connected:
            return web.json_response({
                "ok": True,
                "data": [],
                "connected": False,
                "disabled_groups": sorted(bot.activator.disabled_groups),
            })

        raw_groups = await bot.napcat.get_group_list()
        disabled = bot.activator.disabled_groups
        data = []
        for g in raw_groups or []:
            try:
                gid = int(g.get("group_id", 0))
            except (TypeError, ValueError):
                continue
            if gid <= 0:
                continue
            data.append({
                "group_id": gid,
                "group_name": g.get("group_name", "") or str(gid),
                "member_count": g.get("member_count", 0) or 0,
                "enabled": gid not in disabled,
            })
        # 按成员数倒序，活跃群在前
        data.sort(key=lambda x: -x["member_count"])
        return web.json_response({
            "ok": True,
            "data": data,
            "connected": True,
            "disabled_count": len(disabled),
        })
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def groups_toggle_handler(request: web.Request) -> web.Response:
    """POST /api/groups/{group_id}/toggle — 启用/禁用某群。body: {"enabled": bool}"""
    bot = request.app["bot"]
    try:
        group_id = int(request.match_info["group_id"])
    except (TypeError, ValueError):
        return web.json_response({"ok": False, "error": "无效的群号"}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "无效 JSON"}, status=400)

    enabled = bool(body.get("enabled"))

    # 更新内存黑名单
    bot.activator.set_group_enabled(group_id, enabled)

    # 持久化到 config.yaml
    try:
        disabled_list = sorted(bot.activator.disabled_groups)
        bot.config.setdefault("engagement", {})["disabled_groups"] = disabled_list
        from webui.hot_reload import persist_config
        persist_config(bot.config)
    except Exception as e:
        return web.json_response({
            "ok": True,
            "warning": f"内存已生效但持久化失败: {e}",
            "enabled": enabled,
        })

    action = "启用" if enabled else "禁用"
    return web.json_response({
        "ok": True,
        "message": f"已{action}群 {group_id}",
        "enabled": enabled,
    })
