# omarchy-voice-control — 项目需求文档（Requirements）

| 项 | 值 |
|---|---|
| 项目名称 | `omarchy-voice-control` |
| 版本 | v0.1（需求基线） |
| 日期 | 2026-09-03 |
| 类型 | Omarchy 原生插件 / 桌面语音控制助手（local-first，安全先行） |
| 状态 | 需求已审计，待实现 |

> 本文档为单一事实来源（SSOT）。所有实现必须满足「第 3 章 安全与隐私原则」的硬约束。

---

## 1. 项目概述

在 Omarchy 桌面上提供一个**小而可见**的语音控制助手：

- 顶栏常驻一个主题感知的助手图标，点击显示/隐藏助手弹窗（popover）。
- 弹窗内一个大麦克风按钮：点击开始监听、再点停止；用户**先看**识别文本与解析出的动作，**显式确认后**才执行放行清单（allowlist）内的安全桌面动作。
- 支持中文与英文语音指令。
- **这不是通用 shell 助手**：绝不直接执行语音原文或 LLM 原文，只执行经过规则解析、严格类型化、并通过策略放行与风险确认的受控动作。

产品目标：日常桌面高频操作（打开/聚焦应用、打开文件/文件夹、切工作区、关窗、全屏、截图、锁屏）用一句话完成，全程本地、可解释、可审计、可回滚。

---

## 2. 环境审计（实测基线）

> 下列数据为 2026-09-03 在本机实测获得，是实现的硬约束与兼容性基线。任何实现不得假设与本表不一致的版本或语法。

### 2.1 Omarchy

| 项 | 实测值 |
|---|---|
| 包版本 | `4.0.2-1`（`omarchy version`） |
| `/usr/share/omarchy/version` | `4.0.0.alpha` |
| Shell 架构 | **Quickshell（Quattro）单进程**：`omarchy-shell` 承载 bar、通知、overlay、插件 |
| 打包源码位置 | `/usr/share/omarchy/shell/`（**只读，禁止修改**） |
| 插件宿主目录 | `~/.config/omarchy/plugins/<plugin-id>/`（用户自有，可热重载） |
| 插件清单 | `manifest.json`（`schemaVersion: 1`，`kinds`：`bar-widget`/`service`/`panel`，`entryPoints`：`barWidget`/`service`/`panel`） |
| 栏布局配置 | `~/.config/omarchy/shell.json`（`bar.layout.left/center/right`，热重载） |
| Shell IPC | `omarchy-shell <target> <method> [args...]`，例：`omarchy-shell shell toggle <plugin>`、`omarchy-shell shell rescanPlugins` |
| 插件管理 | `omarchy plugin add|clone|list|remove|enable|disable|validate` |
| 已装用户插件（参考模式） | `max.scene`（bar-widget + Scene.qml + ConfigPanel.qml）、`gmaxxxie.fcitx5-theme`（service）、`saif.system-stats` 等 |
| 打包插件可参考模式 | `/usr/share/omarchy/shell/plugins/panels/*`（BarWidget.qml + Panel.qml 弹窗模式）、`notifications`（Service.qml） |

**结论**：Omarchy 采用 Quattro 插件架构；本项目按 `bar-widget` 插件 + 可选 `service` 组件建模，全部代码落在 `~/.config/omarchy/plugins/`（用户自有目录），不触碰 `/usr/share/omarchy/`。

### 2.2 Hyprland / hyprctl

| 项 | 实测值 |
|---|---|
| Hyprland | `0.56.2`（branch v0.56.2） |
| hyprctl | `/usr/bin/hyprctl`，0.56.x 语法 |
| 配置目录 | `~/.config/hypr/`（Lua：`bindings.lua` 等） |
| 可用只读命令 | `hyprctl binds -j`、`activewindow -j`、`activeworkspace -j`、`clients -j`、`workspaces -j`、`monitors -j`、`getoption`、`configerrors` |
| 可用动作命令 | `hyprctl dispatch <dispatcher> [args]`、`hyprctl reload`、`hyprctl kill` |
| 系统级动作 | `omarchy system lock`、`omarchy capture screenshot [region|fullscreen] [slurp|copy|save]` |

