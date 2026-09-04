# 长对话 + 长短期记忆 设计方案 (draft for review)

> 目标：把当前"单轮 chat_reply / chat_defer"升级为可连续追问的**长对话**，
> 并加入**短期记忆**（会话上下文）与**长期记忆**（跨会话的持久事实/偏好）。
> 全部本地存储，零上传，符合插件隐私定位。
> 状态：**待审阅**（未实现）。

---

## 1. 现状

- 聊天回退是**单轮**：`chat_analyze(transcript) -> {kind, reply}`，
  `chat_reply` 显示后即被 `cancel-action` 清空；追问"那它呢？"没有任何上下文。
- 已有 `config/memory.py`（operation memory）：**命令**草稿缓存（transcript→action），
  不是对话记忆，与本方案互不干扰。
- 已有 `config/character.py`：omaok 人设 + 离线身份问答（本方案保留）。
- 状态机通过 `state.json`（FileView 监控）驱动 UI；CLI 是唯一写入方。

## 2. 调研结论（agent 记忆主流做法）

- 记忆分三层：**短期**（会话内上下文）、**长期语义**（跨会话事实/知识，向量检索）、
  **情景**（episodic，整个会话轨迹/摘要）。对桌面助手，前两层 + 会话摘要已够用。
- 短期记忆用 **滑动窗口 + 滚动摘要**：prompt 只放最近 N 轮 + 早期轮次的摘要，
  控制 token 与成本。
- 长期记忆用 mem0 式闭环：**extract（抽取）→ consolidate（去重/合并/冲突解决）→
  retrieve（检索注入）**；检索 = 向量 top-K + 时间衰减 + 重要性 + 多样性。
- 本地化选项：**SQLite + FTS5（trigram tokenizer 适合中文）+ sqlite-vec + ONNX
  本地 embedding（bge-small-zh / bge-m3）**，零 API、零外部服务；纯关键词 + 时间 +
  重要性在 v1 也可先不引入向量。
- 核心理念：*memory is about relevance, not capacity*。

## 3. 总体架构（分层决策漏斗，优先级 高→低）

```
        speech -> STT -> transcript
             │
  ┌──────────▼──────────┐
  │ L1 规则 (rules)     │ 确定性正则，~ms，零网络   → 命令(confirm->execute)
  │ L2 常用指令(记忆)   │ operation-memory 重放，~ms，跳过 AI → 命令
  │ L3 指令 (AI intent) │ 冷门/新颖措辞，LLM 解析    → 命令
  │ L4 对话 (chat)      │ persona离线问答优先；否则带长短记忆的模型回复 → chat_reply
  │ L5 AI 工具 (defer)  │ 复杂/开放式话题 → 打开 ChatGPT 等（最后手段）→ chat_defer
  └────────────────────┘
```

- **管理电脑的操作指令走快速通道**：L1 规则 + L2 记忆都在本地毫秒级，
  L3 AI 意图（热 daemon）是唯一相对慢的命令层；命令永远优先于聊天。
- L4 与 L5 由同一个 chat 模型调用判定（kind=answer | defer | none），
  defer 是最低优先级的兜底结果。
- 命令解析（L1-L3）与对话记忆（L4）**严格隔离**：记忆只是聊天上下文，
  永不变成可执行命令。

## 4. 短期记忆（长对话）

- 新文件 `intent/chat.py` + 数据 `~/.config/omarchy/voice-control/chat.json`：
  ```json
  {
    "session_id": "…", "active": true,
    "turns": [ {"role":"user","text":"…","ts":…}, {"role":"assistant","text":"…","ts":…} ],
    "summary": "…早期轮次的滚动摘要…"
  }
  ```
- **追加**：每轮 reply 后写入 `turns`。
- **窗口**：`turns` 超过 N（默认 8 轮）或 token 预算时，把最旧几轮交给模型生成/更新
  `summary` 后移除（滑动窗口 + 滚动摘要）。
- **会话生命周期**：连续聊天为同一 session；出现长 gap（默认 >10min）或用户执行命令
  → 收尾会话（consolidate 到长期记忆）→ 开新 session。
- **退出长对话**：规则命中"结束对话/退出聊天/不聊了" → 收尾并提示。
- **UI**：泡泡仍只显示最新 reply；`state.json` 增加 `chat_active` 标记（可选：显示
  "继续对话"或会话倒计时）。历史不一定要在 UI 展示（v1 可只做后端）。

## 5. 长期记忆

- 数据 `~/.config/omarchy/voice-control/memory/facts.json`：
  ```json
  [ {"id":"f1","text":"用户是产品经理","category":"preference|fact|identity|todo|…",
     "ts":…, "source_turn":"…", "hits":3, "last_used":…}, … ]
  ```
- **extract/consolidate**：会话收尾时，一次模型调用把本会话蒸馏成"值得长期记住的条目"
  （对现有条目去重/合并/冲突解决，mem0 风格），写入 facts.json（原子写 + flock）。
- **retrieve**：新会话/每轮开始时，基于当前 transcript 用 **关键词 + 最近使用(recency)
  + 命中次数(importance)** 打分取 top-K 注入 prompt。v1 不引入向量。
- **显式指令**（可选加分项）："记住我住在上海" / "忘掉关于 X 的事" / "我上次说到哪了"。

## 6. v2 可选增强（本次不实现）

- **语义召回**：SQLite + FTS5（trigram tokenizer，中文友好）+ sqlite-vec +
  本地 ONNX embedding（bge-small-zh / bge-m3）做混合检索，提升"换说法也能想起"。
- 参考：SinoMem（本地中文 agent 记忆，SQLite+FTS5+ONNX，零 API）、sqlite-vec 教程。

## 7. 安全边界（不可妥协）

- 命令解析（rules/AI-intent/confirm）**绝不**注入对话记忆——记忆只是聊天上下文，
  永远不会变成可执行命令。
- 用户说出命令时挂起/结束会话；记忆内容与操作执行完全解耦。

## 8. 改动清单

| 文件 | 改动 |
|---|---|
| `intent/chat.py` (新) | 会话层：turn 管理、窗口+摘要、consolidate、retrieve |
| `memory/chatmem.py` (新) | 长期记忆存储：facts.json 读写、打分检索、原子写 |
| `cli.py` | step 4 改为调用 chat 会话层；收尾/退出指令 |
| `intent/ai.py` | chat 请求组装改为复用 chat.py（persona blurb 保留） |
| `config/character.py` | 不变 |
| `config/memory.py` | 不变（命令记忆与对话记忆分离） |
| `state.py` / QML | 可选：`chat_active` 标记（泡泡提示继续对话） |
| `docs/` | 本设计文档 |

## 9. 测试

- 单元：窗口轮转 / summary 触发、consolidate 去重、retrieve 打分排序、命令↔对话边界、
  数据原子写；现有 125 测试保持通过。

## 10. 风险与权衡

- 模型调用次数略增（summary 再生 + consolidate）→ 用 pi-rpc 热 daemon 压延迟，
  consolidate 放 idle 异步做。
- 上下文只注入"最近 N 轮 + 检索事实"，控制 token。
- 纯中文关键词检索精度有限 → v2 embedding 兜底。

## 待用户确认的点

1. 会话生命周期：连续聊天多久算断（10min？）、命令是否打断会话？
2. 长期记忆类目（preference/fact/identity/todo…）是否够用？
3. 是否需要"记住/忘掉/我上次说到哪"这类显式记忆指令？
4. 是否要 UI 显示会话历史/记忆管理，还是先只做后端？
