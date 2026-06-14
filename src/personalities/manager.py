"""人格预设管理器 — 从 config/personalities/*.yaml 加载 ChatLuna 风格预设。

支持的 YAML 格式（ChatLuna 核心子集）：

    name: 默认                  # 显示名
    keywords: [默认, 小白]      # 关键词（仅展示，不参与命令匹配）
    prompts:
      - role: system            # system / user / assistant
        content: |-
          你是{bot_name}，一个可爱的 QQ 群聊 AI 助手。
          你的性格：温柔、热情、俏皮。
      - role: assistant
        content: 喵~

占位符（安全子集，用 str.replace 渲染，避免文本里的 { } 炸）：
  {bot_name}    → bot.config["bot"]["name"]
  {master_id}   → bot.config["bot"]["master_id"]

master_id 为空时，含 {master_id} 的 prompt 整条跳过（不渲染成空字符串）。

健壮性：
  - YAML 解析失败/字段缺失 → 该预设被跳过，log warning，不崩 Bot
  - 目录不存在/为空 → 用内置默认人格兜底，并尝试创建目录 + 写 default.yaml
  - 切换到不存在的预设 → 回退到 default，log warning
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger


# ── 项目根目录（src/personalities/manager.py → 向上三级是项目根）──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PRESET_DIR = _PROJECT_ROOT / "config" / "personalities"

# 允许的 prompt role（其它值当 system 处理）
_VALID_ROLES = {"system", "user", "assistant"}

# 渲染时识别的占位符
_PLACEHOLDERS = ("bot_name", "master_id")

# 保留 key（与 WebUI 路由冲突，禁止用作预设文件名）
# 这些名字对应 /api/personalities/<key> 的固定路由，若用作 key 会导致
# POST /api/personalities/activate 永远命中激活接口而非保存接口。
_RESERVED_KEYS = frozenset({
    "activate", "reload", "open-folder", "active",
})


@dataclass
class Personality:
    """一份人格预设。"""
    # 用于显示和切换的 key（= yaml 文件名去掉扩展名）
    key: str
    # YAML 里的 name 字段（显示名）；缺失时回退到 key
    name: str
    # 关键词（仅展示）
    keywords: list[str] = field(default_factory=list)
    # ChatLuna prompts：[{role, content}]
    prompts: list[dict] = field(default_factory=list)
    # 源文件路径（保存/调试用）
    file_path: Optional[Path] = None

    @property
    def system_content(self) -> str:
        """把所有 system role 的 content 拼成一个字符串（兼容旧 generate_system_prompt）。"""
        return "\n".join(
            p.get("content", "") for p in self.prompts
            if p.get("role", "system") == "system"
        )


# ── 内置兜底预设 ──
# 与 src/llm/client.py 旧版硬编码逐字一致，保证"零行为变化"。
# 当 config/personalities/ 为空或 default.yaml 解析失败时启用。
def _builtin_default() -> Personality:
    base_parts = [
        "你是{bot_name}，一个可爱的 QQ 群聊 AI 助手。",
        "你当前在群ID: {group_id}，此群的对话和记忆独立于其他群。",
        "你的性格: 可爱、温柔、热情、有点小俏皮，说话亲切自然。",
        "你喜欢用轻松活泼的语气和大家聊天，偶尔会卖萌，但不做作。",
        "回复简洁友好，一般一两句话就好，不要长篇大论。",
        "你会主动参与群聊讨论，但不会刷屏打扰别人。",
        "你是群聊中的一员，有自己的性格和温度。",
        "除非被@，否则你可以选择不回复，保持自然。",
        "遇到不知道的问题，坦诚说不知道，不编造。",
        "适当使用颜文字或 emoji 表达情绪，比如 (｡•̀ᴗ-)✧、(´▽`ʃ♡ƪ)。",
        "你的性格会随着对话自然发展，逐渐形成更立体的形象。",
    ]
    return Personality(
        key="default",
        name="默认",
        keywords=["默认", "小白"],
        prompts=[
            {"role": "system", "content": "\n".join(base_parts)},
            # master_id 条件段（master_id 为空时由 render 跳过）
            {"role": "system", "content": "你的主人QQ是{master_id}，主人说话时你可以更亲近一些。"},
        ],
    )


class PersonalityManager:
    """人格预设管理器。

    用法：
        pm = PersonalityManager(config)
        pm.load_all()                       # 启动时扫目录
        messages = pm.render_system_prompt(  # 每次回复时渲染
            bot_name="小白", master_id="123", group_id=456,
            memory_context="...",
        )
        pm.set_active("cat_maid")           # 热切换激活预设
    """

    def __init__(self, config: dict):
        self._config = config
        self._dir = _PRESET_DIR
        # key -> Personality
        self._presets: dict[str, Personality] = {}
        # 当前激活的 key（默认 "default"）
        bot_cfg = config.get("bot", {}) or {}
        self._active_key: str = str(bot_cfg.get("active_personality") or "default")

    # ── 加载 ──

    def load_all(self) -> None:
        """扫描 config/personalities/*.yaml，加载所有预设。

        目录不存在或为空时，用内置默认人格兜底，并尝试创建目录 +
        写出 default.yaml（让用户能直接编辑）。
        """
        self._presets.clear()

        # 确保目录存在
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(f"人格预设目录创建失败: {e}")

        loaded_any = False
        if self._dir.exists():
            for path in sorted(self._dir.glob("*.y*ml")):  # .yaml / .yml
                p = self._load_one(path)
                if p is not None:
                    if p.key in self._presets:
                        logger.warning(f"人格预设 key 重复，后者覆盖前者: {p.key}")
                    self._presets[p.key] = p
                    loaded_any = True

        if not loaded_any:
            # 空目录：写一份 default.yaml 出来，让用户有起点
            logger.info("人格预设目录为空，写出内置 default.yaml 作为起点")
            builtin = _builtin_default()
            self._write_default_seed(builtin)
            self._presets[builtin.key] = builtin

        # 确保激活的 key 合法，否则回退 default
        if self._active_key not in self._presets:
            logger.warning(
                f"激活的人格 '{self._active_key}' 不存在，回退到 default"
            )
            self._active_key = "default"
            if "default" not in self._presets:
                self._presets["default"] = _builtin_default()

        logger.info(
            f"人格预设已加载: {list(self._presets.keys())}，当前激活: {self._active_key}"
        )

    def _load_one(self, path: Path) -> Optional[Personality]:
        """加载单个 yaml 文件。失败返回 None（不抛异常）。"""
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as e:
            logger.warning(f"人格预设解析失败 [{path.name}]: {e}")
            return None

        if not isinstance(data, dict):
            logger.warning(f"人格预设格式错误（非对象）[{path.name}]，已跳过")
            return None

        key = path.stem  # 文件名去扩展名
        if key in _RESERVED_KEYS:
            logger.warning(
                f"人格预设文件名 '{key}' 是保留字（与 WebUI 路由冲突），"
                "将无法在 WebUI 编辑/保存该预设。建议重命名文件。"
            )
        name = str(data.get("name") or key)
        keywords_raw = data.get("keywords") or []
        keywords = [str(k) for k in keywords_raw] if isinstance(keywords_raw, list) else []
        prompts_raw = data.get("prompts") or []
        prompts = self._normalize_prompts(prompts_raw, path.name)

        if not prompts:
            logger.warning(f"人格预设无有效 prompts [{path.name}]，已跳过")
            return None

        return Personality(
            key=key, name=name, keywords=keywords,
            prompts=prompts, file_path=path,
        )

    @staticmethod
    def _normalize_prompts(prompts_raw, src_name: str) -> list[dict]:
        """规整 prompts 列表：每项必须有 role 和 content 字符串。"""
        if not isinstance(prompts_raw, list):
            return []
        result = []
        for i, p in enumerate(prompts_raw):
            if not isinstance(p, dict):
                continue
            role = str(p.get("role") or "system").strip().lower()
            if role not in _VALID_ROLES:
                logger.debug(
                    f"人格预设 [{src_name}] prompt#{i} role={role!r} 非法，当 system 处理"
                )
                role = "system"
            content = p.get("content")
            if content is None:
                continue
            content = str(content)
            if not content.strip():
                continue
            result.append({"role": role, "content": content})
        return result

    def _write_default_seed(self, builtin: Personality) -> None:
        """把内置默认人格写成 default.yaml（仅在目录为空时种子化）。"""
        target = self._dir / "default.yaml"
        try:
            # 拼出与 client.py 旧版逐字一致的文本
            system_block = builtin.prompts[0]["content"]
            master_block = builtin.prompts[1]["content"]
            yaml_text = (
                "# 人格预设（ChatLuna 风格）\n"
                "# 占位符：{bot_name} {master_id} {group_id}\n"
                "# master_id 为空时，含 {master_id} 的 prompt 整条跳过\n"
                "name: 默认\n"
                "keywords:\n"
                "  - 默认\n"
                "  - 小白\n"
                "prompts:\n"
                "  - role: system\n"
                "    content: |-\n"
                + _indent(system_block, 6) + "\n"
                "  - role: system\n"
                "    content: |-\n"
                + _indent(master_block, 6) + "\n"
            )
            with open(target, "w", encoding="utf-8") as f:
                f.write(yaml_text)
            builtin.file_path = target
            logger.info(f"已写出默认预设种子: {target}")
        except OSError as e:
            logger.warning(f"写默认预设种子失败: {e}")

    # ── 查询 ──

    def list_all(self) -> list[Personality]:
        """所有已加载预设（激活项不一定在第一位）。"""
        return list(self._presets.values())

    def get(self, key: str) -> Optional[Personality]:
        return self._presets.get(key)

    @property
    def active(self) -> Personality:
        """当前激活的预设（保证非 None，兜底内置默认）。"""
        return self._presets.get(self._active_key) or _builtin_default()

    @property
    def active_key(self) -> str:
        return self._active_key

    # ── 切换 ──

    def set_active(self, key: str) -> bool:
        """切换激活预设。成功返回 True。"""
        if key not in self._presets:
            logger.warning(f"切换人格失败：'{key}' 不存在")
            return False
        self._active_key = key
        logger.info(f"人格已切换为: {key}")
        return True

    def reload(self) -> int:
        """重新扫描目录（用户手动放文件后调用）。返回加载数量。"""
        # 保留当前激活 key（load_all 会校验合法性）
        self.load_all()
        return len(self._presets)

    # ── 渲染 ──

    def render_system_prompt(
        self,
        bot_name: str,
        master_id: str,
        group_id: int,
        memory_context: str,
    ) -> list[dict]:
        """渲染激活预设为 OpenAI messages 片段。

        返回 [{role, content}, ...]，由调用方拼到 messages 开头。
        渲染占位符；master_id 为空时跳过含 {master_id} 的 prompt。
        末尾追加一条 system prompt 注入 memory_context（与旧行为一致）。
        """
        p = self.active
        rendered: list[dict] = []
        for item in p.prompts:
            content = item["content"]
            role = item["role"]
            # master_id 条件段：master_id 为空且本条引用了它，整条跳过
            if "{master_id}" in content and not master_id:
                continue
            content = content.replace("{bot_name}", str(bot_name))
            content = content.replace("{master_id}", str(master_id))
            content = content.replace("{group_id}", str(group_id))
            rendered.append({"role": role, "content": content})

        # 追加 memory_context（作为最后一条 system，与旧 generate_system_prompt 行为一致）
        if memory_context:
            rendered.append({"role": "system", "content": memory_context})

        return rendered

    # ── 保存（WebUI 编辑用）──

    def save(self, key: str, name: str, keywords: list[str],
             prompts: list[dict]) -> Personality:
        """把编辑后的预设写回 yaml 文件，并更新内存。

        若文件不存在则新建。返回更新后的 Personality。
        """
        key = str(key or "").strip()
        if not key:
            raise ValueError("预设 key 不能为空")
        if key in _RESERVED_KEYS:
            raise ValueError(
                f"'{key}' 是保留字（与 WebUI 路由冲突），请换一个文件名"
            )
        path = self._dir / f"{key}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)

        # 构造 yaml 数据（用 yaml.safe_dump 保证格式合法）
        data = {
            "name": name or key,
            "keywords": [str(k) for k in keywords],
            "prompts": [
                {"role": str(p.get("role", "system")),
                 "content": str(p.get("content", ""))}
                for p in prompts
                if str(p.get("content", "")).strip()
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            # 头部注释 + 正文
            f.write("# 人格预设（ChatLuna 风格）\n")
            f.write("# 占位符：{bot_name} {master_id} {group_id}\n")
            yaml.safe_dump(
                data, f, allow_unicode=True,
                sort_keys=False, default_flow_style=False,
            )

        # 重新加载这一个
        reloaded = self._load_one(path)
        if reloaded is None:
            raise ValueError(f"保存后重新加载失败: {path}")
        self._presets[key] = reloaded
        logger.info(f"人格预设已保存: {path}")
        return reloaded

    def delete(self, key: str) -> bool:
        """删除一个预设。不能删除当前激活的或 default。"""
        if key == self._active_key:
            logger.warning(f"不能删除当前激活的人格: {key}")
            return False
        if key == "default":
            logger.warning("不能删除 default 预设")
            return False
        p = self._presets.get(key)
        if p is None:
            return False
        if p.file_path and p.file_path.exists():
            try:
                p.file_path.unlink()
            except OSError as e:
                logger.warning(f"删除预设文件失败: {e}")
                return False
        self._presets.pop(key, None)
        logger.info(f"人格预设已删除: {key}")
        return True


def _indent(text: str, spaces: int) -> str:
    """每行缩进指定空格数（用于 yaml block scalar）。"""
    pad = " " * spaces
    return "\n".join(pad + line if line else line for line in text.split("\n"))
