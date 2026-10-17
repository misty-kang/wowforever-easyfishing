# -*- coding: utf-8 -*-
"""独立按键小程序：收到「上鱼」信号 → 随机人体延迟 → 收杆（默认滚轮下）→（随机间隔）→ 抛竿（默认滚轮上）。

默认形成完整钓鱼循环：滚轮下=交互收杆，滚轮上=抛竿，命中 BetterFishing 在装备鱼竿时绑定的滚轮键。
也可配置成按键盘键（字母、数字、SPACE / ENTER / F1~F12）。

与 fish_alert.py 解耦：
  - fish_alert.py 在咬钩（=手柄震动）那一刻，向本机 NOTIFY_PORT 推一条 "bite"。
  - 本程序连上那个端口，收到 "bite" 后执行上述序列，用 Windows 最底层的 SendInput 触发滚轮/按键。

本程序可单独运行、单独停用；不开它时 fish_alert.py 照常只震动提醒，功能不受影响。

用法：
  pythonw vibx_press.py            # 静默常驻（配合 vibx_press-静默.vbs）收杆+自动抛竿
  python  vibx_press.py --no-cast  # 只收杆不抛竿（不形成循环）
  python  vibx_press.py --key F    # 前台运行；收杆改为按键盘 F
  python  vibx_press.py --dry      # 只监听不做事（测试信号通不通）
  python  vibx_press.py test       # 自测：1.5 秒后执行一次完整序列（不连服务器）

配置（可选，放同目录 vibx_press.json）：
  {
    "host": "127.0.0.1",
    "port": 49778,
    "key": "WHEEL_DOWN",      # 收杆动作
    "cast": "WHEEL_UP",       # 抛竿动作
    "enable_cast": true,      # 收杆后是否自动抛竿
    "delay_min_ms": 200,      # 上鱼→收杆 随机延时区间
    "delay_max_ms": 600,
    "cast_gap_min_ms": 400,   # 收杆→抛竿 随机间隔区间
    "cast_gap_max_ms": 900,
    "hold_ms": 35             # 按住时长（仅键盘键，滚轮忽略）
  }
命令行参数优先于配置文件。
"""
import json
import os
import random
import socket
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "vibx_press.json")
LOG_PATH = os.path.join(HERE, "vibx_keypress.log")

_KEYLOG = None            # 打开后追加写，避免高频重复 open


def _log(msg):
    """把运行情况写到 vibx_keypress.log（pythonw 无窗口时也能看到）。"""
    global _KEYLOG
    try:
        if _KEYLOG is None:
            _KEYLOG = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
        _KEYLOG.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass

DEFAULT = {
    "host": "127.0.0.1",
    "port": 49778,
    "key": "~",                   # 收杆（交互起竿）：数字 1 左边的 ~ 键
    "cast": "1",                  # 抛竿动作：键盘数字键 1
    "enable_cast": True,          # 收杆后是否自动抛竿（False = 只收杆，不形成循环）
    "delay_min_ms": 200,          # 上鱼→收杆 的随机人体反应延时
    "delay_max_ms": 600,
    "cast_gap_min_ms": 400,       # 收杆→抛竿 之间的随机间隔（等收杆/拾取完成）
    "cast_gap_max_ms": 900,
    "reel_ticks": 1,          # 收杆滚轮连续缺口次数（BetterFishing 需连滚才完成起竿）
    "hold_ms": 35,
    "bring_to_front": True,   # 发动作前先把游戏窗口拉到前台（防动作落到聊天/桌面）
    "inject": "sendinput",    # 滚轮注入方式：sendinput(喂raw input，对魔兽这类raw输入游戏有效) / postmessage
}

# 常用键盘 VK 码。字母没列的会用大写字母的 ASCII 兜底。
VK_DICT = {
    "X": 0x58, "F": 0x46, "Z": 0x5A, "C": 0x43, "V": 0x56, "B": 0x42, "G": 0x47,
    "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34, "5": 0x35,
    "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39, "0": 0x30,
    "SPACE": 0x20, "ENTER": 0x0D, "ESC": 0x1B, "TAB": 0x09,
    "`": 0xC0, "~": 0xC0,                      # VK_OEM_3（数字 1 左边的反引号/波浪号）
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74,
    "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
}


