"""Noob Bot 入口"""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from loguru import logger


def load_config() -> dict:
    """加载配置"""
    config_path = Path(__file__).parent / "config" / "config.yaml"
    if not config_path.exists():
        logger.error(f"配置文件不存在: {config_path}")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 环境变量覆盖
    import os
    load_dotenv(Path(__file__).parent / "config" / ".env")

    if os.getenv("LLM_API_KEY"):
        config["llm"]["api_key"] = os.getenv("LLM_API_KEY")
    elif os.getenv("LLM_API_KEY_HASH"):
        # 从 hash 重构 (sk- + hash)
        h = os.getenv("LLM_API_KEY_HASH") or ""
        config["llm"]["api_key"] = "sk-" + h
    if os.getenv("MASTER_QQ"):
        config["bot"]["master_id"] = os.getenv("MASTER_QQ")
    if os.getenv("NAPCAT_TOKEN"):
        config["napcat"]["reverse"]["access_token"] = os.getenv("NAPCAT_TOKEN")

    return config


def setup_logging(config: dict):
    """配置日志"""
    lc = config.get("logging", {})
    level = lc.get("level", "INFO")
    log_file = lc.get("file", "data/bot.log")
    rotation = lc.get("rotation", "10 MB")

    logger.remove()  # 清除默认 handler
    logger.add(sys.stderr, level=level, colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}")
    logger.add(log_file, level=level, rotation=rotation,
               format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")

    # WebUI 日志流：把日志转发到事件总线，前端可实时查看
    try:
        from webui.events import event_bus
        def _loguru_to_eventbus(message):
            record = message.record
            event_bus.emit_nowait("log", {
                "level": record["level"].name,
                "message": str(record["message"]),
                "module": record["module"],
                "line": record["line"],
            })
        logger.add(_loguru_to_eventbus, level="INFO")
    except Exception as e:
        logger.debug(f"WebUI 日志 sink 注册失败（已忽略）: {e}")

    logger.info("日志配置完成")


async def main():
    config = load_config()
    setup_logging(config)

    # 延迟导入（确保日志先配好）
    from src.bot import QQBot

    bot = QQBot(config)

    # 启动 WebUI（与 Bot 同 event loop，失败不影响主流程）
    webui_runner = None
    try:
        from webui.server import start_webui
        webui_runner = await start_webui(bot, config)
    except Exception as e:
        logger.error(f"WebUI 初始化失败（已忽略）: {e}")

    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("收到退出信号")
    except Exception as e:
        logger.exception(f"启动失败: {e}")
    finally:
        if webui_runner:
            try:
                await webui_runner.cleanup()
            except Exception:
                pass
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
