# Noob Bot 🤖
解压即用的QQbot-下载-解压-启动install.bat-启动start.bat-开始使用
> 一个带 WebUI 控制面板的 QQ 群聊 AI Bot，开箱即用、零外部依赖前端、支持思维链实时观测。
>
> A QQ group-chat AI bot with a built-in WebUI dashboard, real-time chain-of-thought observation, hot-reloadable config, and a layered memory system.

---

## ✨ 特性 / Features

- **🧠 四层记忆系统 / 4-layer memory**
  - 短期 Buffer（当前群聊上下文）
  - 长期向量记忆（Chroma 语义检索，跨会话）
  - 世界书 LoreBook（关键词触发设定注入）
  - 上下文压缩（超长对话 LLM 自动摘要）
- **💭 思维链实时观测 / Real-time chain-of-thought**
  - WebUI 实时展示 `收到消息 → 决策 → 工具调用 → 回复` 全流程
  - 实时日志流，按级别过滤、关键词搜索
- **⚙️ 智能热更新 / Smart hot-reload**
  - 冷却时间、限流、Bot 别名等改完即时生效
  - LLM 配置一键重建客户端
  - 连接/端口类配置保存后提示重启
- **🧠 记忆管理 / Memory management**
  - 短期记忆按群查看/清理
  - 长期记忆语义搜索/删除
- **🎯 技能系统 / Skill system**
  - 关键词触发，触发词可在 WebUI 热改（无需改代码）
  - 内置：生图（GPT Image 2）、群日报（话题/称号/金句/质量锐评）
- **🔧 工具调用 / Tool calling**
  - OpenAI Function Calling，LLM 自主决策
  - 内置：联网搜索、天气、定时提醒、当前时间
- **📊 仪表盘 / Dashboard**
  - NapCat 连接状态、调度任务、活跃群、记忆统计一目了然
- **🖥️ 零依赖前端 / Zero-dependency frontend**
  - 原生 HTML/CSS/JS，无 CDN，离线可用
  - 浅色简约主题

---

## 🏗️ 架构 / Architecture

```
┌──────────────────────────────────────────────────┐
│  main.py  (单进程，单 event loop)                 │
│                                                  │
│  ┌──────────────┐    ┌────────────────────────┐ │
│  │   QQBot 核心  │←──│   aiohttp WebUI 服务   │ │
│  │  (内存中状态) │    │   app['bot'] = bot     │ │
│  └──────────────┘    └────────────────────────┘ │
│         ↑                      ↑                 │
│         │  EventBus 事件总线    │ WebSocket 推送  │
│         └──────────┬───────────┘                 │
│            浏览器订阅事件流                       │
└──────────────────────────────────────────────────┘
```

WebUI 与 Bot 同进程同 event loop，所有状态内存直读，零 IPC。

---

## 📦 项目结构 / Project Structure

```
noobbot/
├── main.py                  # 入口（同时启动 Bot + WebUI）
├── start.bat                # 一键启动 NapCat + Bot
├── config/
│   ├── config.yaml          # 主配置
│   └── .env                 # API key（自行填写）
├── src/
│   ├── bot.py               # Bot 主调度器
│   ├── napcat/client.py     # OneBot v11 协议客户端
│   ├── engine/
│   │   ├── activator.py     # 主动活跃引擎（决策链）
│   │   └── scheduler.py     # 定时任务调度
│   ├── memory/manager.py    # 四层记忆系统
│   ├── llm/client.py        # LLM 客户端 + 人格 prompt
│   ├── models/schemas.py    # 数据模型
│   └── tools/registry.py    # 工具注册中心
├── skills/                  # 技能系统（保留扩展）
│   ├── base.py              # 技能基类
│   ├── manager.py           # 技能管理器
│   ├── sheng_tu.py          # 生图技能
│   └── group_report/        # 群日报技能
├── webui/                   # WebUI 控制面板
│   ├── server.py            # aiohttp 入口
│   ├── events.py            # 事件总线
│   ├── hot_reload.py        # 配置热更新
│   ├── handlers/            # API handlers
│   └── static/              # 前端单页应用
└── requirements.txt
```

---

## 🚀 快速开始 / Quick Start

### 环境要求 / Requirements

- **Python 3.10+**
- **QQ NT**（PC 版 QQ）
- **NapCat**（OneBot 协议端，需单独下载）

### 步骤 / Steps

1. **克隆仓库 / Clone**
   ```bash
   git clone https://github.com/<your-username>/noobbot.git
   cd noobbot
   ```

2. **安装依赖 / Install dependencies**
   ```bash
   pip install -r requirements.txt
   playwright install chromium   # 群日报图片渲染用
   ```

