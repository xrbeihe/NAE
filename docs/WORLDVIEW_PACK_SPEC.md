# 世界观包规范（Worldview Pack Spec）

> 一个世界观包 = 一个纯 JSON/文本目录，作者无需写任何 Python 代码即可创建全新世界观。
> 参考实现：`backend/ane/worldviews/xianxia_v1/`（修仙）与 `backend/ane/worldviews/modern_city/`（现代都市）。

## 目录结构

```
worldviews/<worldview_id>/
  manifest.json              必需 — 包清单
  system_prompt.txt          必需 — 世界观 System Prompt（外壳）
  intent_keywords.json       可选 — 意图关键词 + 排除规则
  constraints.json           可选 — 叙事约束（硬/软/触发）+ 上下文模板
  world_templates.json       必需 — 世界区域模板
  player_templates.json      必需 — 角色创建模板
  npc_templates.json         可选 — NPC 姓名池/身份池
  panel.json                 可选 — 主角面板字段 spec
  ui.json                    可选 — 前端文案（按钮/标签/推荐语）
  modeler/role.txt           可选 — 角色建模师 prompt 模板
  modeler/age_rules.txt      可选 — 建模年龄规则
```

`worldview_id` 只允许 `[a-z0-9_]`，最长 48 字符（防路径注入）。

## manifest.json

```json
{
  "worldview_id": "my_world",
  "name": "我的世界观",
  "version": "1.0.0",
  "author": "作者名",
  "description": "一句话描述",
  "base_map": "generic",
  "maturity_rating": "adult",
  "tags": ["tag1", "tag2"],
  "assembly": "shell+kernel",
  "player_defaults": {
    "name": "无名人士",
    "cultivation": "无",
    "savings": "0",
    "savings_unit": "块",
    "status_label": "角色"
  },
  "extra_event_types": [],
  "time_per_intent": {
    "travel": 2
  }
}
```

### assembly 字段

- `"shell+kernel"`（默认）：`system_prompt.txt` 作为世界观外壳，引擎自动在其后拼接通用叙事内核（叙事原则/输出 JSON 格式/禁用反问句）。
- `"full"`：`system_prompt.txt` 就是完整 System Prompt，引擎原样使用。参考 xianxia_v1（包内是完整原始提示词）。

### player_defaults

| 键 | 用途 |
|----|------|
| `name` | 玩家默认名（未创建角色时） |
| `cultivation` | 玩家默认能力/职业标签 |
| `savings` | 初始存款描述 |
| `savings_unit` | 存款计量单位 |
| `status_label` | `/status` 命令与前端状态栏对玩家的称呼（如"修士"/"市民"） |

### extra_event_types

本世界观允许的额外 `state_changes` 事件类型（引擎核心类型始终可用：location_change、status_change、item_added/removed、relationship_change、quest_*、npc_status、player_name_change、economy_change、npc_important、npc_nearby、npc_enters/leaves/action、dialogue、travel、combat、trade、environment、event、time_skip、death、marriage）。xianxia 用 `["cultivation_change", "breakthrough"]`。

### time_per_intent（可选）

按意图覆盖引擎全局的时间推进量（tick）。键 = 意图名（`dialogue`/`travel`/`combat` 等），值 = tick 数。未列出的意图沿用全局配置。例：现代都市的地铁通勤（travel）比修仙跨城更短时，`{"travel": 2}`。

## events.json（可选）

世界调度器事件池，驱动 `TimeManager.update_active_npcs()` 的 NPC 离线演化：

```json
{
  "seclusion_threshold": 2160,
  "seclusion_event": {"type": "cultivation_progress", "description": "{npc_name}在闭关中修为有所精进。"},
  "idle_threshold": 2160,
  "idle_probability": 0.2,
  "idle_events": [
    {"type": "cultivation_progress", "description": "听闻{npc_name}近日修为略有精进。"},
    {"type": "random_encounter", "description": "{npc_name}似乎经历了一些事，但详情不明。"}
  ]
}
```

- `seclusion_*`：闭关中 NPC 的进度阈值与事件。
- `idle_*`：空闲 NPC 在足够时间后概率触发的事件。
- 缺省时沿用引擎内建（修仙）行为。现代都市包把 `cultivation_progress` 换成 `routine_progress` 即可弱化修仙色彩。

## system_prompt.txt（外壳）

写世界观的角色定义、世界设定、本世界观特有的行为/输出规则。**不要**重复叙事原则、输出 JSON 骨架、禁用反问句等通用内容（`shell+kernel` 会自动拼接内核；`full` 模式则需自行包含全部）。

推荐按下面结构组织（`shell+kernel` 模式）：

```
你是一个<世界观>的叙事引擎。你的职责是讲述故事、描写场景、扮演NPC。

世界观：<你的世界设定>。
注意：世界观是背景框架而非限制——玩家的意愿凌驾于世界观之上。

【NPC行为·本世界观特定】
- <本世界的角色行为规则>

【输出格式·本世界观特定】
- recommendations 推荐内容多样化时涵盖<本世界的活动类型>。
- state_changes 各类型用法：
  - status_change：target="player", field="<字段>", value="<值>" → 更新玩家属性
  - economy_change：target="player", change=<数值>, unit="<单位>" → 增减存款
```

