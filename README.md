# Easy-betterfishing · 魔兽世界钓鱼助手

**上鱼声 → 自动收杆 + 自动抛竿的完整循环。**

识别到「上鱼」的水花声后，本工具会**自动替你收杆、再自动抛竿**，
整个过程不需要再动手柄或键盘。配合 BetterFishing 插件（滚轮键位）一起使用。

> ⚠️ 这会替你按键，属于游戏自动化的范畴，请自行评估封号风险后再使用。
> 代码里没有任何发声、弹窗或闪屏提醒（手柄震动功能也已移除）。

---

## 快速开始

### 一键启动（推荐）

双击 **`EasyFishing.cmd`**，它会**同时拉起两个程序**：

1. **fish_alert**（声音识别）—— 上鱼时往本机发信号
2. **vibx_press**（自动按键）—— 收到信号后自动收杆 + 抛竿

会弹出两个带日志的窗口，分别就是这两个程序，方便你确认它们都在跑。
停止方式：直接关掉两个窗口，或 `Ctrl+C`。

### 分步启动

| 想做什么 | 双击 |
| --- | --- |
| 只启动识别 | `start-alert.cmd` |
| 启动自动按键（管理员） | `start-vibx-admin.cmd` |
| 一键启动识别+按键 | `EasyFishing.cmd` |
| 重录上鱼样本 | `录样本.cmd` |
| 停止 fish_alert（发退出指令） | `停止钓鱼.cmd` |

> **按键程序必须以管理员运行**：游戏（WowB.exe）是高权限进程，普通权限的合成按键
> 会被 Windows 的 UIPI 机制拦截（表现为"手动正常、自动没反应"）。
> `start-vibx-admin.cmd` 已自动请求管理员权限，本机 UAC 已被抑制，不会弹窗。
> 一定要用 **`start-vibx-admin.cmd`**（或 `EasyFishing.cmd`），不要用普通启动。

---

## 它是怎么工作的

```
进游戏抛竿 → 听到上鱼水花声
  → fish_alert 识别（阈值 0.85）
  → 往 127.0.0.1:49778 发信号
  → vibx_press 自动：收杆(WHEEL_DOWN 下滚) → 间隔 → 抛竿(WHEEL_UP 上滚)
```

滚轮方向与 BetterFishing 插件在鱼竿上的绑定**完全一致**：
- **下滚 = 交互（收杆）**
- **上滚 = 钓鱼（抛竿）**

BetterFishing 收杆需要**连续滚动**才能完成起竿，程序默认连滚 3 次（`reel_ticks`）。

---

## 关键配置

### 时序（`vibx_press.json`）

起竿到抛竿的间隔**必须大于**上鱼到收杆的延迟，给收竿动画留足时间，否则鱼拉不起来。

```json
{
  "key": "WHEEL_DOWN",        // 收杆 = 下滚（交互）
  "cast": "WHEEL_UP",         // 抛竿 = 上滚（钓鱼）
  "enable_cast": true,        // false = 只收杆不抛竿
  "reel_ticks": 3,            // 收杆连续滚动次数（BetterFishing 需连滚才完成起竿）
  "delay_min_ms": 250,        // 上鱼 → 收杆 随机延迟
  "delay_max_ms": 500,
  "cast_gap_min_ms": 1400,    // 收杆 → 抛竿 随机间隔（等起竿完成）
  "cast_gap_max_ms": 2200,
  "hold_ms": 35,              // 按住时长（仅键盘键，滚轮忽略）
  "bring_to_front": true,     // 动作前把游戏窗口切到前台
  "inject": "sendinput"       // 按键注入方式
}
```

### 识别（`config.json`）

```json
{
  "threshold": 0.85,          // 相似度阈值，越高越严格
  "cooldown_ms": 1500,        // 两次触发最小间隔
  "energy_gate_db": 8.0,      // 比背景噪声高多少 dB 才匹配
  "cast_guard_ms": 900,       // 抛竿保护：回看窗口
  "cast_guard_db": 12.0,      // 前导响声高出背景多少 dB 才算数
  "cast_guard_min_ms": 200,
  "save_captures": true       // true = 每次触发把声音存到 captures/ 供排查
}
```

> 模板 `template.npy` 基于线上抓到的真上鱼声重建（样本互相比对 0.996）。
> 目前识别稳定，如确需重录：用 `录样本.cmd`。

---

## 当前版本基线（已在用）

- **收竿** = `WHEEL_DOWN`（下滚，连滚 3 次）
- **抛竿** = `WHEEL_UP`（上滚）
- **上鱼→收竿**：随机 250~500 ms
- **收竿→抛竿**：随机 1400~2200 ms
- **识别阈值**：0.85
- 手柄震动：**已移除**

---

## 常见问题

**Q：收竿不见鱼 / 上鱼没反应**
- 按键程序要是**管理员**运行的，普通权限会被 UIPI 拦截。
- 确认两个窗口都在（识别 + 按键）。
- 起竿到抛竿间隔是否被改小到 < 起竿动画时间。

**Q：收杆了但没抛竿**
`enable_cast` 是否为 true；`cast_gap` 是否够大（让拾取/起竿完成）。

**Q：一直误触发或漏触发**
调 `config.json` 的 `threshold`（高=更严格，低=更敏感）和 `energy_gate_db`。

**Q：换了电脑/耳机要重录吗**
先用识别程序跑一下看相似度，明显偏低就 `录样本.cmd` 重录。

---

## 环境依赖（已打包）

- Python 3.12（`D:\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe`）
- `libs/`：已内置 `soundcard` + `cffi` + `pycparser`（本机版，Python 3.12）

启动脚本会自动优先用 DSH 自带的 Python；换机器时需保证 Python 3.12 且重新放好 `libs/`。

---

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `EasyFishing.cmd` | **一键启动**（识别 + 按键） |
| `fish_alert.py` | 声音识别 + 上鱼信号（监听 49777/49778） |
| `vibx_press.py` | 自动收杆 + 自动抛竿（管理员运行） |
| `config.json` | 识别参数（阈值 / 抛竿保护） |
| `vibx_press.json` | 按键动作与时序 |
| `template.npy` | 上鱼声模板（当前良好，勿随意覆盖） |
| `libs/` | Python 依赖库 |
| `start-alert.cmd` | 单独启动识别 |
| `start-vibx-admin.cmd` | 单独启动自动按键（管理员） |
| `start-vibx.cmd` | 普通权限启动按键（**不推荐**，会因 UIPI 失效） |
| `录样本.cmd` | 重录上鱼声音样本 |
| `停止钓鱼.cmd` | 向识别程序发送退出指令 |