3. **放置 NapCat / Place NapCat**
   - 下载 [NapCat](https://github.com/NapNeko/NapCatQQ) Release
   - 解压到项目根目录的 `napcat/` 文件夹（使 `napcat/napcat/launcher-user.bat` 路径存在）

4. **配置 API Key / Configure API Key**
   - 编辑 `config/.env`：
     ```
     LLM_API_KEY_HASH=<你的 DeepSeek key 去掉 sk- 前缀>
     ```
   - 或直接在 `config/config.yaml` 的 `llm.api_key` 填完整 key

5. **启动 / Start**
   - 双击 `start.bat`
   - 按提示扫码登录 QQ
   - 登录完成后按任意键，Bot + WebUI 自动启动

6. **打开 WebUI / Open WebUI**
   - 浏览器访问 `http://127.0.0.1:8081`

---

## 🖥️ WebUI 控制面板 / WebUI Dashboard

启动后在浏览器打开 `http://127.0.0.1:8081`：

| 页面 | 说明 / Description |
|---|---|
| 📊 **仪表盘** | Bot 在线状态、各模块概览、调度任务、记忆统计 |
| 💭 **思维链** | 实时事件流：收到消息 → 决策 → 工具调用 → 回复 的完整处理过程 |
| ⚙️ **配置** | 表单化编辑所有配置，按字段标注「热生效/需重建/需重启」 |
| 🧠 **记忆** | 短期记忆查看/清理；长期记忆语义搜索/删除 |
| 🎯 **技能** | 技能启停、触发词编辑（即时生效，无需改源码） |
| 🔧 **工具** | 工具开关、参数 schema 查看 |
| 📋 **日志** | 实时日志流，按级别过滤、关键词搜索 |

### 配置热更新策略 / Hot-reload strategy

- **热生效**（绿色）：bot 名称、冷却时间、限流参数、随机回复概率、工具开关等，改完立即生效。
- **需重建**（橙色）：LLM 相关配置（model/api_key/temperature 等），保存后点击「重建 LLM 客户端」生效。
- **需重启**（红色）：NapCat 连接、WebUI 端口、短期记忆轮数等，重启 Bot 进程后生效。

所有改动都会自动持久化到 `config/config.yaml`。

---

## ⚙️ 配置说明 / Configuration

主配置文件 `config/config.yaml`：

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `bot.name` | Bot 角色名（人设名） | `小白` |
| `bot.master_id` | 主人 QQ（主人发言时 Bot 更亲近） | 空 |
| `llm.model` | LLM 模型名 | `deepseek-chat` |
| `llm.temperature` | 温度（越高越随机） | `0.8` |
| `engagement.random_reply_frequency` | 随机插话概率 (0~1) | `0.08` |
| `engagement.bot_names` | Bot 别名（消息含此词触发回复） | `[小白]` |
| `cooldown.global_cooldown` | 全局冷却（秒） | `3` |
| `cooldown.rate_limit.max_count` | 每群每分钟最大回复数 | `20` |
| `memory.long_term.enabled` | 启用长期向量记忆 | `true` |
| `webui.port` | WebUI 端口 | `8081` |

> 💡 大部分配置可在 WebUI 的「配置」页实时修改，无需重启。

---

## 🎭 默认人格 / Default Persona

默认人格为 **小白** —— 一个可爱的 AI 助手：
- 可爱、温柔、热情、有点小俏皮
- 说话亲切自然，偶尔卖萌但不做作
- 适当使用颜文字，如 (｡•̀ᴗ-)✧

人格定义在 `src/llm/client.py` 的 `generate_system_prompt()`，可自由修改。
Bot 角色名可在 WebUI 配置页或 `config.yaml` 修改。

---

## 📜 参考项目 / Acknowledgements

本项目在设计与实现上参考了以下优秀开源项目：

### [NapCat / NapCatQQ](https://github.com/NapNeko/NapCatQQ)
> 基于 NTQQ 的现代化 OneBot 协议端实现。
>
> A modern OneBot protocol implementation based on NTQQ.

Noob Bot 通过 NapCat 接入 QQ，使用其提供的 OneBot v11 WebSocket 接口收发消息。NapCat 是本项目运行的核心依赖。

- 官网：https://napneko.github.io
- GitHub：https://github.com/NapNeko/NapCatQQ

### [ChatLuna](https://github.com/ChatLunaLab/chatluna)
> 多平台大模型接入插件，可扩展，支持多种输出格式。
>
> A multi-platform LLM integration plugin, extensible with multiple output formats.

Noob Bot 的以下设计灵感来源于 ChatLuna：
- **活跃决策链 / Engagement decision tree**：参考 ChatLuna 的 `allow_reply.ts` 决策树（@提及、引用回复、名字匹配、随机插话的链式检查）
- **四层记忆架构 / 4-layer memory**：参考 ChatLuna 的 memory pipeline（BufferMemory → VectorMemory → LoreBook → Compressor）
- **LoreBook 世界书 / LoreBook**：参考 ChatLuna 的 LoreBookMatcher，实现关键词触发的设定注入
- **上下文压缩 / Context compression**：参考 ChatLuna 的 `infinite_context.ts` → `compressIfNeeded()`

- GitHub：https://github.com/ChatLunaLab/chatluna
- 文档：https://chatluna.chat

---

## 📄 License

MIT License - 详见 [LICENSE](LICENSE) 文件。

---

## ⚠️ 免责声明 / Disclaimer

本项目仅供学习和个人使用。使用时请遵守：
- [QQ 用户协议](https://rules.qq.com/)
- 相关法律法规
- API 服务商的使用条款

作者不对使用本项目造成的任何后果负责。
