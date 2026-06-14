"""配置热更新逻辑。

把配置字段分成三类，提供统一的 apply_config_change() 入口：
- HOT_RELOADABLE：改 bot.config 字典 + 同步到对应模块实例属性，立即生效
- REBUILD_REQUIRED：需重建子模块（如 LLM 客户端），通过专门接口触发
- RESTART_REQUIRED：需重启进程（如 napcat 连接、webui 端口）

所有改动都先持久化到 config.yaml，避免重启丢失。
"""
from __future__ import annotations
from pathlib import Path

from loguru import logger

# 三种生效方式
APPLIED = "applied"                  # 已即时生效
REBUILD_REQUIRED = "rebuild_required"  # 需要重建子模块
RESTART_REQUIRED = "restart_required"  # 需要重启进程


def _set_nested(d: dict, dotted_key: str, value):
    """按 a.b.c 形式写入嵌套字典。"""
    keys = dotted_key.split(".")
    cur = d
    for k in keys[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[keys[-1]] = value


def _get_nested(d: dict, dotted_key: str, default=None):
    keys = dotted_key.split(".")
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


# ──────────────────── 热更新应用器 ────────────────────

def _apply_bot_name(bot, v):
    bot.bot_name = v
    bot.config["bot"]["name"] = v

def _apply_master_id(bot, v):
    bot.config.setdefault("bot", {})["master_id"] = v

def _apply_random_freq(bot, v):
    bot.activator.random_freq = float(v)
    _set_nested(bot.config, "engagement.random_reply_frequency", v)

def _apply_bot_names(bot, v):
    if not isinstance(v, list):
        raise ValueError("bot_names 必须是列表")
    bot.activator.bot_names = v
    _set_nested(bot.config, "engagement.bot_names", v)

def _apply_name_match_mode(bot, v):
    bot.activator.name_match_mode = v
    _set_nested(bot.config, "engagement.name_match_mode", v)

def _apply_disabled_groups(bot, v):
    """群黑名单热更新：v 是 list[int|str]，存入 activator.disabled_groups。"""
    if not isinstance(v, list):
        raise ValueError("disabled_groups 必须是列表")
    bot.activator.disabled_groups = set(int(g) for g in v if g)
    _set_nested(bot.config, "engagement.disabled_groups", sorted(bot.activator.disabled_groups))

def _apply_global_cooldown(bot, v):
    bot.activator.global_cooldown = float(v)
    _set_nested(bot.config, "cooldown.global_cooldown", v)

def _apply_group_cooldown(bot, v):
    bot.activator.group_cooldown = float(v)
    _set_nested(bot.config, "cooldown.group_cooldown", v)

def _apply_user_cooldown(bot, v):
    bot.activator.user_cooldown = float(v)
    _set_nested(bot.config, "cooldown.user_cooldown", v)

def _apply_rate_limit(bot, v):
    if not isinstance(v, dict):
        raise ValueError("rate_limit 必须是字典")
    rl = bot.activator
    if "window" in v: rl.rate_window = float(v["window"])
    if "max_count" in v: rl.rate_max = int(v["max_count"])
    if "strategy" in v: rl.rate_strategy = v["strategy"]
    _set_nested(bot.config, "cooldown.rate_limit", v)

def _apply_tools_enabled(bot, v):
    if not isinstance(v, list):
        raise ValueError("tools.enabled 必须是列表")
    bot.config.setdefault("tools", {})["enabled"] = v  # bot.py 每次都实时读，天然热生效

def _apply_retrieval_k(bot, v):
    # VectorMemory 在 long_term.enabled=False 时会提前 return，
    # 此时实例上没有 _retrieval_k 属性，直接赋值会 AttributeError
    vector = bot.memory.vector
    if getattr(vector, "_enabled", False):
        vector._retrieval_k = int(v)
    _set_nested(bot.config, "memory.long_term.retrieval_k", int(v))

def _apply_min_save_length(bot, v):
    vector = bot.memory.vector
    if getattr(vector, "_enabled", False):
        vector._min_save_len = int(v)
    _set_nested(bot.config, "memory.long_term.min_save_length", int(v))

def _apply_log_level(bot, v):
    # loguru 没有 set_level API，且重新 add handler 会产生重复日志，
    # 这里仅把级别写入 config；实际过滤靠 WebUI 前端的级别下拉框。
    _set_nested(bot.config, "logging.level", v)


# 热更新字段表
HOT_APPLIERS = {
    "bot.name": _apply_bot_name,
    "bot.master_id": _apply_master_id,
    "engagement.random_reply_frequency": _apply_random_freq,
    "engagement.bot_names": _apply_bot_names,
    "engagement.name_match_mode": _apply_name_match_mode,
    "engagement.disabled_groups": _apply_disabled_groups,
    "cooldown.global_cooldown": _apply_global_cooldown,
    "cooldown.group_cooldown": _apply_group_cooldown,
    "cooldown.user_cooldown": _apply_user_cooldown,
    "cooldown.rate_limit": _apply_rate_limit,
    "tools.enabled": _apply_tools_enabled,
    "memory.long_term.retrieval_k": _apply_retrieval_k,
    "memory.long_term.min_save_length": _apply_min_save_length,
    "logging.level": _apply_log_level,
}

# 需要重建 LLM 客户端的字段
REBUILD_KEYS = {
    "llm.api_key", "llm.base_url", "llm.model",
    "llm.max_tokens", "llm.temperature",
    "llm.context_window", "llm.context_compression_threshold",
}

# 需要重启进程的字段
RESTART_KEYS = {
    "napcat.mode", "napcat.forward.ws_url",
    "napcat.reverse.host", "napcat.reverse.port", "napcat.reverse.access_token",
    "napcat.max_concurrent_handlers",
    "memory.short_term.max_turns", "memory.short_term.compression_threshold",
    "memory.long_term.enabled", "memory.long_term.collection_name",
    "memory.long_term.max_entry_bytes",
    "webui.enabled", "webui.host", "webui.port",
}


def classify(key: str) -> str:
    """判断一个配置 key 的生效方式。"""
    if key in HOT_APPLIERS:
        return APPLIED
    if key in REBUILD_KEYS:
        return REBUILD_REQUIRED
    if key in RESTART_KEYS:
        return RESTART_REQUIRED
    # 默认认为需要重启（未知字段保守处理）
    return RESTART_REQUIRED


def apply_config_change(bot, key: str, value) -> dict:
    """应用一个配置变更。

    返回 {ok, status, message}。即使返回 RESTART_REQUIRED 也已经写入 bot.config + 持久化。

    顺序：先校验+应用到内存（hot 字段），成功后再持久化。apply 失败时回滚内存值，
    避免把非法值（如 cooldown='abc'）写进 config.yaml 导致下次启动 Activator 崩溃。
    """
    # 1. 备份旧值，apply 失败时回滚
    old_value = _get_nested(bot.config, key, _MISSING)

    # 2. 写入 bot.config（让所有热/非热字段都有最新值）
    _set_nested(bot.config, key, value)

    # 3. 按 key 类型应用到内存模块
    status = classify(key)
    if status == APPLIED:
        applier = HOT_APPLIERS[key]
        try:
            applier(bot, value)
        except Exception as e:
            _rollback(bot.config, key, old_value)
            return {"ok": False, "status": "error",
                    "message": f"热更新应用失败: {e}（已回滚）"}

    # 4. 应用成功后才持久化（避免非法值污染 yaml）
    try:
        persist_config(bot.config)
    except Exception as e:
        # persist 失败不回滚内存（热字段已经生效了），只告知用户
        logger.warning(f"配置持久化失败: {e}")
        return {"ok": True, "status": status,
                "message": f"已生效但持久化失败: {e}"}

    if status == APPLIED:
        return {"ok": True, "status": APPLIED, "message": "已即时生效"}

    if status == REBUILD_REQUIRED:
        return {"ok": True, "status": REBUILD_REQUIRED,
                "message": "配置已保存，需点击「重建 LLM 客户端」生效"}

    return {"ok": True, "status": RESTART_REQUIRED,
            "message": "配置已保存，需重启 Bot 进程生效"}


# apply 失败回滚用的哨兵值（区分"字段不存在"和"字段值为 None"）
_MISSING = object()


def _rollback(config: dict, key: str, old_value):
    """apply 失败时把 bot.config 里对应 key 恢复到旧值。"""
    if old_value is _MISSING:
        # 原本不存在，删掉新写入的叶子 key（如果可删）
        keys = key.split(".")
        cur = config
        for k in keys[:-1]:
            if not isinstance(cur, dict) or k not in cur:
                return
            cur = cur[k]
        if isinstance(cur, dict):
            cur.pop(keys[-1], None)
    else:
        _set_nested(config, key, old_value)


def rebuild_llm_client(bot) -> dict:
    """根据当前 bot.config 重建 LLM 客户端。"""
    try:
        from src.llm.client import LLMClient
        bot.llm = LLMClient(bot.config)
        # 同步更新 memory/activator 等引用 llm 的模块
        if bot.memory:
            bot.memory._llm = bot.llm
            if bot.memory.compressor:
                bot.memory.compressor._llm = bot.llm
        # 更新快捷属性
        llm_config = bot.config.get("llm", {})
        bot.context_window = llm_config.get("context_window", 1_000_000)
        bot.context_compression_threshold = llm_config.get(
            "context_compression_threshold", 180_000
        )
        return {"ok": True, "message": "LLM 客户端已重建"}
    except Exception as e:
        return {"ok": False, "message": f"重建失败: {e}"}


# ──────────────────── 持久化 ────────────────────

def persist_config(config: dict, path: str = None) -> str:
    """把 config 写回 config.yaml（注意：会丢失原注释）。"""
    import yaml
    if path is None:
        path = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
    path = Path(path)
    # 写入前确保敏感字段不被错误清空
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return str(path)


def mask_sensitive(config: dict) -> dict:
    """返回脱敏后的 config 副本（api_key 等显示为 ****）。"""
    import copy
    masked = copy.deepcopy(config)
    # llm.api_key
    try:
        if masked.get("llm", {}).get("api_key"):
            k = masked["llm"]["api_key"]
            masked["llm"]["api_key"] = (k[:6] + "****") if len(k) > 8 else "****"
    except Exception:
        pass
    # napcat.reverse.access_token
    try:
        if masked.get("napcat", {}).get("reverse", {}).get("access_token"):
            masked["napcat"]["reverse"]["access_token"] = "****"
    except Exception:
        pass
    return masked


def get_config_meta() -> list:
    """返回字段元信息，供前端展示「热生效/需重建/需重启」标签。"""
    fields = []
    for k in HOT_APPLIERS:
        fields.append({"key": k, "status": APPLIED})
    for k in REBUILD_KEYS:
        fields.append({"key": k, "status": REBUILD_REQUIRED})
    for k in RESTART_KEYS:
        fields.append({"key": k, "status": RESTART_REQUIRED})
    return fields