**实现约束**：
- 执行器只调用上述**已在本机验证存在**的命令与 JSON 字段（`activewindow.class/title/initialClass`、`workspaces.id/name` 等），不硬编码旧版语法。
- 所有 `hyprctl` 调用使用 `-j` 输出 + 本地 JSON 解析（python3/jq），超时与失败纳入状态机。

### 2.3 Voxtype

| 项 | 实测值 |
|---|---|
| 版本 | `1.0.0`，`/usr/bin/voxtype`（分发包装 → `voxtype-onnx-migraphx`） |
| 守护进程 | systemd user 服务 `voxtype.service`（active；主进程 `voxtype-onnx-migraphx daemon` + `voxtype-osd-gtk4`，~1.5G 内存） |
| 配置 | `~/.config/voxtype/config.toml`：`engine="sensevoice"`、`model="small-fp32"`、`language="zh"`、`use_itn=true`、`output.mode="type"`、`fallback_to_clipboard=true`、`post_process=opencc t2s` |
| 状态文件 | `/run/user/1000/voxtype/state`（`idle`/`recording`/`transcribing`）；`voxtype status --format json --extended` 返回 `model/device/backend` |
| 录音命令（守护进程） | `voxtype record start|stop|toggle|cancel`（向守护进程发 SIGUSR1/SIGUSR2） |
| 文件转写 | `voxtype transcribe <file>`（WAV 16kHz mono） |
| 输出模式（schema） | `output.mode ∈ type | clipboard | paste | file`（`file` 追加写 `output.file_path`；type 需 ydotool/wtype，clipboard 需 wl-copy） |
| GPU/Vulkan | `voxtype setup gpu --enable/--disable/--status`；`voxtype info accel|models|devices|engines` |

**关键实测结论（决定 STT 架构）**：
1. `voxtype transcribe <wav>` **将最终文本输出到 stdout，不注入任何文本**（实测返回「嗯。」，exit 0，未打字/未改剪贴板）。这是**唯一可靠的无注入转录通道**。
2. 守护进程 `voxtype record stop` **总是**按 `output.mode` 注入（type 打字到聚焦应用 / clipboard 改剪贴板），**没有**事件/hook/文件回调的纯转录通道 → **不得**用于语音控制录音。
3. 因此：**自行录音**（16kHz mono WAV）→ `voxtype transcribe <wav>` → stdout 取文。复用 Voxtype 本地模型（SenseVoice small-fp32 zh）与后端；不修改用户 `config.toml`（否则破坏正常听写），按调用传 `--engine/--model` 覆盖。
4. `output.mode="file"` 是官方文档化选项，但它是**全局**配置，会改变用户正常听写（SUPER+H）行为 → 不作为本项目方案，仅作上游提案参考。

### 2.4 `SUPER + X` 快捷键冲突审计

| 组合 | 当前占用 | 来源 |
|---|---|---|
| `SUPER + X` | **「Universal cut」**（发送 CTRL+X） | `/usr/share/omarchy/default/hypr/bindings/clipboard.lua:47`；`hyprctl binds -j` 实测确认（modmask 64 + key X → dispatcher `__lua`，desc "Universal cut"） |
| `SUPER + SHIFT + X` | X（webapp，https://x.com/） | 默认 `applications.lua:32` |
| `SUPER + SHIFT + ALT + X` | X Post（webapp） | 默认 `applications.lua:33` |
| `SUPER + CTRL + X` | Toggle dictation（Voxtype） | 默认/既有绑定 |

**结论**：`SUPER + X` **已被占用**。按本需求第 4.6 节，注册前先检测，检测到冲突则**不覆盖**、报告冲突并向用户征询替代快捷键。**用户已选定 `SUPER + SHIFT + V`**（2026-09-03 `hyprctl binds -j` 与默认/当前配置双重验证空闲），确认后写入 `~/.config/hypr/bindings.lua`。

