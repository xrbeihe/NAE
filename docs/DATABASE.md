# 数据库表结构

> 文档位置：`backend/ane/database/models.py`

## 表总览

11 张业务表（v1.2 新增 2 张：开源共享库 + 评分）：

| 表名 | 模型名 | 说明 |
|------|--------|------|
| `users` | User | 注册用户 |
| `sessions` | WorldSession | 游戏世界（一个用户可创建多个） |
| `players` | Player | 玩家角色（每个世界一个） |
| `npcs` | NPC | 世界中的 NPC |
| `npc_relationships` | NPC_Relationship | 关系网边表 |
| `world_regions` | WorldRegion | 地点/区域层级树 |
| `event_logs` | EventLog | 状态变更事件日志 |
| `memories` | Memory | 对话/摘要/推荐/日志 三层记忆 |
| `user_npcs` | UserNPC | 用户级 NPC 总库（跨世界共享模型数据） |
| `worldview_shares` | WorldviewShare | 开源世界观共享库（作者推送，全用户可见） |
| `worldview_ratings` | WorldviewRating | 开源世界观评分（1-5 星，同用户重复评覆盖） |

---

## WorldSession 表（sessions）

世界观平台新增两列（启动时无损迁移自动添加）：

```sql
worldview         TEXT DEFAULT 'xianxia_v1'   -- 会话绑定的世界观包 id
worldview_version TEXT DEFAULT ''            -- 创建时钉住的包版本
```

- `worldview`：决定该会话使用哪个世界观包（加载 system_prompt/表单/约束/地理等）
- `worldview_version`：会话创建时记录包版本；包升级时检测但不自动迁移旧会话（升级检测日志记录）
- **迁移**：`engine.py::init_db()` 在 create_all 前用 `PRAGMA table_info(sessions)` 判断，缺列时 `ALTER TABLE ADD COLUMN ... DEFAULT`——SQLite 元数据级操作，无损、不重写既有行。已上线数据自动获得 `xianxia_v1` 默认值

---

## NPC_Relationship 表

```sql
id            TEXT PRIMARY KEY     -- UUID hex[:12]
session_id    TEXT NOT NULL         -- FK → sessions.id
source_id     TEXT                  -- FK → npcs.id (nullable, 非NPC实体可为空)
source_name   TEXT NOT NULL         -- 发起方姓名（始终有值）
target_id     TEXT                  -- FK → npcs.id (nullable)
target_name   TEXT NOT NULL         -- 接收方姓名（始终有值）
rel_type      TEXT NOT NULL         -- 关系类型，支持多重（如 "母亲/师父"）
description   TEXT DEFAULT ''       -- 关系详细描述
affinity      INTEGER DEFAULT 0     -- 亲密度 -100~+100
updated_at    DATETIME              -- 最后更新时间
```

### 关系类型

支持 `/` 分隔的多重标签，如 `"母亲/师父"`。常用类型：

- 师徒 / 师兄弟 / 师姐妹
- 夫妻 / 配偶 / 恋人 / 道侣
- 姐妹 / 兄弟 / 母女 / 父子
- 仇敌 / 敌人 / 宿敌 / 竞争者
- 朋友 / 知己
- 合作 / 依附 / 交易
- 上级 / 下属
- 抢夺 / 夺宝

### 设计说明

- 一条边 = 一对 `(source, target)` 的有向关系。双向关系需要两条边。
- 关系不自动删除——只通过 LLM 增量更新中的覆盖来改变。
- `source_id`/`target_id` 可为空：因为可以存在非 NPC 实体（如未创建 NPC 记录的角色、组织等）。

---

## Player 表

```sql
id                  TEXT PRIMARY KEY       -- UUID hex[:12]
session_id          TEXT NOT NULL UNIQUE   -- FK → sessions.id
name                TEXT DEFAULT '无名修士'
cultivation         TEXT DEFAULT '凡人'
location            TEXT DEFAULT '青云山·山门'
inventory           JSON DEFAULT []        -- [{name, type, quantity, desc}, ...]
status              JSON DEFAULT {}        -- freeform status flags
long_term_abilities JSON DEFAULT []        -- permanent abilities
attributes          JSON DEFAULT {}        -- 结构化角色属性（见下方）
```

### Player.attributes 结构

attributes 是 Player 的核心扩展字段，采用 JSON 对象存储，分组如下：

