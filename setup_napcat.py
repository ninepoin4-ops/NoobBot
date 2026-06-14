"""NapCat 一键配置脚本。

背景：NoobBot 通过正向 WebSocket 连接 NapCat（默认 ws://127.0.0.1:3001），
但 NapCat 出厂不会自动开任何 WS 服务，必须手动配置 onebot11_<QQ>.json。
本脚本替用户把这件事做了，让"开箱即用"名副其实。

做什么：
  1. 询问 QQ 号（+可选端口，默认 3001）
  2. 在 napcat/napcat/config/ 下生成/更新 onebot11_<QQ>.json，
     写入一个正向 WebSocket Server（host=127.0.0.1, postFormat=string）
  3. messagePostFormat 必须是 string —— bot 代码用 [CQ:at,qq=...] 字符串判定 @ 提及

幂等：已存在的配置只补/改 websocketServers，不动其它字段。

用法：
  python setup_napcat.py
  python setup_napcat.py --qq 123456
  python setup_napcat.py --qq 123456 --port 3001 --token mytoken
"""
from __future__ import annotations
import argparse
import json
import os
import secrets
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAPCAT_CONFIG_DIR = ROOT / "napcat" / "napcat" / "config"
DEFAULT_PORT = 3001  # 与 config/config.yaml 的 napcat.forward.ws_url 对齐

# 最小可用的 onebot11 配置模板。messagePostFormat 必须 string，
# 否则 NapCat 上报 array 格式消息段，bot 的 [CQ:at,qq=] 字符串匹配会失效。
DEFAULT_ONEBOT11_TEMPLATE = {
    "network": {
        "httpServers": [],
        "httpSseServers": [],
        "httpClients": [],
        "websocketClients": [],
        "plugins": [],
    },
    "musicSignUrl": "",
    "enableLocalFile2Url": True,  # bot 生图/日报要用 url 形式取图片
    "parseMultMsg": False,
    "imageDownloadProxy": "",
    "timeout": {
        "baseTimeout": 10000,
        "uploadSpeedKBps": 0,
        "downloadSpeedKBps": 0,
        "maxTimeout": 1800000,
    },
}