---

## 3. 安全与隐私原则（硬约束）

> 以下为不可违背的硬性约束（违反即视为需求不满足）。

1. **绝不执行原始输入**：语音原文、识别文本、LLM/引擎输出，一律不得直接作为命令执行。
2. **只执行严格类型化的 Action**：一切动作必须先经 `intent/` 规则解析为符合第 11 章 schema 的 Action 对象，再经 `policy/` 放行。
3. **动作白名单**：仅允许固定集合的桌面动作（第 11 章 `type` 枚举）。白名单外指令一律拒绝并提示。
4. **显式确认**：`risk = "confirm_required"` 的动作（不可逆、需提升、有副作用，见第 8 章）必须用户显式点击确认后才执行；`low` 动作也建议默认要求一次确认（可配置为免确认）。
5. **本地优先**：全部语音处理本地完成（本机 Voxtype 模型/后端）；无云端、无遥测、无账号。
6. **无监听期不录音**：弹窗隐藏、超时、用户取消时必须立即停止录音并丢弃未完成音频；**绝不静默后台录音**。
7. **不篡改用户系统配置**：不修改 `/usr/share/omarchy/`、不改用户 Voxtype 正常听写行为、不 monkey-patch 打包文件。
8. **不使用黑客式集成**：禁止按键捕获、剪贴板抓取、焦点欺骗、时序 hack 作为生产集成。
9. **可审计**：每次执行的 Action（原文→意图→目标→风险→确认→结果）记录到本地日志，用户可查看。

---

## 4. 产品需求（UX）

### 4.1 顶栏图标（bar-widget）
- 在 Omarchy 顶栏提供一个**紧凑、主题感知**的图标按钮（跟随当前主题 foreground/accent；复用现有 bar-widget 模式，如 `max.scene`/`panels/clock`）。
- 点击图标 → 切换弹窗显隐（见 4.3）。
- 图标可视状态反映助手状态：空闲 / 监听中 / 处理中 / 等待确认（用不同前景色或小圆点）。
- 经 `shell.json` 的 `bar.layout.*` 放置（默认左侧或右侧，用户可 `omarchy bar move` 调整）。

### 4.2 弹窗 Popover
- 点击图标弹出助手面板；必须同时良好支持**鼠标、触控、键盘**。
- 键盘可用性：按钮可 Tab 聚焦、Enter/Space 触发、Escape 关闭。
- 弹出定位贴近图标（复用 Quickshell Popup 面板模式，参考 `panels/*/Panel.qml`）。
- 尺寸自适应内容；弹窗内不出现横向滚动。

### 4.3 录音流程
- 弹窗内一个**大麦克风按钮**：点击「开始监听」→ 按钮变为「停止」，再次点击停止并转写。
- 监听中 UI 显示录音状态（红色圆点 / 「Listening…」）。
- 停止后进入转写（STT），完成后在弹窗内展示结果（见 4.4）。
- 录音与转写全程本地；见第 5 章。

### 4.4 界面信息展示（5 段）
弹窗内按顺序展示：
1. **识别文本（transcript）**：STT 最终文本（中文/英文原文）。
2. **解析动作（action）**：规则解析出的 `type` 与可读描述（例：`open_app` / `focus_app` / `open_file`…）。
3. **解析目标（target）**：解析出的具体对象（应用名/路径/工作区号/窗口）。
4. **风险/确认状态（risk）**：`low` 或 `confirm_required`；若需确认，显示「确认执行 / 取消」按钮。
5. **执行结果（result）**：成功或失败，失败含原因（超时/权限/找不到目标）。

### 4.5 关闭 / 隐藏行为
- **关闭方式**：点击外部、按 `Escape`、再次点击顶栏图标。
- **隐藏即停止**：弹窗隐藏时若正在监听，**立即停止录音并丢弃未完成音频**，绝不静默后台录音。
- 隐藏不中断已完成的转写结果展示；再次打开显示最近一次会话（可选，默认仅当前会话）。