def load_config(args):
    cfg = dict(DEFAULT)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update({k: v for k, v in json.load(f).items() if k in cfg})
        except Exception as exc:
            print("[config] 读取 %s 失败：%s（用默认值）" % (CONFIG_PATH, exc))
    if args.key:
        cfg["key"] = args.key
    if args.cast:
        cfg["cast"] = args.cast
    if args.no_cast:
        cfg["enable_cast"] = False
    if args.port:
        cfg["port"] = args.port
    if args.delay_min is not None:
        cfg["delay_min_ms"] = args.delay_min
    if args.delay_max is not None:
        cfg["delay_max_ms"] = args.delay_max
    if args.gap_min is not None:
        cfg["cast_gap_min_ms"] = args.gap_min
    if args.gap_max is not None:
        cfg["cast_gap_max_ms"] = args.gap_max
    if args.hold is not None:
        cfg["hold_ms"] = args.hold
    if cfg["delay_max_ms"] < cfg["delay_min_ms"]:
        cfg["delay_max_ms"] = cfg["delay_min_ms"]
    if cfg["cast_gap_max_ms"] < cfg["cast_gap_min_ms"]:
        cfg["cast_gap_max_ms"] = cfg["cast_gap_min_ms"]
    return cfg


def vk_of(name):
    if name is None:
        return None
    n = str(name).strip().upper()
    if n in VK_DICT:
        return VK_DICT[n]
    if len(n) == 1 and "A" <= n <= "Z":
        return ord(n)
    return None


def resolve_action(name):
    """把配置里的按键名解析成一个动作。
    返回 ("key", vk) 表示发键盘按键；("wheel", delta) 表示鼠标滚轮。
    滚轮向下/向上分别用 WHEEL_DOWN / WHEEL_UP（或 MWS、WHEEL）。"""
    n = str(name or "").strip().upper()
    if n in ("WHEEL_DOWN", "WHEEL", "MWS", "MOUSEWHEEL"):
        return ("wheel", -120)          # 一个滚轮缺口（向下）
    if n == "WHEEL_UP":
        return ("wheel", 120)
    return ("key", vk_of(n))


# ---------- SendInput ----------
from ctypes import Structure, Union, WINFUNCTYPE, byref, c_long, c_void_p, sizeof, windll, wintypes

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008        # 用扫描码注入，raw input 游戏才认
MAPVK_VK_TO_VSC = 0
INPUT_KEYBOARD = 1
INPUT_MOUSE = 0
MOUSEEVENTF_WHEEL = 0x0800


def _ts():
    """YYYY-MM-DD HH:MM:SS.mmm：strftime 不支持 %f，毫秒手动拼。"""
    now = time.localtime()
    base = time.strftime("%H:%M:%S", now)
    ms = int((time.time() * 1000) % 1000)
    return "%s.%03d" % (base, ms)


class KEYBDINPUT(Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", c_void_p),
    ]


class MOUSEINPUT(Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", c_long),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", c_void_p),
    ]


class HARDWAREINPUT(Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _inp(vk, flags):
    """构造键盘输入事件。wVk 填虚拟键，wScan 填扫描码，并走扫描码路径——
    这样 raw input 游戏（如魔兽）收到的和真实物理按键一致。"""
    i = INPUT()
    i.type = INPUT_KEYBOARD
    i.u.ki.wVk = vk
    i.u.ki.wScan = windll.user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    i.u.ki.dwFlags = flags | KEYEVENTF_SCANCODE
    return i


# ---------- 拉前台：确保滚轮/按键落到游戏而不是聊天/桌面 ----------
_GAME_TITLES = ("魔兽", "world of warcraft", "wow", "战争")


def _game_hwnd():
    """枚举可见顶层窗口，返回第一个像游戏主窗口的句柄（没有则 None）。"""
    user32 = windll.user32
    kernel32 = windll.kernel32
    found = []

    @WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd_, _lp):
        if not user32.IsWindowVisible(hwnd_):
            return True
        buf = (wintypes.WCHAR * 256)()
        user32.GetWindowTextW(hwnd_, buf, 256)
        title = buf.value or ""
        if any(k in title for k in _GAME_TITLES):
            found.append(hwnd_)
        return True

    user32.EnumWindows(_cb, 0)
    return found[0] if found else None


def _force_foreground(hwnd):
    """把指定窗口可靠地拉到前台（绕过 Windows 的前台锁）。"""
    user32 = windll.user32
    kernel32 = windll.kernel32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                 # SW_RESTORE
    # 前台锁绕行：模拟一次 Alt 键按放，通常能获得置前授权
    user32.keybd_event(0x12, 0, 0, 0)              # VK_MENU down
    user32.keybd_event(0x12, 0, 2, 0)              # VK_MENU up
    cur = kernel32.GetCurrentThreadId()
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    attached = (cur != fg_tid)
    if attached:
        user32.AttachThreadInput(cur, fg_tid, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur, fg_tid, False)


