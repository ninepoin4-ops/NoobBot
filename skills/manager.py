"""技能管理器 — 关键词匹配 + 调度"""
from __future__ import annotations
import importlib
import pkgutil
import re
from typing import Any

from loguru import logger

from skills.base import Skill


class SkillManager:
    """技能管理器"""

    def __init__(self):
        self._skills: list[Skill] = []
        # 触发词覆盖：name -> list[str]；为空时用 Skill 类默认值
        self._custom_triggers: dict[str, list[str]] = {}
        # 禁用列表：name -> bool
        self._disabled: set[str] = set()

    def load_all(self, config: dict | None = None):
        """自动发现并加载 skills/ 目录下所有技能。

        config: 可选，传入 config dict 用于读取 skills.<name>.triggers/enabled 覆盖。
        """
        self._skills.clear()
        self._custom_triggers.clear()
        self._disabled.clear()
        import skills
        for importer, modname, ispkg in pkgutil.iter_modules(skills.__path__):
            if modname in ("base",):
                continue
            try:
                module = importlib.import_module(f"skills.{modname}")
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and issubclass(attr, Skill) and attr is not Skill:
                        skill = attr()
                        self._skills.append(skill)
                        logger.info(f"技能已加载: [{skill.name}] 触发词={skill.triggers}")
            except Exception as e:
                logger.warning(f"技能加载失败 [{modname}]: {e}")

        # 应用 config 中的触发词/禁用覆盖
        if config:
            self._apply_config(config)

        logger.info(f"共加载 {len(self._skills)} 个技能")

    def _apply_config(self, config: dict):
        """从 config.skills.<name>.{triggers,enabled} 应用覆盖。"""
        skills_cfg = config.get("skills", {}) or {}
        for skill in self._skills:
            sc = skills_cfg.get(skill.name) or {}
            triggers = sc.get("triggers")
            if isinstance(triggers, list) and triggers:
                self._custom_triggers[skill.name] = triggers
            if sc.get("enabled") is False:
                self._disabled.add(skill.name)

    def get_triggers(self, skill_name: str) -> list[str]:
        """获取技能的有效触发词（自定义 > 默认）。"""
        return self._custom_triggers.get(skill_name) or []

    def set_triggers(self, skill_name: str, triggers: list[str]):
        """设置/清除自定义触发词（triggers 为空列表则清除覆盖，回退到默认）。"""
        if triggers:
            self._custom_triggers[skill_name] = list(triggers)
        else:
            self._custom_triggers.pop(skill_name, None)

    def set_enabled(self, skill_name: str, enabled: bool):
        if enabled:
            self._disabled.discard(skill_name)
        else:
            self._disabled.add(skill_name)

    def is_enabled(self, skill_name: str) -> bool:
        return skill_name not in self._disabled

    def match(self, message: str) -> Skill | None:
        """匹配消息中的技能触发词（必须出现在句首）"""
        # 去掉开头 CQ 码和空白，定位有效正文开头
        stripped = re.sub(r'^(\[CQ:[^\]]*\]\s*)*', '', message).strip()
        for skill in self._skills:
            if skill.name in self._disabled:
                continue
            # 优先用自定义触发词，回退到类默认值
            triggers = self._custom_triggers.get(skill.name, skill.triggers)
            for trigger in triggers:
                if stripped.startswith(trigger):
                    return skill
        return None

    async def execute(self, skill: Skill, bot: Any, group_id: int,
                      user_id: int, message: str) -> str | None:
        """执行技能"""
        try:
            return await skill.run(bot, group_id, user_id, message)
        except Exception as e:
            logger.exception(f"技能执行失败 [{skill.name}]: {e}")
            return f"技能 [{skill.name}] 执行出错：{e}"

    def list_skills(self) -> list[dict]:
        result = []
        for s in self._skills:
            result.append({
                "name": s.name,
                "triggers": self._custom_triggers.get(s.name, s.triggers),
                "default_triggers": s.triggers,
                "customized": s.name in self._custom_triggers,
                "enabled": s.name not in self._disabled,
                "desc": s.description,
            })
        return result


# 全局管理器
skill_manager = SkillManager()