### 4.6 快捷键（可选的 push-to-talk）
- 目标：可选 push-to-talk 快捷键（**已选定 `SUPER + SHIFT + V`**，`SUPER + X` 因被占用不采用）作为按住说话快捷键，与 UI 并存。
- **注册前必须先检测**：以 `hyprctl binds -j` 查询（并核对 `~/.config/hypr/bindings.lua` / 默认 `clipboard.lua`）。
- 若占用：**不覆盖**；在 UI 与日志中**报告冲突**（当前占用者：「Universal cut」），并请用户选择替代快捷键。
- **已选定**：`SUPER + SHIFT + V`（2026-09-03 实测空闲：live binds 与默认/当前配置均无占用）。
- 注册时将绑定写入 `~/.config/hypr/bindings.lua`（`o.bind("SUPER + SHIFT + V", "Voice control push-to-talk", <cmd>)`），随后 `hyprctl reload` 并以 `hyprctl configerrors` 校验。
- 绑定行为：按住说话 = 开始监听，松开 = 停止并转写（同 4.3）。

---

## 5. STT 需求（Voxtype 适配）

### 5.1 提供者接口（`stt/`）
```text
interface STTProvider {
  status(): ProviderStatus            // available | unavailable + 详情
  transcribe(wavPath): Promise<Transcript>  // 仅返回文本，绝不注入
}
```
- 默认实现 `VoxtypeProvider`，后端命令：`voxtype transcribe <wav>`（stdout 取文）。
- 录制：由本项目自己的录音模块产出 16kHz mono WAV（走 PipeWire/pactl 或 Voxtype 文档化录音能力的**安全子集**；**不得**用会注入的 `voxtype record stop`）。
- 引擎/模型覆盖：按调用传 `--engine sensevoice --model small-fp32 --language zh`（或从配置读取），**不修改** `~/.config/voxtype/config.toml`。
- 转写超时、模型未装、二进制缺失、stdout 为空 → 返回可读错误并标 `status=unavailable`。

### 5.2 防注入规则
1. 语音控制激活期间，**绝不允许** Voxtype 把文本打进聚焦应用（终端/编辑器/浏览器/聊天/密码管理器等）。
2. 仅使用 `voxtype transcribe`（stdout）通道；不使用 `record stop`（注入）。
3. 不 monkey-patch 打包 Voxtype 文件；不按键捕获/剪贴板抓取/焦点欺骗/时序 hack。
4. 若某版本 Voxtype 无法安全返回无注入转录 → **停止并记录该限制**，保留提供者接口，提出最小适配器或上游贡献（见 5.4）。

### 5.3 提供者状态展示（UI）
- 面板内展示 STT 提供者状态：`Voxtype: 可用 / 不可用` + 引擎/模型/后端（读 `voxtype status --format json --extended` 与 `voxtype info models/accel`）。
- 状态含：守护进程是否运行、模型是否就位、GPU/Vulkan 后端是否启用、可用的录音设备。
- 状态变化时 UI 刷新（FileView/状态文件监听，参考 `max.scene` 的经验：**不要**用 Timer+Process 轮询）。

### 5.4 已知限制与上游提案
- **限制**：Voxtype 1.0.0 守护进程 `record stop` 无「纯 stdout/事件回调」转录通道，必然按 `output.mode` 注入（type/clipboard）。已文档化的 `output.mode="file"` 是全局配置，会改变正常听写 → 本项目不采用。
- **上游贡献提案（最小）**：为 `voxtype record stop` 增加 `--output stdout`（或独立输出模式），让外部集成可在不注入的情况下取回转录。若上游接受，本项目可将录音也切回 Voxtype 原生管线。
- **本地备选**：`voxtype transcribe <wav>` 已满足无注入转录；录音自管。

---

## 6. 意图识别需求（`intent/`）

