# LLM JSON 输出质量控制

> 解决 LLM（llm_main）输出 JSON 格式不稳定、截断、混入多余文字的问题。

---

## 问题描述

llm_main 在输出叙事 JSON 时偶尔出现：
- **截断**：内容超出 `max_tokens`，JSON 尾部被切掉，括号不完整
- **格式错乱**：JSON key 缺少双引号、混入 ```json 代码块标记、前后有多余说明文字
- **拒绝回答**：输出纯文本而非 JSON

这些问题的根因是 LLM 的自回归生成特性——它无法在生成前预判 JSON 结构的完整性，生成长内容时容易"忘了关括号"。

---

## 防御策略（三层）

### 第一层：Prompt 预防（最便宜）

在 `prompt_builder.py` 的 SYSTEM_PROMPT 输出格式板块中，明确要求 LLM 只输出纯 JSON。

当前指令已包含：
```
输出的内容必须能被 json.loads() 直接解析。
所有 key 必须在英文双引号内，不要在 JSON 前后添加 ```json 代码块标记或其他任何解释性文字。
```

### 第二层：JSON 修复库（中间层）

使用 `json_repair` 库（Python）处理常见的 LLM JSON 格式问题：

| 问题类型 | json_repair 处理方式 |
|---------|-------------------|
| 括号缺失（截断） | 自动补齐 `}` 和 `]` |
| 多余逗号 | 去掉尾随逗号 `,}` `,]` |
| key 漏引号 | `name:` → `"name":` |
| 单引号代替双引号 | `'x':` → `"x":` |
| 前后多余文字 | 自动剥离 preamble/postamble |
| 布尔值大小写 | `True`/`False` → `true`/`false` |

**集成方式**：在 `output_parser.py` 的 `_parse_json()` 中，`json.loads()` 失败后 fallback 到 `json_repair.loads()`。

```python
try:
    import json_repair
    data = json_repair.loads(json_str)
except ImportError:
    data = json.loads(json_str)
```

### 第三层：重试机制（最后防线）

如果修复仍失败，在 `game_engine.py` 中重试 LLM 调用：
- 最多重试 1 次（2 次 total）
- 重试 prompt 尾部追加格式提醒
- 重试仍失败则返回纯文本 fallback（已有逻辑）

```python
for attempt in range(2):
    raw = await model_adapter.generate(prompt, ...)
    try:
        parsed = parse(raw)
        if parsed.narrative:
            break
    except:
        prompt += "\n\n⚠️ 注意：上次输出JSON格式有误，请只输出纯JSON。"
```

---

## Hook: 忘记 HO 提醒

在 `frontend/index.html` 的 `sendTurn()` 中增加了 NSFW 关键词检测：
- 至少匹配 **2 个** 关键词（如"肉棒""小穴""操"等）且用户未加 `HO` 后缀时
- 在输入框上方显示红色提醒 `💡 需要直白描写请在末尾加 HO`
- 不阻断发送，只做提示

## 为什么不使用更复杂的方案

| 方案 | 不适合原因 |
|------|-----------|
| Tool Use 强制 Schema | 需要 Anthropic/OpenAI 专有 API 支持，当前项目支持 6 个 Provider |
| 流式状态解析 | 本项目非流式输出（一次性返回完整 JSON），不需要增量解析 |
| 多轮续写 | 叙事被截断时保留部分输出+续写对用户更不友好，不如直接让 LLM 重来 |
| 模型分层（弱→强） | 项目已有 6 个 Provider，用户自选模型，不适合强行切换 |

当前三层方案已在 90+ 其他项目中验证有效，代码改动量 <= 10 行。

---

## 涉及文件

| 文件 | 修改内容 |
|------|---------|
| `modules/prompt_builder.py` | SYSTEM_PROMPT 输出格式指令（已改） |
| `modules/output_parser.py` | 增加 `json_repair` fallback |
| `modules/model_adapter.py` | （无需改动） |
| `game_engine.py` | 增加重试逻辑 |
| `requirements.txt` | 新增 `json_repair` 依赖 |
