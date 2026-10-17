#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
魔兽世界钓鱼助手（只提醒，不按键）

作用：通过系统声音回环（WASAPI loopback）实时监听游戏声音，
      听到"上鱼"的水花声就闪屏 + 响提示音，提醒你抬眼看浮标。

      *** 本工具不发送任何按键、不注入游戏进程、不读写游戏内存，
          不构成自动化操作。按不按收杆键，始终由你自己决定。***

用法：
    python fish_alert.py devices      列出可用的回环设备
    python fish_alert.py learn        录一段上鱼声音当样本（强烈建议先做）
    python fish_alert.py run          开始监听（上鱼时通知 vibx_press 自动收杆+抛竿）
    python fish_alert.py self-test    自检：播一段测试音，看能不能检测到

第一次使用顺序：devices -> learn -> run
"""

import argparse
import json
import os
import queue
import socket
import sys
import threading
import time
import wave

try:
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    sys.stderr.reconfigure(errors="replace", line_buffering=True)
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
LIBS = os.path.join(HERE, "libs")
if os.path.isdir(LIBS):
    sys.path.insert(0, LIBS)

import numpy as np  # noqa: E402

try:
    import soundcard as sc  # noqa: E402
except ImportError:
    print("找不到 soundcard 库。请确认 libs/ 目录存在（见 README.md）。")
    raise SystemExit(1)

if sys.platform == "win32":
    import winsound
else:
    winsound = None


# ======================================================================
# 参数
# ======================================================================

SR = 48000                  # 采样率
WIN = 1024                  # 每帧 FFT 长度
HOP = 512                   # 帧移（约 10.7ms 一帧）
NBANDS = 8                  # 频带数（实测 8 带 + 7 帧平滑最稳，见 tools/sweep2.py）
SMOOTH_FRAMES = 7           # 特征的时间平滑帧数：抵消回环带来的时间抖动
FMIN, FMAX = 80.0, 16000.0

LEARN_BACK_START = 2.2      # 学习样本时：从按下回车往前取
LEARN_BACK_END = 0.30       # 留出人的反应时间
TEMPLATE_SECONDS = LEARN_BACK_START - LEARN_BACK_END   # 1.9 秒

DEFAULT_CONFIG = {
    "device": None,                 # 回环设备名（留空 = 默认播放设备）
    "threshold": 0.50,              # 模板相似度阈值，越高越严格（learn 会给出推荐值）
    "cooldown_ms": 1500,            # 两次提醒之间最短间隔
    "energy_gate_db": 8.0,          # 比背景噪声高多少 dB 才做匹配
    # 抛竿/收杆的落水声和"上鱼"的水花声很像（同一个水声素材），只靠频谱分不开。
    # 区别在**上下文**：抛竿落水前一定有一串挥杆/收线的响声，而鱼咬钩前是安静的。
    # 所以匹配上之后，再回头看一眼"窗口之前有没有持续响声"——有就判为抛竿，不震。
    "cast_guard_ms": 1600,          # 回看多长（毫秒）；0 = 关闭这个保护
    "cast_guard_db": 6.0,           # 前导响声要比背景高多少 dB 才算数
    "cast_guard_min_ms": 120,       # 前导响声至少持续多久（毫秒），排除落水声自己的起始脉冲
    "log_skips": True,              # 忽略抛竿落水时，往日志里记一行（方便确认它真的在工作）
    "simple_threshold_db": 14.0,    # 没有样本时的简易能量阈值
    "save_captures": False,
}


def save_config(cfg):
    path = os.path.join(HERE, "config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return path


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    path = os.path.join(HERE, "config.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
        for k, v in user.items():
            cfg[k] = v
    return cfg


# ======================================================================
# 设备
# ======================================================================

def list_devices():
    speakers = sc.all_speakers()
    default = sc.default_speaker()
    print("可用播放设备（同时也是可用的回环设备）：")
    if not speakers:
        print("  （系统里没找到播放设备）")
    for i, s in enumerate(speakers):
        mark = "   <- 默认" if default is not None and s.name == default.name else ""
        print("  [%d] %s%s" % (i, s.name, mark))
    print()
    print("如果游戏声音走的是别的设备，用 --device \"名字的一部分\" 指定。")
    return speakers


def pick_loopback(name=None):
    speakers = sc.all_speakers()
    if not speakers:
        raise RuntimeError("系统里没有可用的播放设备。")

    if name:
        cands = [s for s in speakers if name.lower() in s.name.lower()]
        if not cands:
            raise RuntimeError("找不到名字里含 %r 的播放设备，跑一下 devices。" % name)
        speaker = cands[0]
    else:
        speaker = sc.default_speaker() or speakers[0]

    mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
    if mic is None:
        raise RuntimeError("拿不到 %r 的回环输入。" % speaker.name)
    return speaker, mic


# ======================================================================
# 特征
# ======================================================================

def _make_band_idx():
    edges = np.logspace(np.log10(FMIN), np.log10(FMAX), NBANDS + 1)
    freqs = np.fft.rfftfreq(WIN, 1.0 / SR)
    idx = []
    for i in range(NBANDS):
        lo, hi = edges[i], edges[i + 1]
        sel = np.where((freqs >= lo) & (freqs < hi))[0]
        if len(sel) == 0:
            sel = np.array([int(np.argmin(np.abs(freqs - lo)))])
        idx.append(sel)
    return idx


BAND_IDX = _make_band_idx()
WINDOW_FN = np.hanning(WIN).astype(np.float32)


def frame_features(block):
    """(WIN,) 波形 -> (NBANDS,) 对数频带能量"""
    spec = np.abs(np.fft.rfft(block * WINDOW_FN))
    power = spec * spec
    out = np.empty(NBANDS, dtype=np.float32)
    for i, sel in enumerate(BAND_IDX):
        out[i] = power[sel].sum()
    return np.log10(out + 1e-10)


def frames_of(samples):
    """一段波形 -> (T, NBANDS)"""
    if len(samples) < WIN:
        return np.zeros((0, NBANDS), dtype=np.float32)
    count = 1 + (len(samples) - WIN) // HOP
    out = np.empty((count, NBANDS), dtype=np.float32)
    for k in range(count):
        out[k] = frame_features(samples[k * HOP: k * HOP + WIN])
    return out


def normalize_shape(mat):
    """每列（频带）各自零均值单位方差 —— 对音量不敏感，只看形状"""
    mu = mat.mean(axis=0, keepdims=True)
    sd = mat.std(axis=0, keepdims=True) + 1e-6
    return (mat - mu) / sd


def smooth_frames(mat, k):
    """沿时间轴做移动平均。回环采集出来的音频每个 session 都有几十毫秒级的抖动，
    不平滑的话噪声类声音根本对不上（实测跨段相似度会从 0.72 掉到 0.52）。"""
    if k <= 1 or mat.shape[0] < k:
        return mat
    ker = np.ones(k, dtype=np.float32) / k
    out = np.empty_like(mat)
    for b in range(mat.shape[1]):
        out[:, b] = np.convolve(mat[:, b], ker, mode="same")
    return out


def cosine(a, b):
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ======================================================================
# 录音
# ======================================================================

class LoopbackRecorder(object):
    """后台线程持续录回环音频，保留最近若干秒"""

    def __init__(self, mic, keep_seconds=6.0, block_ms=50):
        self.mic = mic
        self.keep = int(keep_seconds * SR)
        self.block = int(SR * block_ms / 1000)
        self.buf = np.zeros(0, dtype=np.float32)
        self.lock = threading.Lock()
        self.queue = queue.Queue(maxsize=300)
        self.running = False
        self.rec = None
        self.thread = None

    def start(self):
        # 注意：soundcard 的 MediaFoundation 录音器必须在创建它的线程里调用 record()，
        # 否则拿回来的全是静音。所以这里只起线程，录音器在线程内创建。
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        for _ in range(100):            # 等设备就绪
            if self.rec is not None:
                break
            time.sleep(0.05)

    def _loop(self):
        try:
            self.rec = self.mic.recorder(samplerate=SR, channels=None,
                                         blocksize=self.block)
            self.rec.__enter__()
        except Exception as exc:
            print("\n[打不开录音设备] %s" % exc)
            self.running = False
            return
        while self.running:
            try:
                data = self.rec.record(numframes=self.block)
            except Exception as exc:
                print("\n[录音中断] %s" % exc)
                self.running = False
                break
            mono = np.asarray(data, dtype=np.float32)
            if mono.ndim > 1:
                mono = mono.mean(axis=1)
            mono = np.ascontiguousarray(mono)
            with self.lock:
                self.buf = np.concatenate((self.buf, mono))
                if len(self.buf) > self.keep:
                    self.buf = self.buf[-self.keep:]
            try:
                self.queue.put_nowait(mono)
            except queue.Full:
                pass

    def tail(self, seconds):
        with self.lock:
            n = int(seconds * SR)
            if len(self.buf) >= n:
                return self.buf[-n:].copy()
            return self.buf.copy()

    def stop(self):
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        if self.rec is not None:
            try:
                self.rec.__exit__(None, None, None)
            except Exception:
                pass


class Alerter(object):
    """上鱼提醒。原有手柄震动功能已移除；现在的提醒完全交给独立的
    vibx_press 按键程序（收杆+抛竿）。这里保留 Alerter 占位，避免改动主流程。"""

    def __init__(self, cfg):
        self.cfg = cfg

    def start(self):
        print("提醒方式：由 vibx_press 自动收杆+抛竿")

    def fire(self):
        # 震动已移除；上鱼动作由 notify_bite() 通知 vibx_press 处理
        pass


# ======================================================================
# 检测
# ======================================================================

class Detector(object):
    def __init__(self, cfg, template=None):
        self.cfg = cfg
        self.tpl_shape = None
        self.tpl = None
        self.frames = 0
        if template is not None:
            self.tpl = normalize_shape(smooth_frames(template, SMOOTH_FRAMES))
            self.tpl_shape = self.tpl.ravel()
            self.frames = int(template.shape[0])
        self.ring = np.zeros((0, NBANDS), dtype=np.float32)
        self.last_fire = 0.0
        self.armed = True
        self.peak_score = 0.0
        self.skips = 0              # 被判为"抛竿/收杆落水"而没震的次数
        self.last_pre_db = -999.0   # 上一次匹配时，窗口之前的响声比背景高多少 dB
        self.last_skip_score = 0.0

    def _recent_stats(self, end):
        """end 之前最近若干帧的能量分布（log10 域）
        注意必须按 end 取，不能直接取 ring 尾部 —— 批量喂数据时尾部是固定的。"""
        lo = max(0, end - 200)
        seg = self.ring[lo:end]
        if len(seg) == 0:
            return 0.0, 0.0
        per_frame = seg.max(axis=1)
        floor = float(np.percentile(per_frame, 30))
        return float(np.max(per_frame)), floor

    def push(self, feats):
        """feats: (k, NBANDS)。返回触发次数（0/1）"""
        if len(feats) == 0:
            return 0
        self.ring = np.concatenate((self.ring, feats), axis=0)
        limit = max(self.frames, 200) * 3
        if len(self.ring) > limit:
            self.ring = self.ring[-limit:]

        n = len(self.ring)
        first_end = n - len(feats) + 1      # 新帧中第一帧作为窗口末尾时的 end
        fired = 0
        for end in range(first_end, n + 1):
            fired += self._step(end)
        return fired

    def _step(self, end):
        now = time.time()
        top, floor = self._recent_stats(end)
        loud_db = (top - floor) * 10.0        # log10 -> 约等于 dB

        if self.tpl is None:
            if loud_db >= float(self.cfg.get("simple_threshold_db", 14.0)):
                return self._maybe_fire(now)
            return 0

        if end < self.frames:
            return 0

        # 相似度始终计算，方便 --debug 调阈值；能量门槛只用来决定要不要触发
        win = smooth_frames(self.ring[end - self.frames:end], SMOOTH_FRAMES)
        score = cosine(normalize_shape(win).ravel(), self.tpl_shape)
        if score > self.peak_score:
            self.peak_score = score

        thr = float(self.cfg.get("threshold", 0.50))
        if loud_db < float(self.cfg.get("energy_gate_db", 8.0)):
            self.armed = True
            return 0
        if score >= thr:
            if self.armed and self._cast_like(end, floor):
                # 抛竿 / 收杆带起的落水声：前面有一串持续响声（挥杆/收线），不是鱼咬钩
                self.armed = False
                self.last_fire = now          # 把这次和它的余波一起吃掉
                self.skips += 1
                self.last_skip_score = score
                return 0
            return self._maybe_fire(now)
        if score < thr * 0.8:
            self.armed = True
        return 0

    def _cast_like(self, end, floor):
        """匹配窗口之前是否有『持续』的响声（挥杆/收线）——有就判为抛竿落水。

        只看"有没有声音"不够：落水声自己的起始脉冲、以及鱼咬钩前的水面杂音都会误伤。
        所以要求前导响声①高于背景 `cast_guard_db`，②至少持续 `cast_guard_min_ms`。
        """
        guard_ms = float(self.cfg.get("cast_guard_ms", 1600))
        if guard_ms <= 0:
            return False
        gap = int(0.25 * SR / HOP)          # 跳过紧贴窗口的 attack（落水声自己的起始段）
        k = int(guard_ms / 1000.0 * SR / HOP)
        hi = end - self.frames - gap
        lo = max(0, hi - k)
        if hi - lo < 5:
            return False
        seg = self.ring[lo:hi].max(axis=1)
        vals = (seg - floor) * 10.0
        self.last_pre_db = float(np.max(vals))
        thr_db = float(self.cfg.get("cast_guard_db", 6.0))
        need = max(1, int(float(self.cfg.get("cast_guard_min_ms", 120)) / 1000.0 * SR / HOP))
        return self.last_pre_db >= thr_db and int(np.sum(vals >= thr_db)) >= need

    def _maybe_fire(self, now):
        cd = float(self.cfg.get("cooldown_ms", 1500)) / 1000.0
        if not self.armed or (now - self.last_fire) < cd:
            return 0
        self.armed = False
        self.last_fire = now
        return 1


# ======================================================================
# 工具
# ======================================================================

def save_wav(path, samples, sr=SR):
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def read_wav(path):
    with wave.open(path, "rb") as w:
        if w.getnchannels() != 1:
            raise RuntimeError("只支持单声道 wav：%s" % path)
        if w.getframerate() != SR:
            raise RuntimeError("wav 采样率需要是 %d：%s" % (SR, path))
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def play_wav(path):
    if winsound is None:
        return
    winsound.PlaySound(path, winsound.SND_FILENAME)


def template_path():
    return os.path.join(HERE, "template.npy")


def load_template():
    p = template_path()
    return np.load(p) if os.path.exists(p) else None


def pair_score(tpl_feats, live_feats):
    """把一个特征矩阵当模板，在另一个上滑动找最大相似度 —— 和实时检测同一口径。"""
    t = min(tpl_feats.shape[0], live_feats.shape[0])
    if t < 3:
        return 0.0
    tv = normalize_shape(smooth_frames(tpl_feats[:t], SMOOTH_FRAMES)).ravel()
    lv = smooth_frames(live_feats, SMOOTH_FRAMES)
    if len(lv) < t:
        return 0.0
    best = 0.0
    for k in range(len(lv) - t + 1):
        s = cosine(normalize_shape(lv[k:k + t]).ravel(), tv)
        if s > best:
            best = s
    return best


# ======================================================================
# 学习样本
# ======================================================================

def learn(args, cfg):
    if getattr(args, "device", None):
        cfg["device"] = args.device
        with open(os.path.join(HERE, "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print("已把监听设备记到 config.json：%s" % args.device)

    samples_dir = os.path.join(HERE, "samples")
    os.makedirs(samples_dir, exist_ok=True)
    n = int(TEMPLATE_SECONDS * SR)

    if args.from_wav:
        raw = read_wav(args.from_wav)
        seg = raw[:n] if len(raw) >= n else np.pad(raw, (0, n - len(raw)))
        feats = frames_of(seg)
        np.save(template_path(), feats)
        save_wav(os.path.join(samples_dir, "from_wav.wav"), seg)
        print("已从 %s 生成模板：%d 帧" % (args.from_wav, feats.shape[0]))
        return

    speaker, mic = pick_loopback(cfg.get("device"))
    print("监听设备：%s" % speaker.name)
    rec = LoopbackRecorder(mic, keep_seconds=6.0)
    rec.start()
    time.sleep(0.8)

    print()
    print("操作方式：")
    print("  1) 进游戏，抛竿开始钓鱼")
    print("  2) 听到「上鱼」的水花声，立刻按回车")
    print("  3) 程序会把回车前 %.2f 秒截出来放给你听，你确认对不对" % TEMPLATE_SECONDS)
    print("  收 1~3 段就够（多收几段更准）。输入 q 回车结束。")
    print()

    collected = []
    try:
        while True:
            cmd = input("听到水花声立刻回车（q 结束）> ").strip().lower()
            if cmd == "q":
                break
            seg = rec.tail(LEARN_BACK_START)
            if len(seg) < n:
                print("  音频还不够长，再等一会儿。")
                continue
            end = len(seg) - int(LEARN_BACK_END * SR)
            piece = seg[max(0, end - n):end]
            if len(piece) < n:
                piece = np.pad(piece, (n - len(piece), 0))

            idx = len(collected) + 1
            path = os.path.join(samples_dir, "sample_%d.wav" % idx)
            save_wav(path, piece)
            print("  截取 %.2f 秒 -> %s" % (TEMPLATE_SECONDS, os.path.basename(path)))
            play_wav(path)
            ans = input("  回放的是水花声吗？[y/n] > ").strip().lower()
            if ans == "y":
                collected.append(frames_of(piece))
                print("  收下第 %d 段。" % len(collected))
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass
                print("  丢弃，重来。")
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        rec.stop()

    if not collected:
        print("没收集到样本，模板未更新。")
        return

    length = min(s.shape[0] for s in collected)
    stacked = np.stack([s[:length] for s in collected], axis=0)
    tpl = stacked.mean(axis=0)
    np.save(template_path(), tpl)
    print()
    print("模板已保存：template.npy（%d 帧，来自 %d 段样本）" % (tpl.shape[0], len(collected)))

    # 用"互相比对"估一个合理的阈值：同一声音两次采集之间能到多少分，
    # 阈值取这个分数的 85%，既不会漏也不会被随便什么声音触发。
    if len(collected) >= 2:
        scores = []
        for i in range(len(collected)):
            for j in range(len(collected)):
                if i != j:
                    scores.append(pair_score(collected[i], collected[j]))
        scores = [s for s in scores if s > 0]
        if scores:
            mid = float(np.median(scores))
            lo = float(np.min(scores))
            rec = round(min(0.90, max(0.35, mid * 0.85)), 2)
            print()
            print("样本之间互相匹配的分数：中位 %.3f，最低 %.3f" % (mid, lo))
            print("建议阈值：%.2f（已写入 config.json）" % rec)
            cfg["threshold"] = rec
            cfg["_auto_threshold_from"] = {"median": round(mid, 3),
                                           "min": round(lo, 3),
                                           "samples": len(collected)}
            path = os.path.join(HERE, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        else:
            print("样本之间相似度过低，可能采到的是别的声音，建议重新采。")
    else:
        print("只采到 1 段，没法估阈值。建议再采 1~2 段，或者直接 run 后看 --debug 输出。")

    print()
    print("接下来： python fish_alert.py run")


# ======================================================================
# 运行
# ======================================================================

# ======================================================================
# 静默运行支持（无窗口时看不到 print，所以写日志；并防重复启动）
# ======================================================================

LOG_PATH = os.path.join(HERE, "fishing-alert.log")
CONTROL_PORT = 49777          # 本地单实例锁 + 退出通道，只绑 127.0.0.1
NOTIFY_PORT = 49778           # 上鱼信号推送口：把「咬钩」通知给独立按键小程序（只绑 127.0.0.1）
LOG_MAX_BYTES = 512 * 1024

_lock_sock = None
_notify_sock = None           # 上鱼信号推送：服务端 socket
_notify_clients = []          # 已连接、等待信号的客户端 socket 列表
_notify_lock = threading.Lock()
_quit_event = threading.Event()
_silent_mode = False      # True = 跑在 pythonw 里，没有控制台


class _LogTee(object):
    """把 print 的内容写进日志文件；有控制台时同时打一份。"""

    def __init__(self, path, console):
        self.f = open(path, "a", encoding="utf-8", buffering=1)
        self.console = console

    def write(self, s):
        if not s:
            return
        if self.console:
            try:
                sys.__stdout__.write(s)
            except Exception:
                pass
        try:
            self.f.write(s)
        except Exception:
            pass

    def flush(self):
        try:
            self.f.flush()
        except Exception:
            pass

    def isatty(self):
        return False


def setup_logging(force=False):
    """静默运行（pythonw 下 sys.stdout 为 None）或 --log 时，把输出接进日志文件。"""
    global _silent_mode
    console = sys.stdout is not None
    if not force and console:
        return
    _silent_mode = not console
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            os.replace(LOG_PATH, LOG_PATH + ".1")
    except Exception:
        pass
    tee = _LogTee(LOG_PATH, console)
    sys.stdout = tee
    sys.stderr = tee
    print("\n===== %s 启动 =====" % time.strftime("%Y-%m-%d %H:%M:%S"))


def acquire_single_instance():
    """用本地端口做单实例锁；已经有实例在跑就返回 False。"""
    global _lock_sock
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", CONTROL_PORT))
    except OSError:
        s.close()
        return False
    s.listen(4)
    s.settimeout(0.5)
    _lock_sock = s
    return True


def start_control_listener():
    """接受本机连接：收到 quit 就退出；其它指令回报 running。"""

    def loop():
        while not _quit_event.is_set():
            try:
                conn, _ = _lock_sock.accept()
            except (socket.timeout, OSError):
                continue
            try:
                conn.settimeout(1.0)
                cmd = conn.recv(64).decode("utf-8", "ignore").strip().lower()
                if cmd in ("quit", "stop", "exit"):
                    print("[%s] 收到退出指令。" % time.strftime("%H:%M:%S"))
                    conn.sendall(b"bye\n")
                    _quit_event.set()
                else:
                    conn.sendall(b"running\n")
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    threading.Thread(target=loop, daemon=True).start()


def start_notify_server():
    """开一个上鱼信号推送口（NOTIFY_PORT）。独立的按键小程序连上来后，
    每次咬钩（alerter.fire()）都会收到一条 "bite"。只绑本机，失败静默。"""
    global _notify_sock
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", NOTIFY_PORT))
        s.listen(8)
        s.settimeout(0.5)
    except OSError:
        try:
            s.close()
        except Exception:
            pass
        return
    _notify_sock = s
    threading.Thread(target=_notify_loop, daemon=True).start()


def _notify_loop():
    """接受按键程序的长连接，并定期清掉已断开的连接。"""
    while not _quit_event.is_set() and _notify_sock is not None:
        try:
            conn, _ = _notify_sock.accept()
        except (socket.timeout, OSError):
            conn = None
        if conn is not None:
            conn.settimeout(0.5)
            with _notify_lock:
                # 只保留最新一个客户端：新连接接管，旧的先断开（避免多实例重复动作）
                for old in _notify_clients:
                    try:
                        old.close()
                    except Exception:
                        pass
                _notify_clients.clear()
                _notify_clients.append(conn)
        with _notify_lock:
            drop = [c for c in _notify_clients if c.fileno() == -1]
            for c in drop:
                try:
                    c.close()
                except Exception:
                    pass
                _notify_clients.remove(c)


def notify_bite():
    """向所有已连接的按键小程序推送一次「咬钩」。不阻塞、失败静默。"""
    if not _notify_clients:
        return
    drop = []
    with _notify_lock:
        for c in _notify_clients:
            try:
                c.sendall(b"bite\n")
            except OSError:
                drop.append(c)
        for c in drop:
            try:
                c.close()
            except Exception:
                pass
            _notify_clients.remove(c)


def run(args, cfg):
    if args.threshold is not None:
        cfg["threshold"] = args.threshold
    if args.device:
        cfg["device"] = args.device

    setup_logging(force=getattr(args, "log", False))
    if not acquire_single_instance():
        print("已有实例在运行（本地端口 %d 被占用），本次不重复启动。" % CONTROL_PORT)
        print("  停止：双击「停止钓鱼.cmd」　查看状态：双击「查看状态.cmd」")
        return
    start_control_listener()
    start_notify_server()   # 开放上鱼信号口（供按键小程序订阅，可有可无）
    print("日志文件：%s" % LOG_PATH)

    template = load_template()
    if template is None:
        print("!! 没有 template.npy，先用简易能量检测（误报会偏多）。")
        print("!! 建议先跑： python fish_alert.py learn")
        print()

    speaker, mic = pick_loopback(cfg.get("device"))
    print("监听设备：%s" % speaker.name)
    if template is not None:
        print("模式：模板匹配   阈值 %.2f   冷却 %dms"
              % (cfg["threshold"], cfg["cooldown_ms"]))
    else:
        print("模式：简易能量   阈值 %.1f dB   冷却 %dms"
              % (cfg["simple_threshold_db"], cfg["cooldown_ms"]))
    if template is not None:
        if float(cfg.get("cast_guard_ms", 1600)) > 0:
            print("抛竿保护：开（回看 %.1fs，前导 ≥ %.1f dB 且持续 ≥ %dms）"
                  % (float(cfg.get("cast_guard_ms", 1600)) / 1000.0,
                     float(cfg.get("cast_guard_db", 6.0)),
                     int(cfg.get("cast_guard_min_ms", 120))))
        else:
            print("抛竿保护：关（cast_guard_ms=0）")
    if _silent_mode:
        print("静默运行中（没有窗口）。停止：双击「停止钓鱼.cmd」　看状态：双击「查看状态.cmd」\n")
    else:
        print("按 Ctrl+C 退出。\n")

    alerter = Alerter(cfg)
    alerter.start()
    rec = LoopbackRecorder(mic, keep_seconds=6.0)
    rec.start()
    det = Detector(cfg, template)

    stop_flag = threading.Event()

    def worker():
        pending = np.zeros(0, dtype=np.float32)
        count = 0
        last_report = time.time()
        while not stop_flag.is_set() and not _quit_event.is_set() and rec.running:
            try:
                block = rec.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            pending = np.concatenate((pending, block))
            feats = []
            while len(pending) >= WIN:
                feats.append(frame_features(pending[:WIN]))
                pending = pending[HOP:]
            if feats:
                skips_before = det.skips
                if det.push(np.stack(feats, axis=0)):
                    count += 1
                    # 注意：这里不要用 \a —— 控制台收到 BEL 字符会真的响一声
                    print("[%s] 上鱼了！(第 %d 次，相似度 %.3f)"
                          % (time.strftime("%H:%M:%S"), count, det.peak_score))
                    det.peak_score = 0.0
                    alerter.fire()
                    notify_bite()   # 通知按键小程序：这次上鱼了
                    if cfg.get("save_captures"):
                        cap_dir = os.path.join(HERE, "captures")
                        os.makedirs(cap_dir, exist_ok=True)
                        save_wav(os.path.join(cap_dir, "hit_%s.wav"
                                              % time.strftime("%H%M%S")),
                                 rec.tail(2.0))
                elif det.skips > skips_before and cfg.get("log_skips", True):
                    print("[%s] 忽略疑似抛竿落水（前导 %.1f dB，相似度 %.3f）"
                          % (time.strftime("%H:%M:%S"), det.last_pre_db,
                             det.last_skip_score))
            if args.debug and time.time() - last_report > 1.0:
                last_report = time.time()
                print("    [debug] 峰值相似度 %.3f   当前能量差 %.1f dB   前导 %.1f dB"
                      % (det.peak_score, _loud_db(det), det.last_pre_db))

    th = threading.Thread(target=worker, daemon=True)
    th.start()

    try:
        while th.is_alive() and not _quit_event.is_set():
            time.sleep(0.5)
        if _quit_event.is_set():
            print("已退出监听。")
    except KeyboardInterrupt:
        print("\n退出中…")
    finally:
        stop_flag.set()
        rec.stop()
        global _lock_sock, _notify_sock
        if _lock_sock is not None:
            try:
                _lock_sock.close()
            except Exception:
                pass
            _lock_sock = None
        if _notify_sock is not None:
            try:
                _notify_sock.close()
            except Exception:
                pass
            _notify_sock = None
        for c in list(_notify_clients):
            try:
                c.close()
            except Exception:
                pass
        _notify_clients.clear()


def _loud_db(det):
    top, floor = det._recent_stats(len(det.ring))
    return (top - floor) * 10.0


# ======================================================================
# 引导启动（双击 开始钓鱼.cmd 走这里）
# ======================================================================

def guided_start(cfg):
    print("=" * 58)
    print("  钓鱼提醒助手  ——  只提醒，不按键")
    print("=" * 58)
    print()

    if load_template() is None:
        print("还没有录过「上鱼声音」样本，先做一次校准（只需要做一次）。")
        print()
        print("接下来：")
        print("  1) 切到游戏，抛竿钓鱼")
        print("  2) 听到上鱼的水花声，立刻回到这个窗口按回车")
        print("  3) 程序会回放刚截到的声音，你确认是不是水花声")
        print()
        try:
            ans = input("现在开始录制吗？[y/n] > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            return
        if ans != "y":
            print()
            print("已取消。下次双击再录，或在终端里跑： .\\run.cmd learn")
            return

        print()
        learn(argparse.Namespace(from_wav=None, device=None), cfg)

        if load_template() is None:
            print()
            print("没有录到样本，先不启动监听。")
            return

        print()
        try:
            input("按回车开始监听 > ")
        except (KeyboardInterrupt, EOFError):
            print()
            return
    else:
        auto = cfg.get("_auto_threshold_from")
        print("已找到声音样本，直接开始监听。")
        print("当前阈值：%s" % cfg.get("threshold"))
        if auto:
            print("（这个阈值是根据 %d 段样本自动算的）" % auto.get("samples", 0))
        print("想重新校准：删掉 template.npy，或在终端跑 .\\run.cmd learn")
        print()

    run(argparse.Namespace(threshold=None, device=None, debug=False), cfg)


# ======================================================================
# 自检
# ======================================================================


def self_test(args, cfg):
    if args.device:
        cfg["device"] = args.device
    if getattr(args, "threshold", None) is not None:
        cfg["threshold"] = args.threshold
    path = args.wav or os.path.join(HERE, "samples", "test_splash.wav")
    if not os.path.exists(path):
        print("找不到测试音 %s" % path)
        print("先生成： python tools/make_test_sound.py")
        return

    template = load_template()
    if template is None:
        print("没有 template.npy。可以先用测试音生成一个：")
        print("  python fish_alert.py learn --from-wav \"%s\"" % path)
        return

    speaker, mic = pick_loopback(cfg.get("device"))
    print("监听设备：%s" % speaker.name)
    rec = LoopbackRecorder(mic, keep_seconds=6.0)
    rec.start()
    det = Detector(cfg, template)
    time.sleep(0.5)

    hits = 0
    best_score = 0.0
    for i in range(args.times):
        time.sleep(args.gap)
        print("播放第 %d 次…" % (i + 1))
        det.ring = np.zeros((0, NBANDS), dtype=np.float32)
        det.peak_score = 0.0
        det.armed = True
        det.last_fire = 0.0
        play_wav(path)              # 阻塞到播完（约 1 秒）
        time.sleep(0.4)             # 给回环一点缓冲时间
        audio = rec.tail(1.8)       # 立刻取，别先睡
        got = det.push(frames_of(audio))
        hits += 1 if got else 0
        best_score = max(best_score, det.peak_score)
        print("   -> %s（峰值相似度 %.3f，能量差 %.1f dB）"
              % ("检测到" if got else "没检测到", det.peak_score, _loud_db(det)))

    rec.stop()
    print()
    print("结果：%d / %d 命中，最高相似度 %.3f" % (hits, args.times, best_score))
    if best_score <= 0.05:
        print("完全没抓到 —— 大概率是设备选错了。跑 devices 看看，")
        print("再用 --device \"音箱名字的一部分\" 指定游戏声音实际走的那个。")
    else:
        rec_thr = round(min(0.90, max(0.30, best_score * 0.85)), 2)
        print("按这次的结果，把 config.json 里的 threshold 设成 %.2f 比较合适" % rec_thr)
        if hits == 0:
            print("（现在是 %.2f，偏高所以一次都没触发）" % float(cfg.get("threshold", 0.50)))
        print("也可以直接改 config.json，或者临时用 --threshold %.2f" % rec_thr)


# ======================================================================
# main
# ======================================================================

def main():
    ap = argparse.ArgumentParser(description="魔兽世界钓鱼助手（只提醒，不按键）")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("devices", help="列出可用回环设备")

    p_learn = sub.add_parser("learn", help="录一段上鱼声音当样本")
    p_learn.add_argument("--from-wav", help="直接从 wav 生成模板（调试用）")
    p_learn.add_argument("--device", help="回环设备名（部分匹配，会记到 config.json）")

    p_run = sub.add_parser("run", help="开始监听")
    p_run.add_argument("--threshold", type=float, help="覆盖相似度阈值")
    p_run.add_argument("--device", help="回环设备名（部分匹配）")
    p_run.add_argument("--debug", action="store_true", help="每秒打印分值，用来调阈值")
    p_run.add_argument("--log", action="store_true",
                       help="把输出写进 fishing-alert.log（静默运行时自动开）")

    p_st = sub.add_parser("self-test", help="自检：播放测试音看能否检测到")
    p_st.add_argument("--wav", help="测试音路径")
    p_st.add_argument("--times", type=int, default=3)
    p_st.add_argument("--gap", type=float, default=1.5)
    p_st.add_argument("--threshold", type=float, help="覆盖相似度阈值")
    p_st.add_argument("--device", help="回环设备名")

    args = ap.parse_args()
    cfg = load_config()

    try:
        if args.cmd is None:
            guided_start(cfg)
        elif args.cmd == "devices":
            list_devices()
        elif args.cmd == "learn":
            learn(args, cfg)
        elif args.cmd == "run":
            run(args, cfg)
        elif args.cmd == "self-test":
            self_test(args, cfg)
    except RuntimeError as exc:
        print("错误：%s" % exc)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