- **确定性解析**：基于短语/关键词/别名表的规则匹配，**无 LLM 依赖**（`source = "rule"`；`future_llm` 仅作为 schema 预留字段）。
- 中英双语规则集（配置化）：示例
  - 「打开 浏览器」/「open browser」→ `open_app`
  - 「聚焦 终端」/「focus terminal」→ `focus_app`
  - 「打开 项目报告.pdf」→ `open_file`
  - 「打开 下载文件夹」→ `open_folder`
  - 「关闭当前窗口」/「close window」→ `close_active_window`
  - 「切到工作区 3」/「workspace 3」→ `switch_workspace`
  - 「把当前窗口移到工作区 2」/「move to workspace 2」→ `move_active_window_to_workspace`
  - 「全屏」/「fullscreen」→ `toggle_fullscreen`
  - 「截图」/「screenshot」→ `take_screenshot`
  - 「锁屏」/「lock screen」→ `lock_screen`
- 解析输出严格结构化（第 11 章）；解析失败 → 明确提示「无法识别」，**不执行**。
- 别名表与短语表放 `config/`（用户可编辑，见第 10 章）。

---

## 7. 解析器需求（`resolver/`）

- **应用查找**：从应用名/别名解析到可执行或 `.desktop`（读取系统与用户 `applications` 目录、`hyprctl clients -j` 的 `class`）；支持「打开」与「聚焦（已在运行则 focus，否则 launch）」两种语义。
- **文件/文件夹查找**：基于家目录与已配置路径（`~/`, `~/下载`, `~/文档`, `~/项目`, XDG 用户目录）做前缀/子串匹配；安全边界：只解析到允许访问的目录（避免任意路径遍历执行）。**blocklist 拦截**：所有隐藏（点开头）条目与敏感路径段/文件名（`.ssh/.gnupg/.config/.cache/.local/.pki/.npm/.cargo`、agent auth、浏览器数据、keyring、密钥/证书文件等，见 `config/blocklist.json`）一律不解析、不搜索（os.walk 剪枝），规则与 AI 来源同等生效。
- **活动窗口与工作区**：读 `hyprctl activewindow -j` / `workspaces -j`，支持「当前窗口/工作区」相对指令与绝对编号。
- 解析结果写入 `Action.target`；解析失败 → `low confidence` + 提示，**不执行**。

---

## 8. 策略需求（`policy/`）

- **允许清单**：`Action.type` 只允许第 11 章枚举的 10 类。类型不在白名单 → 拒绝。
- **路径 blocklist**：`open_file`/`open_folder` 的目标路径命中隐藏条目或敏感路径段/文件名（`config/blocklist.json`，可用 `~/.config/omarchy/voice-control/blocklist.json` 扩展）→ **拒绝**。三层执行：resolver 不返回、policy 拒决、executor 拒绝（`omarchy-voice-control blocklist` 可查看生效规则）。
- **风险分级**：
  - `low`：无破坏性/可逆/常规（open_app、open_file、open_folder、switch_workspace、take_screenshot、focus_app、move_active_window_to_workspace(默认)）。
  - `confirm_required`：有副作用/不可逆/影响全局（close_active_window、toggle_fullscreen、lock_screen；以及任何目标解析置信度低于阈值、或目标越权/特殊路径的动作）。
- **确认要求**：`confirm_required` 必须显式确认；`low` **默认自动执行、无需确认**（`config.confirm_low=false`，可改回保守模式；低风险执行结果仍在弹窗展示）。
- **策略表可配置**（`config/`）：每类动作的风险等级与是否需要确认可被用户覆盖（只允许在安全范围内降级）。

---

## 9. 执行器需求（`executor/`）