def is_port_free(host: str, port: int) -> bool:
    """检查端口是否空闲（未被监听）。被占用返回 False。

    始终用 127.0.0.1 做实际 bind 测试——即使 host=0.0.0.0，
    如果 0.0.0.0:port 已被占用，127.0.0.1:port 也会 bind 失败；
    而 127.0.0.1 bind 成功则说明本机该端口空闲。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def make_ws_server_entry(port: int, token: str) -> dict:
    """构造一个正向 WS server 配置项。

    messagePostFormat='string' 是关键：bot 依赖 [CQ:at,qq=...] 字符串。
    """
    return {
        "enable": True,
        "name": "noobbot",
        "host": "127.0.0.1",  # 只本机连，最安全；要远程改这里
        "port": port,
        "messagePostFormat": "string",
        "reportSelfMessage": False,
        "token": token,
        "enableForcePushEvent": True,
        "debug": False,
        "heartInterval": 30000,
    }


def load_existing(path: Path) -> dict:
    """读取已存在的 onebot11 配置；损坏或不存在则返回 None。"""
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def merge_ws_server(config: dict, port: int, token: str) -> tuple[dict, str]:
    """把正向 WS 项合并进 config['network']['websocketServers']。

    返回 (新 config, 动作描述)。已存在同端口则覆盖，否则追加。

    健壮性：NapCat 配置常被用户手改，network 可能不是 dict、
    websocketServers 可能不是 list。这里强制规整，避免崩溃。
    """
    # network 必须是 dict；若被手改成其它类型则重置为空 dict
    if not isinstance(config.get("network"), dict):
        config["network"] = {}
    network = config["network"]

    # 兜底：旧配置可能缺某些子字段，或被手改成非 list
    for key in ("httpServers", "httpSseServers", "httpClients",
                "websocketClients", "plugins"):
        if not isinstance(network.get(key), list):
            network[key] = []

    servers = network.setdefault("websocketServers", [])
    if not isinstance(servers, list):
        servers = []
        network["websocketServers"] = servers

    new_entry = make_ws_server_entry(port, token)

    replaced = False
    for i, s in enumerate(servers):
        if not isinstance(s, dict):
            continue
        # 同端口或同名都视为同一项，避免重复监听
        if s.get("port") == port or s.get("name") == new_entry["name"]:
            servers[i] = new_entry
            replaced = True
            break
    if not replaced:
        servers.append(new_entry)

    action = "更新" if replaced else "新增"
    return config, action


def write_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def ensure_napcat_dir() -> bool:
    """检查 napcat 配置目录是否存在（即 NapCat 是否已解压到位）。"""
    return NAPCAT_CONFIG_DIR.exists()


def regenerate_webui_token_if_default() -> bool:
    """若 webui.json 里残留的是已知默认 token，替换成新的随机值。

    背景：项目分发的 webui.json 可能带开发者本机的 token，属于敏感残留。
    只在 token 是已知默认值（e6248adcb724）时才动，避免覆盖用户已改的值。
    返回是否替换。
    """
    KNOWN_DEV_TOKEN = "e6248adcb724"
    webui_path = NAPCAT_CONFIG_DIR / "webui.json"
    if not webui_path.exists():
        return False
    try:
        with open(webui_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False
    if cfg.get("token") != KNOWN_DEV_TOKEN:
        return False
    cfg["token"] = secrets.token_hex(6)
    with open(webui_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return True


def validate_qq(qq: str) -> bool:
    """QQ 号合法性：纯数字、4~12 位。"""
    return qq.isdigit() and 4 <= len(qq) <= 12


def main():
    parser = argparse.ArgumentParser(
        description="一键配置 NapCat 的正向 WebSocket，让 NoobBot 能连上",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="无参数运行会进入交互模式；CI/自动化场景用 --qq 指定。",
    )
    parser.add_argument("--qq", help="用于登录的 QQ 号")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"正向 WS 监听端口（默认 {DEFAULT_PORT}，与 bot 配置对齐）")
    parser.add_argument("--token", default="",
                        help="WS 访问 token（留空=不鉴权；设了的话两边要一致）")
    parser.add_argument("--host", default="127.0.0.1",
                        help="WS 监听地址（默认 127.0.0.1 仅本机；远程连改 0.0.0.0）")
    parser.add_argument("--non-interactive", action="store_true",
                        help="非交互模式：缺参数直接报错退出，不提示输入")
    args = parser.parse_args()

    print("=" * 50)
    print("  NapCat 配置助手 / NapCat WebSocket Setup")
    print("=" * 50)

    # 1. 检查 napcat 目录
    if not ensure_napcat_dir():
        print()
        print("❌ 未找到 NapCat 配置目录:")
        print(f"   {NAPCAT_CONFIG_DIR}")
        print()
        print("   请先按 README 把 NapCat 解压到 napcat/ 目录，")
        print("   确保路径 napcat/napcat/launcher-user.bat 存在。")
        sys.exit(1)

    # 2. 收集 QQ 号
    qq = args.qq or ""
    if not qq and not args.non_interactive:
        try:
            qq = input("\n请输入用于登录的 QQ 号: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            sys.exit(1)
    if not validate_qq(qq):
        print(f"❌ QQ 号无效: {qq!r}（需 4~12 位纯数字）")
        sys.exit(1)

    # 3. 端口检查
    port = args.port
    if not is_port_free("127.0.0.1", port):
        # 不阻止——可能是 NapCat 自己已经在监听（首次配置不是这种情况，
        # 但用户重跑脚本时 NapCat 可能已起）。只提示。
        print(f"⚠️  端口 {port} 当前已被占用（可能是 NapCat 已在运行）。")
        print("   如果是首次配置请先关掉占用该端口的程序。")

    # 4. 写配置
    target = NAPCAT_CONFIG_DIR / f"onebot11_{qq}.json"
    existing = load_existing(target)
    if existing is None:
        print(f"\n📝 创建新配置: {target.name}")
        config = json.loads(json.dumps(DEFAULT_ONEBOT11_TEMPLATE))  # 深拷贝
    else:
        print(f"\n📝 更新已有配置: {target.name}")
        config = existing

    # host 参数：默认 127.0.0.1，用户可改 0.0.0.0
    config, action = merge_ws_server(config, port, args.token)
    # 覆盖 host（merge_ws_server 用了 127.0.0.1 默认，这里尊重用户 --host）
    for s in config["network"]["websocketServers"]:
        if s.get("port") == port:
            s["host"] = args.host

    write_config(target, config)
    print(f"   ✅ 已{action}正向 WebSocket: {args.host}:{port}")

    # 5. 同步提示 bot 侧配置
    print()
    print("📋 Bot 侧（config/config.yaml）应当匹配以下设置：")
    print(f"   napcat.mode: forward")
    print(f"   napcat.forward.ws_url: ws://{args.host}:{port}")
    if args.token:
        print(f"   napcat.forward.access_token: {args.token}  (forward 模式鉴权)")
        print("   注意：forward 模式默认走 127.0.0.1 本机回环，通常不需要设 token；")
        print("         只有把 NapCat WS 暴露到网络时才需要。")
    print("   （默认值已对齐，除非你改过端口/token，否则无需动 config.yaml）")

    # 6. 顺手清理 webui.json 残留 token
    if regenerate_webui_token_if_default():
        print()
        print("🔐 检测到 webui.json 残留开发者 token，已替换为随机值。")
        print("   （如需进 NapCat WebUI，请查看 napcat/napcat/config/webui.json 的 token 字段）")

    print()
    print("=" * 50)
    print("  ✅ 配置完成！接下来：")
    print("  1. 运行 start.bat（或直接启动 NapCat + python main.py）")
    print("  2. 在弹出的 NapCat 窗口完成 QQ 登录")
    print("  3. 看到 Bot 日志「✅ 已连接到 NapCat」即成功")
    print("=" * 50)


if __name__ == "__main__":
    main()