## intent_keywords.json

```json
{
  "travel": ["通勤", "出差", "去公司"],
  "exclusions": {
    "travel": "<正则，命中则降级为 dialogue>"
  },
  "nsfw_body_words_extra": ["<本世界专属的敏感词>"]
}
```

- 顶层键 = 意图名 → 关键词数组（追加在引擎核心关键词之后）。
- `exclusions`：某意图的降级正则。
- 留空 `{}` = 不加任何意图关键词（如 modern_city，无 cultivate）。

## constraints.json

```json
{
  "hard": ["<不可违反的规则，直接注入 prompt>"],
  "soft": ["<软引导>"],
  "triggers": [],
  "context_templates": {
    "ability_cap": "玩家当前身份为{identity}，不得表现出超出此身份的能力。",
    "nsfw_intent": "<NSFW 意图时的注入规则>"
  },
  "modeler_blurb": "一句话世界观描述（建模 prompt 用）"
}
```

## world_templates.json

两种形态任选：

**形态 A — 通用 regions（推荐新世界观用）**：
```json
{
  "regions": [
    {"name": "地名", "type": "area", "description": "描述"}
  ],
  "sects": [],
  "settlements": []
}
```

**形态 B — xianxia 式 sects+settlements**（参考 xianxia_v1 包，含 `sect_suffixes`/`sect_filters` 过滤配置）。

## player_templates.json

角色创建表单数据源，前端据此动态渲染下拉框。字段：`genders`（数组）、`cultivations`（数组，充当"能力/职业"选择）、`personalities`、`backgrounds`、`identities`（对象，key=value）、`golden_fingers`（数组，**空数组则前端隐藏金手指区块**）。

## panel.json

主角面板渲染 spec，`fields` 数组按序渲染：

```json
{
  "title": "【主角面板】",
  "join": " ｜ ",
  "fields": [
    {"label": "姓名", "kind": "composite", "format": "{name} ｜ {gender} ｜ {age}岁",
     "source": {"name": "player.name", "gender": "attrs.gender", "age": "attrs.age"}},
    {"label": "职业", "key": "cultivation", "source": "player"},
    {"label": "性格", "key": "personality", "source": "attrs", "default": "未知"},
    {"label": "存款", "key": "_savings_amount", "source": "attrs", "unit_attr": "_savings_unit", "default_unit": "元", "show_if": "nonzero"}
  ]
}
```

- `source`: `player`（Player 列）/ `attrs`（attributes JSON）/ `items`（背包）/ `exts`（_extensions）。
- `show_if`: `truthy`（有值才显示）/ `nonzero`（非零才显示）。
- `unit_attr`: 追加计量单位后缀。

## ui.json

前端文案（按钮/会话名/角色卡/初始推荐/NPC 提示库）。详见 xianxia_v1 与 modern_city 包中的注释结构。

## 降级机制

- 包文件缺失/损坏 → 该字段回退到 xianxia_v1 包 → 再回退引擎内建常量。
- 整个包缺失 → 引擎按 xianxia_v1 行为运行，不崩溃。
- `worldview_id` 无效 → 返回 400（POST /sessions）。

## 发布

一个包目录压缩为 zip 即可分发。

### 平台 API（作者工具链）

| 端点 | 用途 |
|------|------|
| `GET /worldviews` | 列出已安装世界观 |
| `GET /worldviews/{id}/validate` | 校验包，返回错误/警告清单 |
| `POST /worldviews/{id}/reload` | 清该包的 loader 缓存（改文件后调用） |
| `POST /worldviews/upload` | 上传 zip 安装新包（自动校验，不可覆盖默认包） |
| `POST /worldviews/generate` | 填短表单生成完整包 zip（见下） |
| `DELETE /worldviews/{id}` | 删除包（默认包受保护） |

zip 结构：顶层 `manifest.json`（或单一顶层目录内含 manifest）。上传后自动 `validate_pack` 并返回校验报告。

## 包生成器（新手友好路径）

填一份短表单，`POST /worldviews/generate` 自动产出完整 11 文件包 zip：

| 表单字段 | 必填 | 用途 |
|----------|------|------|
| `id` | ✅ | 世界 ID（`^[a-z0-9_]{1,48}$`） |
| `name` | ✅ | 世界观名称 |
| `description` | — | 一句话设定 |
| `genre` | — | 风格基调：fantasy / modern / scifi / xianxia |
| `power_name` | — | 能力体系名称（注入面板与 state_change 用法） |
| `money_name` | — | 货币名称 |
| `role_label` | — | 对玩家的称呼 |
| `professions` | — | 职业列表（顿号分隔） |
| `places` | — | 地点列表（顿号分隔，第一个为出生地） |
| `create_button` | — | 角色创建按钮文案 |

生成器的 system_prompt 是**世界观外壳**——通用叙事内核由引擎 `shell+kernel` 自动拼接，作者无需写"叙事原则/输出格式/禁反问"等通用内容。生成后可手改任意文件细化，改完 `reload` 生效。