| 动作 | 执行途径（Hyprland 0.56.2 Lua 模式 / 系统） |
|---|---|
| `open_app` / `focus_app` | `hyprctl dispatch 'hl.dsp.exec_cmd("<cmd>")'`；已运行则 `hl.dsp.focus({ window = "address:<addr>" })`（详见 ADR-006）|
| `open_file` | XDG 默认应用打开（`xdg-open`，路径经 resolver 白名单校验）|
| `open_folder` | `xdg-open` 或文件管理器 |
| `close_active_window` | `hyprctl dispatch 'hl.dsp.window.close()'`（`confirm_required`）|
| `switch_workspace` | `hyprctl dispatch 'hl.dsp.focus({ workspace = "<id>" })'` |
| `move_active_window_to_workspace` | `hyprctl dispatch 'hl.dsp.window.move({ workspace = "<id>" })'` |
| `toggle_fullscreen` | `hyprctl dispatch 'hl.dsp.window.fullscreen({ mode = "fullscreen" })'`（`confirm_required`）|
| `take_screenshot` | `omarchy capture screenshot` 系列（默认保存，可选复制）|
| `lock_screen` | `omarchy system lock`（`confirm_required`）|

> **ADR-006（2026-09-03）**：Omarchy 4.0 的 Hyprland 为 Lua 配置模式，经典 `hyprctl dispatch exec|workspace|killactive|...` 语法全部失效（参数被当作 `hl.dispatch(...)` 的 Lua 表达式解析报错）。必须改用 Omarchy 注册的 Lua dispatcher（`hl.dsp.exec_cmd / hl.dsp.focus / hl.dsp.window.*`），经 `hyprctl dispatch '<lua>'` 调用。已实测修复并验证 chromium 正常启动。

- 每个执行：带超时、捕获退出码/错误、规范化结果写入 UI 第 5 段；失败原因可读化。
- 执行前后记录审计日志（时间、Action、结果）。

---

## 10. 配置需求（`config/`）

- 位置：`~/.config/omarchy/plugins/<plugin-id>/` 内（或 `~/.config/omarchy/voice-control/`）。
- 内容：
  - STT：引擎/模型/语言、超时、录音设备。
  - 别名表：应用/文件/文件夹/短语的中英别名。
  - 策略覆盖：动作级风险与免确认开关。
  - 快捷键：可选绑定与冲突处理结果（`SUPER+X` 已确认占用；用户已选定 `SUPER+SHIFT+V`，实测空闲）。
  - 外观：图标字形（Nerd Font PUA，用 `\uXXXX` 转义）、弹窗主题。
- 配置 JSON 校验后加载；非法配置回退默认并提示。
- 用户配置不落入 `/usr/share/omarchy/`。

---

## 11. Action 严格类型 Schema

```json
{
  "type": "open_app | focus_app | open_file | open_folder | close_active_window | switch_workspace | move_active_window_to_workspace | toggle_fullscreen | take_screenshot | lock_screen",
  "target": {},
  "confidence": 0.0,
  "risk": "low | confirm_required",
  "source": "rule | future_llm"
}
```

- `type`：仅允许枚举值。
- `target`：结构随 type（例：`open_app → {app, alias, desktop?}`；`switch_workspace → {id}`；`open_file → {path, matched}`…）。
- `confidence`：0..1；低于 `policy.min_confidence`（默认 0.6）→ 拒绝执行并请求澄清。
- `risk`：由 policy 判定。
- `source`：当前恒为 `"rule"`；`"future_llm"` 仅为未来扩展预留，且即使启用也必须同样经 policy/确认关卡。

---

## 12. UI 组件需求（`ui/`）

| 组件 | 说明 |
|---|---|
| 顶栏图标 | bar-widget，主题感知，状态可视化 |
| 助手弹窗 | Popover，鼠标/触控/键盘，含 4.4 五段展示 |
| 麦克风按钮 | 大按钮，开始/停止双态 |
| 确认界面 | `confirm_required` 时的确认/取消按钮 |
| 通知 | 执行成功/失败（复用 `omarchy.notifications` / Hyprland `hyprctl notify`） |
| 提供者状态行 | 第 5.3 节 |

- 文案：默认英文（用户 2026-09-03 确认）。
- QML 实现遵循 Omarchy 原生模式（参考 `max.scene`/`panels/*`）；图标字形用 `\uXXXX`；避免 `Timer`+`Process` 轮询（用 FileView/状态文件监听）。

---

## 13. 非功能需求