def bring_game_to_front():
    hwnd = _game_hwnd()
    if hwnd is None:
        _log("未找到游戏窗口（标题不含魔兽/World of Warcraft），跳过拉前台")
        return False
    try:
        _force_foreground(hwnd)
        time.sleep(0.06)
        return True
    except Exception as e:
        _log("拉前台失败: %s" % e)
        return False


def send_key(vk, hold_ms):
    """按下 → 保持 hold_ms → 松开（真按一下，比瞬时点按更可靠）。"""
    d = _inp(vk, 0)
    u = _inp(vk, KEYEVENTF_KEYUP)
    darr = (INPUT * 1)(d)
    uarr = (INPUT * 1)(u)
    windll.user32.SendInput(1, byref(darr), sizeof(INPUT))
    if hold_ms and hold_ms > 0:
        time.sleep(hold_ms / 1000.0)
    windll.user32.SendInput(1, byref(uarr), sizeof(INPUT))


def send_wheel(delta, ticks=1):
    """SendInput 方式：需要有焦点的窗口，可能被游戏的合成输入拦截。

    ticks 为滚轮缺口次数：有的插件（如 BetterFishing）收竿需要连续滚动才
    完成起竿，单缺口往往只触发"交互"而拉不起竿，手动连续滚就有效。
    """
    i = INPUT()
    i.type = INPUT_MOUSE
    i.u.mi.dwFlags = MOUSEEVENTF_WHEEL
    i.u.mi.mouseData = delta
    for _ in range(max(1, int(ticks))):
        windll.user32.SendInput(1, byref(i), sizeof(INPUT))


WM_MOUSEWHEEL = 0x020A
_GAME_HWND = None


def send_wheel_post(delta):
    """PostMessage 方式：把 WM_MOUSEWHEEL 直接发给游戏窗口本身。

    等效手动滚轮走的消息通路，绕开「是否前台」和「合成输入被拦截」两个问题。
    """
    global _GAME_HWND
    hwnd = _GAME_HWND
    if hwnd is None:
        hwnd = _game_hwnd()
        if hwnd is None:
            _log("找不到游戏窗口，跳过滚轮")
            return False
        _GAME_HWND = hwnd
    try:
        pt = wintypes.POINT()
        windll.user32.GetCursorPos(byref(pt))
        lparam = (pt.x & 0xFFFF) | (pt.y << 16)        # MAKELPARAM(x, y) 屏幕坐标
        wparam = (delta & 0xFFFF) << 16                # 高位放滚轮增量
        windll.user32.PostMessageW(hwnd, WM_MOUSEWHEEL, wparam, lparam)
        return True
    except Exception as e:
        _log("PostMessage 滚轮失败: %s" % e)
        return False


def do_action(action, hold_ms, inject="postmessage", ticks=1):
    """action: ("key", vk) 或 ("wheel", delta)。ticks 为滚轮缺口次数。"""
    kind, val = action
    if kind == "wheel":
        if inject == "sendinput":
            send_wheel(val, ticks)
        else:
            send_wheel_post(val)
    else:
        if val is None:
            return False
        send_key(val, hold_ms)
    return True


def press_sequence(cfg, reel_action, cast_action):
    """上鱼后的完整序列：拉前台 → 随机人体延时 → 收杆 →（可选）随机间隔 → 抛竿。"""
    if cfg.get("bring_to_front", True):
        bring_game_to_front()
    delay = random.uniform(cfg["delay_min_ms"], cfg["delay_max_ms"]) / 1000.0
    if delay > 0:
        time.sleep(delay)
    if not do_action(reel_action, cfg["hold_ms"], cfg.get("inject", "postmessage"),
                     ticks=cfg.get("reel_ticks", 1)):
        _log("收杆动作无效: %r" % (cfg["key"],))
        print("[%s] 收杆动作名无效：%r" % (_ts(), cfg["key"]))
        return
    _log("REEL %s (+%.0fms)" % (cfg["key"], delay * 1000))
    print("[%s] 收杆 %s（+%.0fms）" % (_ts(), cfg["key"], delay * 1000))
    if cfg.get("enable_cast", True) and cfg.get("cast"):
        gap = random.uniform(cfg["cast_gap_min_ms"], cfg["cast_gap_max_ms"]) / 1000.0
        if gap > 0:
            time.sleep(gap)
        if not do_action(cast_action, cfg["hold_ms"], cfg.get("inject", "postmessage")):
            _log("抛竿动作无效: %r" % (cfg["cast"],))
            print("[%s] 抛竿动作名无效：%r" % (_ts(), cfg["cast"]))
            return
        _log("CAST %s (+%.0fms)" % (cfg["cast"], gap * 1000))
        print("[%s] 抛竿 %s（+%.0fms）" % (_ts(), cfg["cast"], gap * 1000))