```jsonc
{
  // ── Background / Identity ──
  "age": 18,
  "gender": "男",
  "background": "orphan",                 // 出身（模板 key）
  "identity": "scholar",                  // 身份（模板 key）
  "identity_custom": "",                  // 自定义身份文本（identity="custom" 时）
  "identity_desc": "书生",                // 身份展示名
  "background_summary": "自幼父母双亡...",
  "personality": "kind",
  "personality_custom": "",

  // ── Spiritual Root ──
  "spiritual_root": "金灵根",
  "talent_note": "中品",

  // ── Golden Finger ──
  "golden_finger_id": "reincarnation",
  "golden_finger_name": "转世重生",
  "golden_finger_tagline": "前尘往事皆知晓",
  "golden_finger_desc": "保留前世记忆...",

  // ── Extensions（扩展属性）──
  "height": 175,
  "weight": 65,
  "appearance_brief": "相貌平平",
  "appearance_summary": "",
  "moral_character": "节操正常",
  "sexual_knowledge": "粗浅",
  "fertility": "正常",
  "special_constitution": "",
  "lifestyle_summary": "",
  "clothing": "青衫长袍",
  "current_action": "",
  "current_pose": "",
  "visible_state": "",
  "location_hierarchy": "青云山·山门",
  "travel_log": [],                       // [{action, destination, time}, ...]

  // ── Economy ──
  "savings": "10块下品灵石",
  "monthly_income": "5块下品灵石",

  // ── Relations ──
  "relations": []                         // [{npc_id, affinity, ...}, ...]
}
```

> 该字段由 `player_manager.py` 中的 `apply_character` 方法组装，`game_engine.py` 和 `api/routes.py` 中的各端点按需读取/写入子字段。

---

## NPC 表

```sql
id                    TEXT PRIMARY KEY     -- UUID hex[:12]
session_id            TEXT NOT NULL         -- FK → sessions.id
name                  TEXT NOT NULL
identity              TEXT DEFAULT '散修'
appearance            TEXT DEFAULT ''
personality           TEXT DEFAULT ''
cultivation           TEXT DEFAULT '凡人'
location              TEXT DEFAULT ''
relations             JSON DEFAULT {}       -- {player_relation, affinity_score, ...}
abilities             JSON DEFAULT []
equipment             JSON DEFAULT []
long_term_state       JSON DEFAULT {}       -- 持久状态（含 model 子字段）
short_term_state      JSON DEFAULT {}       -- 临时状态（场景切换时清除）
behavior              TEXT DEFAULT ''       -- 当前行为描述
is_important          INTEGER DEFAULT 0     -- 玩家标记 ⭐
npc_type              TEXT DEFAULT 'named'  -- named / background
gender                TEXT DEFAULT ''
age                   INTEGER              -- NULL = 未知
is_alive              INTEGER DEFAULT 1
source_user_npc_id    TEXT                  -- FK → user_npcs.id
```

### NPC.long_term_state["model"] 结构

重要 NPC（⭐）在标记后由 `npc_modeler.py` 调用 LLM 生成结构化模型，存入 `long_term_state["model"]`：

```jsonc
{
  "model_version": "1.0",

  "basic": {
    "name": "", "race": "", "gender": "", "age": 0,
    "height": "", "cultivation": "", "identity": "",
    "faction": "", "position": ""
  },

  "appearance": {
    "overall_impression": "", "body_proportion": "", "aura": "",
    "face":     { "shape": "", "features": "", "eyes": "", "lashes": "",
                  "eyebrows": "", "nose": "", "lips": "", "teeth": "",
                  "dimples": "", "tear_mole": "", "expression_habit": "" },
    "skin":     { "color": "", "luster": "", "fineness": "" },
    "hair":     { "length": "", "style": "", "color": "", "ornament": "" },
    "legs":     { "length": "", "muscle_tone": "", "thighs": "" },
    "feet":     { "shape": "", "size": "" },
    "hands":    { "fingers": "", "back": "" },
    "neck": "", "collarbone": "", "shoulders": "",
    "waist": "", "belly": "", "hips": ""
  },

  "voice": { "timbre": "", "speed": "", "volume": "" },

  "clothing": {
    "type": "", "color": "", "material": "", "pattern": "",
    "collar": "", "outerwear": "", "belt": "", "hosiery": "", "shoes": ""
  },

  "jewelry": { "earrings": "", "necklace": "", "rings": "", "bracelets": "" },

  "equipment": [
    { "name": "", "description": "", "position": "" }
  ],

  "behavior": {
    "stance": "", "sitting": "", "gait": "", "smile": "",
    "mannerisms": "", "speech_rhythm": "", "catchphrase": ""
  },

  "speech_style": {
    "word_habits": "", "particles": "", "address_player": "",
    "address_others": "", "when_angry": ""
  },

  "combat_style": {
    "preference": "", "weapon_usage": "", "battle_cry": "",
    "spirit_power_signature": ""
  },

  "personality": {
    "core": "", "values": "", "principles": "", "bottom_line": "",
    "interests": "", "fears": "", "aversions": "", "likes": "", "obsession": ""
  },

  "background": {
    "history": "", "major_events": "", "faction_affiliation": "", "family": ""
  },

  "knowledge_bounds": {
    "knows": [], "does_not_know": [], "suspicious_of": []
  },

  "attitude_to_player": {
    "surface": "", "true_feelings": "", "relationship_trend": ""
  },

  "relationships": {
    "father": "", "mother": "", "spouse": "", "master": "",
    "senior_brother": "", "senior_sister": "",
    "junior_brother": "", "junior_sister": "",
    "teacher": "", "superior": "", "subordinate": "",
    "lover": "", "fiance": "", "beloved": "",
    "rival": "", "pursuer": "",
    "friends": [],
    "enemies": []
  },

  "nsfw": {
    "is_virgin": true,
    "fertility": "",
    "desire_toward_target": "",
    "rejection_toward_target": "",
    "male_genital": "",
    "female_genital": ""
  }
}
```