- **本地优先**：STT、意图解析、执行全部本地；离线可用。
- **延迟**：转写（SenseVoice small-fp32 zh 实测 ~0.2s/5s 音频）与执行合计交互 < ~2s（录音时长除外）。
- **安全**：见第 3 章；所有外部调用子进程化、超时、无 shell 拼接注入（参数数组传参）。
- **可审计**：动作日志；不记录敏感音频原文之外的明文凭据。
- **中英双语**：语音识别支持中英；**界面文案默认英文**（用户 2026-09-03 确认，中文仅作后续 i18n 预留）。
- **可维护**：`stt/ intent/ resolver/ policy/ executor/ ui/ config/ tests/` 分层，职责单一。

---

## 14. 测试计划（`tests/`）

| 层 | 用例 |
|---|---|
| intent | 中英文短语→Action 映射、别名、否定/歧义→拒绝 |
| resolver | 应用/文件/文件夹解析、活动窗口与工作区解析、路径越权拦截 |
| policy | 白名单外拒绝、风险分级、确认要求、confidence 阈值 |
| executor | 各 action 的 mock/真实 Hyprland 调用、超时与失败注入 |
| stt | Voxtype 可用性探测、`transcribe` stdout 解析、无注入断言（焦点应用无打字） |
| ui | 打开/关闭/外点/Escape、录音中断即停、键盘可达性 |
| 集成 | 端到端：说→文本→动作→确认→执行→结果；`SUPER+X` 冲突检测路径 |

- 安全回归：每次改动后运行「无注入」与「确认前不执行」断言。

---

## 15. 目录结构与交付物

```
omarchy-voice-control/
├── docs/requirements.md      # 本文档
├── stt/                      # STT 提供者（接口 + VoxtypeProvider + 录音）
├── intent/                   # 规则解析、Action 构建
├── resolver/                 # 应用/文件/窗口/工作区解析
├── policy/                   # 白名单、风险、确认
├── executor/                 # Hyprland / XDG / 系统动作执行
├── ui/                       # QML：顶栏图标、弹窗、确认、状态
├── config/                   # 用户设置与别名
├── tests/                    # 分层与集成测试
├── manifest.json             # Omarchy 插件清单（bar-widget 等）
└── README.md
```

- 交付物：一个可 `omarchy plugin add` 的本地插件（或复制到 `~/.config/omarchy/plugins/<id>/`），安装脚本负责：复制文件、注册到 `shell.json`、检测并处理快捷键冲突、`omarchy-shell shell rescanPlugins` 热载。

---

## 16. 里程碑

| 阶段 | 范围 | 验收 |
|---|---|---|
| M0 需求 | 本文档 + 环境审计 | 已审计版本/冲突/STT 通道 ✓ |
| M1 骨架 | 目录分层、manifest、bar 图标、弹窗显隐 | 点击图标开合弹窗、外点/Escape 关闭 |
| M2 STT | 录音→`voxtype transcribe`→stdout；提供者状态 | 说中文/英文得到文本，焦点应用无打字 |
| M3 意图+解析 | intent/resolver 全 10 类动作 | 中英指令→结构化 Action |
| M4 策略+执行 | policy/executor + 确认关卡 | 确认前不执行、白名单外拒绝、结果展示 |
| M5 快捷键 | 注册 push-to-talk `SUPER + SHIFT + V`（已选定、实测空闲） | 绑定生效、`hyprctl configerrors` 无错、与 UI 录音流程一致 |
| M6 打磨 | 主题、通知、审计日志、测试完善 | 全量回归通过 |

---

## 17. 开放问题 / 待决策

1. **免确认策略**：已定 `low` 动作默认免确认、直接执行（`confirm_low=false`，2026-09-03）；`confirm_required`（关窗/全屏/锁屏）始终确认。
2. **录音设备**：使用系统默认输入设备（`pactl list sources short` 验证），还是允许配置。
3. **截图落点**：默认保存到 `~/图片/` 还是复制到剪贴板。
4. **Voxtype 上游**：是否将「`record stop --output stdout`」提案提交给 Voxtype。