# ---------- 监听逻辑 ----------
def run_listener(cfg, dry=False):
    reel = None if dry else resolve_action(cfg["key"])
    cast = None
    if not dry:
        cast = resolve_action(cfg["cast"]) if cfg.get("enable_cast", True) and cfg.get("cast") else None
        if reel[1] is None or (cast is not None and cast[1] is None):
            print("无法识别的动作（key=%r cast=%r），可改 vibx_press.json 或用 --key/--cast。"
                  % (cfg["key"], cfg["cast"]))
            return 2
    mode = "收杆+自动抛竿" if cast is not None else ("收杆" if reel is not None else "仅监听")
    print("按键小程序：等待上鱼信号 %s:%d → %s，序列 = %s →%s %s"
          % (cfg["host"], cfg["port"], mode,
             cfg["key"],
             (" %s~%s ms " % (cfg["cast_gap_min_ms"], cfg["cast_gap_max_ms"])) if cast is not None else " ",
             cfg["cast"] if cast is not None else ""))
    _log("启动 %s | 信号 %s:%s | %s | 延时 %.0f~%.0f ms | 间隔 %.0f~%.0f ms"
         % (mode, cfg["host"], cfg["port"],
            "->".join(str(a) for a in [cfg["key"]] + (["+" + cfg["cast"]] if cast is not None else [])),
            cfg["delay_min_ms"], cfg["delay_max_ms"],
            cfg["cast_gap_min_ms"], cfg["cast_gap_max_ms"]))

    def worker():
        while True:
            try:
                with socket.create_connection((cfg["host"], cfg["port"]),
                                              timeout=5.0) as s:
                    s.settimeout(30.0)
                    buf = b""
                    while True:
                        data = s.recv(256)
                        if not data:
                            break
                        buf += data
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            msg = line.decode("utf-8", "ignore").strip().lower()
                            if msg == "bite":
                                if dry:
                                    _log("收到上鱼信号 (dry)")
                                    print("[%s] 收到上鱼信号（dry 模式，不按键）"
                                          % time.strftime("%H:%M:%S"))
                                else:
                                    _log("收到上鱼信号，开始收杆+抛竿序列")
                                    threading.Thread(
                                        target=press_sequence,
                                        args=(cfg, reel, cast), daemon=True).start()
            except (OSError, socket.timeout):
                pass
            time.sleep(1.0)  # 重连退避

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n已停止。")


# ---------- 自测 ----------
def run_selftest(cfg):
    reel = resolve_action(cfg["key"])
    cast = resolve_action(cfg["cast"]) if cfg.get("enable_cast", True) and cfg.get("cast") else None
    if reel[1] is None or (cast is not None and cast[1] is None):
        print("无法识别的动作（key=%r cast=%r）。" % (cfg["key"], cfg["cast"]))
        return 2
    label = "%s + 抛竿%s" % (cfg["key"], cfg["cast"]) if cast is not None else cfg["key"]
    print("自测：1.5 秒后执行 [%s]，请把焦点放在游戏里（装备鱼竿状态）。" % label)
    time.sleep(1.5)
    press_sequence(cfg, reel, cast)
    print("完成。若游戏先收杆再接抛竿，说明链路正常。")
    return 0


def main():
    import argparse
    ap = argparse.ArgumentParser(description="上鱼信号 → 人体延迟 → 收杆(→ 抛竿)")
    ap.add_argument("cmd", nargs="?", default="run", help="run / test")
    ap.add_argument("--key", default=None, help="收杆动作，如 WHEEL_DOWN、X、F、F1")
    ap.add_argument("--cast", default=None, help="抛竿动作，如 WHEEL_UP")
    ap.add_argument("--no-cast", action="store_true", help="只收杆不抛竿（关闭自动循环）")
    ap.add_argument("--port", type=int, default=None, help="信号端口（默认 49778）")
    ap.add_argument("--delay-min", type=int, default=None, help="上鱼→收杆最小延时毫秒")
    ap.add_argument("--delay-max", type=int, default=None, help="上鱼→收杆最大延时毫秒")
    ap.add_argument("--gap-min", type=int, default=None, help="收杆→抛竿最小间隔毫秒")
    ap.add_argument("--gap-max", type=int, default=None, help="收杆→抛竿最大间隔毫秒")
    ap.add_argument("--hold", type=int, default=None, help="按住时长毫秒（仅键盘键）")
    ap.add_argument("--dry", action="store_true", help="只监听不做事（测信号通不通）")
    args = ap.parse_args()
    cfg = load_config(args)
    if args.cmd == "test":
        return run_selftest(cfg)
    return run_listener(cfg, dry=args.dry)


if __name__ == "__main__":
    sys.exit(main())