> 完整 schema 定义见 `backend/ane/modules/npc_modeler.py`。
> 通过 `render_model_for_prompt()` 函数渲染为结构化文本后注入 LLM prompt 的 [重要人物] 段。

---

## WorldRegion 表

```sql
id            TEXT PRIMARY KEY     -- UUID hex[:12]
session_id    TEXT NOT NULL         -- FK → sessions.id
name          TEXT NOT NULL
region_type   TEXT DEFAULT 'area'  -- area / city / sect / building / resource
description   TEXT DEFAULT ''
parent_id     TEXT                  -- FK → world_regions.id (自引用树)
attributes    JSON DEFAULT {}       -- type-specific data
```

---

## EventLog 表

```sql
id            TEXT PRIMARY KEY     -- UUID hex[:12]
session_id    TEXT NOT NULL         -- FK → sessions.id
event_type    TEXT NOT NULL         -- QuestAccepted, Travel, Combat, ...
timestamp     DATETIME
world_time    TEXT DEFAULT ''
data          JSON DEFAULT {}       -- event payload
```

---

## Memory 表

```sql
id            TEXT PRIMARY KEY     -- UUID hex[:12]
session_id    TEXT NOT NULL         -- FK → sessions.id
memory_type   TEXT NOT NULL         -- 见下方枚举
content       TEXT NOT NULL
turn_number   INTEGER DEFAULT 0    -- 所属 turn
created_at    DATETIME
```

### memory_type 枚举

| 值 | 说明 |
|----|------|
| `conversation` | 每轮 LLM 原始对话回合（按需裁剪） |
| `shortmemory` | 短期摘要，覆盖最近若干回合 |
| `longmemory` | 长期摘要，跨 session 持久摘要 |
| `prompt` | 构建 prompt 时的中间产物（调试用） |
| `llm_log` | LLM 原始请求/响应日志 |
| `recommendations` | NPC 推荐/标签等零散结构数据 |

> 管理策略见 `backend/ane/modules/memory_manager.py` 中的裁剪与合并逻辑。

---

## UserNPC 表（跨世界总库）

```sql
id            TEXT PRIMARY KEY     -- UUID hex[:12]
user_id       TEXT NOT NULL         -- FK → users.id
name          TEXT NOT NULL         -- 角色名（同 user 下唯一）
model_data    JSON DEFAULT {}       -- 结构化模型数据（同 npc_modeler schema）
tags          JSON DEFAULT []       -- 用户自定义标签
created_at    DATETIME
updated_at    DATETIME

UNIQUE(user_id, name)
```

用于在**用户级别**跨世界共享 NPC 模型数据。一个用户创建的 NPC 模型可导入多个世界的 NPC 实例。导入后 `NPC.source_user_npc_id` 指向此条记录。

## WorldviewShare 表（开源共享库，v1.2）

```sql
id            TEXT PRIMARY KEY     -- UUID hex[:12]
user_id       TEXT NOT NULL         -- FK → users.id（推送者）
worldview_id  TEXT NOT NULL         -- 包 id（worldviews/<id>/ 目录）
title         TEXT NOT NULL         -- 分享标题
description   TEXT DEFAULT ""       -- 简介
tags          JSON DEFAULT []       -- 标签（作者点选，≤12 个）
version       TEXT DEFAULT ""       -- 推送时的包版本
created_at    DATETIME
updated_at    DATETIME
```

每个包最多一条共享记录（重新推送覆盖元数据）。`DELETE /worldviews/share` 仅作者本人可撤销。

## WorldviewRating 表（开源评分，v1.2）

```sql
id            TEXT PRIMARY KEY     -- UUID hex[:12]
user_id       TEXT NOT NULL         -- FK → users.id
worldview_id  TEXT NOT NULL         -- 被评分的包 id
rating        INT NOT NULL          -- 1-5 星
created_at    DATETIME

UNIQUE(user_id, worldview_id)
```

同一用户对同一包重复评分覆盖旧值。列表接口按 worldview_id 聚合 `COUNT` + `AVG`。