已解决（2026-09-03）：① push-to-talk 快捷键选定 `SUPER + SHIFT + V`（实测空闲）；② UI 文案默认英文。

---

## 18. 决策记录（ADR，追加式）

- **ADR-001**（2026-09-03）：STT 通道 = 自管录音 + `voxtype transcribe <wav>`（stdout，无注入）。**理由**：守护进程 `record stop` 必然注入；实测 `transcribe` 无注入可复用本地模型。**备选否决**：`output.mode="file"`（全局配置破坏正常听写）。
- **ADR-002**（2026-09-03）：快捷键 `SUPER + X` 已占用（Universal cut）→ 默认不注册，报告冲突并请用户另选（见开放问题 1）。
- **ADR-003**（2026-09-03）：Action 白名单固定 10 类；`source` 预留 `future_llm` 但同样受 policy/确认约束。
- **ADR-004**（2026-09-03）：push-to-talk 快捷键选定 `SUPER + SHIFT + V`（`SUPER+X` 被 Universal cut 占用，未覆盖；`hyprctl binds -j` + 默认/当前配置实测空闲）。UI 文案默认英文（中文仅作后续 i18n 预留）。
- **ADR-007（2026-09-03）**：AI 意图层后端选定 **pi RPC mode**（`pi --mode rpc --no-session`，JSON-RPC over stdio，官方文档含 Python 客户端示例），本机实测可用（默认模型 deepseek-v4-flash，1M 上下文）。已建 `config/catalog.py` 生成机器命令目录（`~/.config/omarchy/voice-control/catalog.json`：356 条 omarchy 命令 + 118 个应用 + 10 类动作 + hl.dsp.* dispatcher）作为 AI grounding。**安全不变式**：AI 只输出严格结构化 Action JSON，不产生可执行 shell；仍走 policy + 人工确认关卡；规则匹配（毫秒级）优先，AI 仅作规则未命中时的增强理解层。
- **ADR-008（2026-09-03）**：AI 意图层已实现并实测。`intent/ai.py`：spawn `pi --mode rpc --no-session` → `set_thinking_level off`（非思考提速，实测 ~5s）→ `prompt`（含 catalog 摘要 + 安全约束）→ 等 `agent_settled` → `get_last_assistant_text` → JSON 抽取/校验（白名单 + confidence 钳位，拒绝 shell）。CLI 流程：规则先匹配 → 命中且可解析即走（source=rule，秒级）；规则未命中**或规则命中但解析失败**（如转写乱码）→ AI（source=future_llm）→ 同一 resolver/policy/确认关卡。UI 动作行显示 `Action · AI` 标签。ai 配置段 `{enabled, backend=pi-rpc, thinking=off, model=null, timeout_secs=60}`；pi 缺失/超时时优雅降级为“Could not understand”。另修：`xdg-open` 在本机挂 Tracker3 导致超时 → open_file/open_folder 改用 `gio open`（glib，0.01s）。
- **ADR-009（2026-09-03）**：低风险动作**默认免确认、直接执行**（`confirm_low=false`）；`confirm_required`（close_active_window / toggle_fullscreen / lock_screen）仍必须显式确认。规则/AI 命中后，低风险自动走 executing→result（结果在弹窗展示，成功消息用可读描述如 “Switch workspace: workspace 2”）；高风险停在 awaiting_confirm 等用户确认。安全：风险分级与白名单不变。
- **ADR-010（2026-09-03）**：新增**路径 blocklist**（`config/blocklist.json`）：隐藏（点开头）条目 + 敏感路径段/文件名（`.ssh/.gnupg/.config/.cache/.local/.pki/.npm/.cargo`、agent auth、浏览器数据、keyring、密钥/证书文件等）禁止被 `open_file`/`open_folder` 访问，规则与 AI 来源同等生效。三层执行：resolver 不返回/搜索剪枝 → policy 拒决 → executor 拒绝。用户可用 `~/.config/omarchy/voice-control/blocklist.json` 扩展；`omarchy-voice-control blocklist` 查看。60 单测全绿。
