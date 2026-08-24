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
  events.json                可选 — NPC 离线演化事件池
  form.json                  可选 — 声明式角色创建表单（无则前端回退 legacy 表单）
  world_facts.json           可选 — IP 世界观权威设定（控制 LLM 预训练记忆使用）
  modeler/role.txt           可选 — 角色建模师 prompt 模板
  modeler/age_rules.txt      可选 — 建模年龄规则
  modeler/schema.json        可选 — NPC 建模字段树（包级替换修仙默认 90+ 字段；无则降级 xianxia 模板）
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
    "status_label": "角色"
  },
  "extra_event_types": [],
  "time_per_intent": {
    "travel": 2
  }
}
```

### assembly 字段

- `"shell+kernel"`（默认）：`system_prompt.txt` 作为世界观外壳，引擎自动在其后拼接通用叙事内核（叙事原则/输出 JSON 格式/禁用反问句）。四个内置包均用此模式。
- `"full"`：`system_prompt.txt` 就是完整 System Prompt，引擎原样使用（作者完全掌控全文）。保留兼容旧包，无包时兜底用引擎内建 legacy 修仙提示词。

### player_defaults

| 键 | 用途 |
|----|------|
| `name` | 玩家默认名（未创建角色时） |
| `cultivation` | 玩家默认能力/职业标签 |
| `status_label` | `/status` 命令与前端状态栏对玩家的称呼（如"修士"/"市民"） |

### extra_event_types

本世界观允许的额外 `state_changes` 事件类型（引擎核心类型始终可用：location_change、status_change、item_added/removed、relationship_change、quest_*、npc_status、player_name_change、npc_important、npc_nearby、npc_enters/leaves/action、dialogue、travel、combat、trade、environment、event、time_skip、death、marriage）。xianxia 用 `["cultivation_change", "breakthrough"]`。

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
    {"label": "性格", "key": "personality", "source": "attrs", "default": "未知"}
  ]
}
```

- `source`: `player`（Player 列）/ `attrs`（attributes JSON）/ `items`（背包）/ `exts`（_extensions）。
- `show_if`: `truthy`（有值才显示）/ `nonzero`（非零才显示）。
- `unit_attr`: 追加计量单位后缀。

## form.json（声明式角色创建表单，可选）

无此文件时前端回退 legacy 硬编码表单。有则前端按 spec 动态渲染、后端 `apply_character_from_form` 通用写入：

```json
{
  "title": "创建你的忍者",
  "fields": [
    {"key": "name", "label": "姓名", "kind": "text", "random_button": true, "store": "player.name"},
    {"key": "age", "label": "年龄", "kind": "number", "default": 12, "min": 10, "max": 60, "store": "attrs.age"},
    {"key": "cultivation", "label": "忍者等级", "kind": "select", "options_from": "cultivations",
     "hint_template": "{desc}", "allow_custom": true, "custom_label": "自定义等级", "store": "player.cultivation"},
    {"key": "identity", "label": "身份", "kind": "select", "options_from": "identities",
     "hint_template": "衣物：{clothing}", "allow_custom": true,
     "store": "attrs.identity", "derive": ["identity_desc", "clothing"]},
    {"key": "golden_finger", "label": "特殊能力", "kind": "card_grid", "options_from": "golden_fingers",
     "allow_custom": true, "visible_if": "has_golden_fingers",
     "option_map": {"id": "golden_finger_id", "name": "golden_finger_name", "tagline": "golden_finger_tagline", "desc": "golden_finger_desc"},
     "store": "attrs.golden_finger_id"}
  ]
}
```

字段属性：
- `kind`: `text`（+`random_button` 随机名）/ `number`（min/max）/ `select`（下拉）/ `card_grid`（卡片网格，如金手指）
- `options_from`: 从 player_templates 取选项（cultivations/identities/backgrounds/personalities/golden_fingers）；`sects` 特殊（从世界模板）
- `hint_template`: 解释小字，`{字段名}` 占位符从选中选项填充
- `allow_custom` + `custom_label`: 选中 `__custom__` 时弹出文本框
- `store`: 写入位置（`player.name`/`player.cultivation`/`player.location` 列，或 `attrs.xxx`）
- `derive`: 选中选项后复制到 attributes 的字段列表（如 identity → clothing）
- `option_map`: 卡片选项字段重命名映射（金手指 id→golden_finger_id 等）
- `visible_if`: `has_sects` / `has_golden_fingers` 条件显隐

## world_facts.json（IP 世界观权威设定，可选）

基于既有作品时使用，控制 LLM 对预训练记忆的使用。每轮注入【本世界权威设定】块，声明冲突时以此为准：

```json
{
  "knowledge_mode": "hybrid",
  "must_follow": ["故事基于作品《火影忍者》展开", "时间线在第四次忍界大战之前"],
  "forbidden": ["不得出现佩恩/晓组织入侵木叶"],
  "characters": [
    {"name": "漩涡鸣人", "desc": "九尾人柱力，木叶下忍"},
    {"name": "宇智波佐助", "desc": "写轮眼拥有者"}
  ]
}
```

`knowledge_mode` 三档：`pack_only`（仅包内设定）/ `hybrid`（包内为最高权威 + 常识补全，推荐）/ `full_ip`（基于原作自由发挥）。完整指南见 [IP_WORLDVIEW.md](IP_WORLDVIEW.md)。

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

## 包校验规则（`POST /worldviews/{id}/validate`）

`validate_pack` 除检查文件齐全/JSON 合法外，还执行以下结构性规则。作者写包时提前知晓可避免 warnings：

### NPC 姓名池（npc_templates.json）
- `surnames` / `given_names_male` / `given_names_female` 各自**无重复**
- 男名池与女名池**不重叠**
- 名池**不含姓氏**（避免"宇智波"同时当姓和名 → 生成完整姓名撞车）
- 姓氏超 4 字警示（通常是非姓氏混入，但真实西方复姓如 Fitzgerald 可忽略）

### panel 字段来源对齐（panel.json vs player_templates.json）
- panel 引用 `attrs.*` 的字段需在 player_templates（identities/golden_fingers）中找到，或属于已知通用 attrs（age/gender/special_constitution/background_summary 等）
- `golden_finger_*` 字段由 card_grid option_map 生成，视为合法
- 此规则防止"panel 显示某字段但角色创建从未写入该值"的断链

### 时间线完整性（world_facts.json 的 timelines）
- 每个 timeline 节点必须有 `id`（全包唯一）/ `label` / `description` / `must_follow[]` / `forbidden[]` / `characters[]`
- id 重复、缺字段会告警

> 结构性规则是**通用**的，不依赖具体世界观知识，作用于所有包。语义性正确性（如某角色该在何时死亡）仍需作者按原作把关。
