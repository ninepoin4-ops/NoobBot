"""记忆管理 API — 短期/长期记忆的查看、搜索、删除。"""
from __future__ import annotations
from aiohttp import web


# ── 短期记忆 ──

async def short_term_groups_handler(request: web.Request) -> web.Response:
    """GET /api/memory/groups — 有短期记忆的群列表。"""
    bot = request.app["bot"]
    try:
        buffer = bot.memory.buffer
        groups = []
        for gid, turns in buffer._group_contexts.items():
            groups.append({
                "group_id": gid,
                "turn_count": len(turns),
                "last_time": turns[-1].time if turns else 0,
            })
        groups.sort(key=lambda x: -x["last_time"])
        return web.json_response({"ok": True, "data": groups})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def short_term_handler(request: web.Request) -> web.Response:
    """GET /api/memory/short-term?group_id=X&limit=50 — 该群最近 N 轮对话。"""
    bot = request.app["bot"]
    try:
        group_id = int(request.query.get("group_id", 0))
        limit = int(request.query.get("limit", 50))
        if group_id <= 0:
            return web.json_response({"ok": False, "error": "缺少 group_id"}, status=400)
        turns = bot.memory.buffer.get_group_context(group_id, max_turns=limit)
        data = [{
            "role": t.role,
            "content": t.content,
            "time": t.time,
        } for t in turns]
        return web.json_response({"ok": True, "data": data})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def short_term_delete_handler(request: web.Request) -> web.Response:
    """DELETE /api/memory/short-term/{group_id} — 清空该群短期记忆。"""
    bot = request.app["bot"]
    try:
        group_id = int(request.match_info["group_id"])
        buffer = bot.memory.buffer
        # 清空群上下文 + 该群所有用户的 session
        buffer._group_contexts.pop(group_id, None)
        keys_to_remove = [k for k in buffer._sessions if k.startswith(f"{group_id}:")]
        for k in keys_to_remove:
            buffer._sessions.pop(k, None)
        return web.json_response({"ok": True, "message": f"已清空群 {group_id} 的短期记忆"})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


# ── 长期记忆 ──

async def long_term_stats_handler(request: web.Request) -> web.Response:
    """GET /api/memory/long-term/stats — 长期记忆统计。"""
    bot = request.app["bot"]
    try:
        vector = bot.memory.vector
        if not vector._enabled:
            return web.json_response({"ok": True, "data": {"enabled": False, "count": 0}})
        count = vector._collection.count()
        return web.json_response({"ok": True, "data": {"enabled": True, "count": count}})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def long_term_search_handler(request: web.Request) -> web.Response:
    """GET /api/memory/long-term?q=关键词&group_id=X&limit=20 — 语义搜索长期记忆。

    q 为空时返回最近 N 条（按时间倒序）。
    """
    bot = request.app["bot"]
    try:
        vector = bot.memory.vector
        if not vector._enabled:
            return web.json_response({"ok": True, "data": []})

        q = request.query.get("q", "").strip()
        group_id = request.query.get("group_id", "").strip()
        limit = int(request.query.get("limit", 20))

        if q:
            # 语义搜索
            gid = int(group_id) if group_id else None
            entries = await vector.search(q, group_id=gid, k=limit)
            data = [{
                "id": e.id,
                "content": e.content,
                "group_id": e.group_id,
                "user_id": e.user_id,
                "timestamp": e.timestamp,
            } for e in entries]
        else:
            # 浏览模式：按时间倒序取最近 N 条
            kwargs = {"limit": limit}
            if group_id:
                kwargs["where"] = {"group_id": str(group_id)}
            result = vector._collection.get(**kwargs)
            ids = result.get("ids", [])
            docs = result.get("documents", [])
            metas = result.get("metadatas", [])
            data = []
            for i, _id in enumerate(ids):
                meta = metas[i] if i < len(metas) else {}
                data.append({
                    "id": _id,
                    "content": docs[i] if i < len(docs) else "",
                    "group_id": int(meta.get("group_id", 0)) if meta.get("group_id") else 0,
                    "user_id": int(meta.get("user_id", 0)) if meta.get("user_id") else 0,
                    "timestamp": float(meta.get("timestamp", 0)) if meta.get("timestamp") else 0,
                })
            # 时间倒序
            data.sort(key=lambda x: -x["timestamp"])
        return web.json_response({"ok": True, "data": data})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def long_term_delete_handler(request: web.Request) -> web.Response:
    """DELETE /api/memory/long-term/{id} — 删除单条长期记忆。"""
    bot = request.app["bot"]
    try:
        mem_id = request.match_info["id"]
        vector = bot.memory.vector
        if not vector._enabled:
            return web.json_response({"ok": False, "error": "长期记忆未启用"}, status=400)
        vector._collection.delete(ids=[mem_id])
        return web.json_response({"ok": True, "message": f"已删除记忆 {mem_id}"})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
