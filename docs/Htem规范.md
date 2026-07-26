# Htem — 角色扮演统一 Prompt 上下文格式规范

> 版本：1.0  
> 适用项目：AI Narrative Engine (ANE)  
> 设计原则：所有数据来源于数据库，由 Prompt Builder 自动组装生成

---

## 目录

1. [概述](#1-概述)
2. [Htem 板块总览](#2-htem-板块总览)
3. [板块详细规范](#3-板块详细规范)
   - [3.0 Narrative（叙事正文，不可见）](#30-narrative-叙事正文不可见)
   - [3.1 World（世界规则层）](#31-world-世界规则层)
   - [3.2 Player（玩家状态层）](#32-player-玩家状态层)
   - [3.3 NPC（在场 NPC 数据层）](#33-npc-在场-npc-数据层)
   - [3.4 Scene（当前场景层）](#34-scene-当前场景层)
   - [3.5 Constraints（场景约束层）](#35-constraints-场景约束层)
   - [3.6 Agentic State（代理状态层）](#36-agentic-state-代理状态层)
   - [3.7 Memory（记忆层）](#37-memory-记忆层)
   - [3.8 Story / Related Characters（故事管理层）](#38-story--related-characters-故事管理层)
   - [3.9 Action Suggestions（行动建议层）](#39-action-suggestions-行动建议层)
   - [3.10 Output Rules（输出规范层）](#310-output-rules-输出规范层)
4. [数据库映射表](#4-数据库映射表)
5. [分隔符与编码规范](#5-分隔符与编码规范)
6. [裁剪策略](#6-裁剪策略)
7. [与 Prompt Builder 的集成方案](#7-与-prompt-builder-的集成方案)
8. [实施路线图](#8-实施路线图)

---

## 1. 概述

### 1.1 Htem 是什么

`Htem`（Hyper-Text Environment Metadata）是一套为 AI 角色扮演引擎设计的**结构化上下文编码格式**。将游戏世界、角色、记忆等离散数据压缩为 LLM 可高效解析的紧凑文本块。

### 1.2 与 ANE 的关系

- ANE 的 **数据库** 是唯一数据来源（架构原则 3）
- **Prompt Builder** 是唯一生成 Htem 的模块（架构原则 5）
- Htem 是 Prompt Builder 的标准化输出格式
- Htem **只进入 LLM 上下文**，不进入用户可见的 narrative

### 1.3 设计目标

| 目标 | 说明 |
|------|------|
| 数据驱动 | 所有字段均能从 DB 自动生成，零人工手写 |
| 可裁剪 | 支持按 token 预算动态裁剪，有明确的裁剪优先级 |
| 可扩展 | 新字段不影响旧解析逻辑 |
| 信息密度 | 用分隔符而非自然语言连接词压缩信息 |
| 角色一致性 | 角色行为、感官、意图有明确的声明机制 |

---

## 2. Htem 板块总览

### 2.1 板块一览

| 序号 | 板块 | 标记 | 优先级 | 更新频率 | ANE 当前状态 |
|------|------|------|--------|----------|-------------|
| 1 | World | `[世界规则]` | 高 | Session 级（几乎不变） | 有（硬编码单行） |
| 2 | Player | `[用户扮演角色]` | **不可裁剪** | 每轮可能变 | 有（基础字段） |
| 3 | NPC | `[重要人物]` / `[当前交互角色]` | 高 | 随场景切换 | 有（扁平字段） |
| 4 | Scene | `[当前场景]` | 高 | 随位置变化 | 有（基础字段） |
| 5 | Constraints | `[场景约束]` | 高 | 随场景切换 | 有（叙事约束模块） |
| 6 | Agentic State | `[本轮代理]` | 中 | 每轮可能变 | **缺失** |
| 7 | Memory Layers | `[世界事实]` / `[剧情回顾]` / `[最近对话]` | 中/低 | 每轮递增 | 有（三层记忆） |
| 8 | Story / Related | `[相关的未登场人物]` | 低 | 随场景切换 | **缺失** |
| 9 | Action Suggestions | `[推荐行动]` | 低（可选） | 每轮生成 | **缺失** |
| 10 | Output Rules | （嵌入 System Prompt） | **不可裁剪** | Session 级 | 有（硬编码） |

### 2.2 板块顺序逻辑

```
不可变 → 可变 | 全局 → 局部 | 背景知识 → 即时刺激
```

1. **World** — 世界设定，几乎不变
2. **Player** — 模型需要随时知道"谁在行动"
3. **NPC** — 交互对象的完整画像
4. **Scene** — 当前所在的环境
5. **Constraints** — 本场景的规则边界
6. **Agentic State** — 本轮代理权分配
7. **Memory Layers** — 过去发生了什么
8. **Story / Related** — 不在场但有关联的角色
9. **Action Suggestions** — 可选的叙事方向建议
10. **Output Rules** — 格式约束（通过 System Prompt 注入）

---

## 3. 板块详细规范

### 3.0 Narrative（叙事正文，不可见）

用户阅读的 pure narrative 文本**不属于 Htem**。它由 LLM 生成后经 Output Parser 分离，直接返回给前端。

**归属**：LLM 输出的 `narrative` 字段 → 前端渲染  
**与 Htem 的关系**：Htem 是 LLM 的输入上下文，narrative 是 LLM 的输出

---

### 3.1 World（世界规则层）

#### 字段定义

```
[世界规则]
名称：{session.name}
历法：{session.world_time_label}（大荒历七四二年）
时代背景：{world_context_long}
社会秩序：{world_law_description}
关键势力：
  - {faction_1}｜{faction_1_summary}
  - {faction_2}｜{faction_2_summary}
灵气规则：{spiritual_energy_rules}
```

#### 字段说明

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `名称` | str | `WorldSession.name` | 世界/存档名 |
| `历法` | str | `WorldSession.world_time` + 自定义纪元名 | 如"大荒历七四二年·秋季·正午" |
| `时代背景` | str | WorldRegion 根节点的 `attributes.era_description` | 一段话描述当前时代特征 |
| `社会秩序` | str | WorldRegion 根节点的 `attributes.law_description` | 世界观化的法律/规则（如"各宗门联合制定的《基础修真律》"） |
| `关键势力` | list | WorldRegion 中 `region_type=sect` 的记录 | 缩写为名称+一句话 |
| `灵气规则` | str | `attributes.spiritual_rules` | 修炼体系的基本约束 |

#### 对应 ANE 改动

- `WorldRegion` 的 `attributes` JSON 字段需新增 `era_description`, `law_description`, `spiritual_rules` 等键
- `PromptContext.world_context` 从单行字符串改为结构化 dict → Prompt Builder 组装

---

### 3.2 Player（玩家状态层）

#### 字段定义

```
[用户扮演角色]
姓名：{player.name}
人设：{player.attributes.age}岁，身高{player.attributes.height}cm，
      体重{player.attributes.weight}kg，{player.attributes.appearance_brief}。
      {player.attributes.background_summary}
性格：{player.attributes.personality}
资质：{player.attributes.spiritual_root}灵根，{player.attributes.talent_note}
修为：{player.cultivation}
经济：每月固定收入{player.attributes.monthly_income}，
      现存款{player.attributes.savings}。
特殊体质：{player.attributes.special_constitution}
关系网：
{for rel in player.attributes.relations}
  - {rel.target}→{rel.type}/{rel.nature}→{rel.note}
{endfor}
能力：
{for ab in player.long_term_abilities}
  - {ab.name}｜{ab.description}
{endfor}
穿着：{player.attributes.clothing}
道具：{for item in player.inventory}{item.name}（{item.description}）、{endfor}
正在：{player.attributes.current_action}
表情/姿势/动作：{player.attributes.current_pose}，
                  {player.attributes.visible_state}
具体位置：{player.location_hierarchy}
```

#### 字段说明

| 字段 | 类型 | 来源 | 新增？ |
|------|------|------|--------|
| `姓名` | str | `Player.name` | 已有 |
| `人设` | composite | `Player.attributes` 中的子字段 | **新增子字段** |
| `性格` | str | `Player.attributes.personality` | **新增** |
| `资质` | str | `Player.attributes.spiritual_root` | **新增** |
| `修为` | str | `Player.cultivation` | 已有 |
| `经济` | str | `Player.attributes.{monthly_income, savings}` | **新增** |
| `特殊体质` | str | `Player.attributes.special_constitution` | **新增** |
| `关系网` | list | `Player.attributes.relations` (list[dict]) | **新增** |
| `能力` | list | `Player.long_term_abilities` | 已有，需结构化 |
| `穿着` | str | `Player.attributes.clothing` | **新增** |
| `道具` | list | `Player.inventory` | 已有 |
| `正在` | str | `Player.attributes.current_action` | **新增** |
| `表情/姿势/动作` | str | `Player.attributes.{current_pose, visible_state}` | **新增** |
| `具体位置` | str | `Player.location` → 展开为层级链 | 已有，需展开 |

**关键设计**：`attributes` JSON 字段承载所有自由格式的角色属性。这是 ANE 的核心扩展机制——不需要新增数据库列，只需在 `attributes` 中约定键名语义。

#### 状态更新机制

Player 的 `current_action`、`current_pose`、`visible_state` 每轮由 **上一轮的 state_changes + LLM narrative 推断** 更新。更新管线：

```
LLM narrative → Output Parser → state_changes 
→ Event Bus → PlayerManager.update_status() → attributes.current_action 更新
```

---

### 3.3 NPC（在场 NPC 数据层）

NPC 数据分为两个子板块：
- `[重要人物]` — 核心 NPC（始终在场的剧情关键角色）
- `[当前交互角色]` — 当前场景中与本轮直接相关的 NPC

`[当前交互角色]` 使用**完整字段格式**（深度画像）。  
`[重要人物]` 使用**精简格式**（仅关键字段）。

#### 完整字段定义（当前交互角色）

```
[当前交互角色]
姓名：{npc.name}
人设：{npc.attributes.age}岁，身高{npv.attributes.height}cm，
      体重{npc.attributes.weight}kg，{npc.attributes.appearance_summary}。
      {npc.attributes.background_summary}
性格：{npc.personality}
资质：{npc.attributes.spiritual_root}，
      {npc.attributes.talent_note}
修为：{npc.cultivation}
经济：月例{npc.attributes.monthly_income}，
      存款{npc.attributes.savings}
身份：{npc.identity}
特殊体质：{npc.attributes.special_constitution}
心性：{npc.attributes.moral_character}
性知识：{npc.attributes.sexual_knowledge}
受孕体质：{npc.attributes.fertility}
能力：
{for ab in npc.abilities}
  - {ab.name}｜{ab.description}｜{ab.power_level}
{endfor}
穿着：
  -上身：（{npc.attributes.upper_garment}｜{npc.attributes.upper_inner}）
  -下身：（{npc.attributes.lower_garment}｜{npc.attributes.lower_inner}｜{npc.attributes.footwear}）
  -物品：{for item in npc.equipment}{item.name}｜{item.position}、{endfor}
位置/正在：{npc.location}｜{npc.behavior}→{npc.short_term_state.intended_action}
          →（{npc.short_term_state.intended_timing}）{npc.short_term_state.intended_detail}
表情/姿势/动作：{npc.short_term_state.current_pose}，
                {npc.short_term_state.visible_state}
称呼：{npc.relations.addressing}→{npc.relations.addressing_term}
关系网：
{for rel in npc.relations.entries}
  - {rel.target}→{rel.type}/{rel.nature}→{rel.external_note}
{endfor}
{for rel in npc.relations.entries_with_inner_voice}
  - {rel.target}→"{rel.inner_voice}"
{endfor}
异常部位：
{for sense in npc.short_term_state.special_perceptions}
  - {sense.type}→能清晰感知到{sense.target}因{sense.reason}
{endfor}
生活：{npc.attributes.lifestyle_summary}
```

#### 精简字段定义（重要人物）

```
[重要人物]
- {npc.name}｜{npc.identity}｜{npc.cultivation}｜
  位置：{npc.location}｜正在：{npc.behavior}→
  （{npc.short_term_state.intended_timing}）{npc.short_term_state.intended_detail}｜
  距离玩家{npc.short_term_state.distance_to_player}
```

#### 字段说明

| 字段 | 来源 DB 列 | 新增？ |
|------|-----------|--------|
| 基本属性（年龄/身高/体重/外貌） | `NPC.attributes` (JSON) | **新增子字段** |
| 性格 | `NPC.personality` (Text) | 已有 |
| 修为 | `NPC.cultivation` | 已有 |
| 身份 | `NPC.identity` | 已有 |
| 穿着（结构化） | `NPC.attributes.{upper_garment, lower_garment, ...}` | **新增** |
| 装备 | `NPC.equipment` (JSON) | 已有，需结构化 |
| 位置/行为 | `NPC.location` + `NPC.behavior` | 已有 |
| 意图时间线 | `NPC.short_term_state.{intended_action, intended_timing, intended_detail}` | **新增** |
| 表情/姿势 | `NPC.short_term_state.{current_pose, visible_state}` | **新增** |
| 称呼 | `NPC.relations.{addressing, addressing_term}` | **新增** |
| 关系网（含内心独白） | `NPC.relations.{entries, entries_with_inner_voice}` | **新增** |
| 异常感官 | `NPC.short_term_state.special_perceptions` | **新增** |

**关键设计**：`short_term_state` 是每场景清空的临时状态（ANe 已有此字段但未使用）。所有"本轮此刻"的状态（姿势、意图、感官、距离）都放在这里。

---

### 3.4 Scene（当前场景层）

#### 字段定义

```
[当前场景]
位置层级：{province}｜{region}｜{sect/city}｜{sub_location}
具体位置：{player_location}
时间/天气：{world_time_label}｜{weather}
环境描写：{location_description}
在场人物：
{for npc in all_present_npcs}
  - {npc.name}（{npc.identity}）— {npc.short_term_state.scene_action}
{endfor}
可感知物：{for obj in scene.perceptible_objects}{obj.name}、{endfor}
环境氛围：{scene.atmosphere}
```

#### 字段说明

| 字段 | 来源 | 新增？ |
|------|------|--------|
| 位置层级 | `WorldRegion` 的 parent chain | 已有（Prompt Builder 第 7 步） |
| 具体位置 | `Player.location` → `WorldRegion` | 已有 |
| 时间/天气 | `WorldSession.world_time` + `Weather` 表（将来） | 部分已有 |
| 环境描写 | `WorldRegion.description` | 已有 |
| 在场人物 | Active Set（core + nearby NPCs） | 已有逻辑，需格式化 |
| 可感知物 | `Scene` 新表 或 `WorldRegion.attributes.perceptible_objects` | **新增** |
| 环境氛围 | `WorldRegion.attributes.atmosphere` | **新增** |

---

### 3.5 Constraints（场景约束层）

#### 字段定义

```
[场景约束]
硬限制：
{for rule in constraints.hard}
  - {rule}
{endfor}
软引导：
{for rule in constraints.soft}
  - {rule}
{endfor}
强制触发：
{for trigger in constraints.triggers}
  - 当{trigger.condition}时：{trigger.action}
{endfor}
```

#### 字段说明

| 字段 | 来源 | 新增？ |
|------|------|--------|
| `硬限制` | `NarrativeConstraints` 模块 — `ConstraintSet.hard` | 重构 |
| `软引导` | `NarrativeConstraints` 模块 — `ConstraintSet.soft` | 重构 |
| `强制触发` | `NarrativeConstraints` 模块 — `ConstraintSet.triggers` | **新增** |

**与 ANE 现有约束系统的关系**：ANE 已有 `narrative_constraints` 模块和 `ConstraintSet`，但当前只输出一个扁平的 `rules_text`。需要重构 `to_prompt_block()` 方法，按 hard/soft/triggers 分类输出，并支持"世界观化表述"——将元指令翻译为世界观内的规则语言。

示例：
- 元指令："禁止角色展示超出筑基期的能力"
- 世界观化表述："此方天地灵气稀薄，筑基期以上修为者的灵力外放被天然压制，无法施展高阶法术"

---

### 3.6 Agentic State（代理状态层）**[新增]**

#### 字段定义

```
[本轮代理]
当前叙述视角：{agentic.pov_character}（玩家角色）
可主动行动角色：{for c in agentic.actionable_characters}{c.name}、{endfor}
NPC 主动行为配额：{agentic.npc_action_quota} 个
玩家输入模式：{agentic.input_mode}
场景推进边界：{agentic.scene_boundary}
```

#### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `pov_character` | str | 当前叙事视角，通常为玩家 |
| `actionable_characters` | list[str] | 本轮模型可以主动驱动的角色列表 |
| `npc_action_quota` | int | 本轮最多有多少个 NPC 可以主动行动（防止所有 NPC 抢戏） |
| `input_mode` | enum | `waiting`（等待玩家输入）/ `auto_continue`（允许自动推进） |
| `scene_boundary` | str | 场景结束的信号，如"直到玩家做出决定"、"直到对话自然结束" |

**目的**：防止模型不知道它能"动"谁。商业系统中这个信息是隐式的（由系统 prompt 承载），显式声明后模型行为更可控。

**生成方式**：由 Game Engine 在 turn 管线中根据 `val.intent` + `active_set` + `scene_context` 自动生成，不需要 LLM。

---

### 3.7 Memory（记忆层）

记忆层拆分为三个子板块：

#### 3.7.1 世界事实（Facts）

```
[世界事实]
{for fact in facts}
  - [{fact.category}] {fact.content}
{endfor}
```

对应 ANE：`Fact` 表 → `memory_manager.get_facts()`

**裁剪规则**：按 `priority` 降序排列，超出预算时裁剪低优先级 facts。

#### 3.7.2 剧情回顾（Summary）

```
💾剧情回顾：
{memory_type=summary 的最新一条 content}
```

对应 ANE：`Memory` 表（`memory_type="summary"`）→ `memory_manager.get_latest_summary()`

#### 3.7.3 短记忆区（Conversation Window）

```
💾短记忆区 {current_count}/{max_count}：
{for mem in recent_conversations}
  - Turn {mem.turn_number}：{mem.content_compact}
{endfor}
```

对应 ANE：`Memory` 表（`memory_type="conversation"`）→ `memory_manager.get_conversation()`

**槽位化改进**：
- 当前 ANE 用 `CONVERSATION_WINDOW_SIZE`（默认 20）做截断
- 商业系统展示 `短记忆区1/6` 表示固定 6 槽，填满后 FIFO + 自动摘要
- **建议**：将 conversation window 从"最近 N 轮全部显示"改为"最近 N 轮 + 超限自动摘要"，并在 prompt 中展示计数

---

### 3.8 Story / Related Characters（故事管理层）

#### 字段定义

```
[相关的未登场人物]
{for npc in related_but_absent}
  - {npc.name}｜{npc.identity}｜{npc.cultivation}｜
    {npc.attributes.one_line_relevance}｜当前位置：{npc.location}
{endfor}
```

#### 字段说明

| 字段 | 来源 | 新增？ |
|------|------|--------|
| `related_but_absent` 列表 | 从 `Fact` 表中检索与在场 NPC/Player 有关系但不在 Active Set 中的角色 | **新增逻辑** |

**检索逻辑**：
1. 从 `Fact` 表中筛选 `category=character` 的 fact
2. 提取 fact 中提及的 NPC 名称
3. 排除已在 Active Set 中的 NPC
4. 去重后取 priority 最高的 5 个

**目的**：
- 告诉模型"这些人存在"（可用于伏笔、对话提及）
- 告诉模型"这些人不在这里"（防止凭空出现）
- 只需要姓名 + 一句话关系，不需要完整数据

---

### 3.9 Action Suggestions（行动建议层）**[新增，可选]**

#### 字段定义

```
[推荐行动]
{for suggestion in action_suggestions}
  {suggestion.index}. {suggestion.description}
{endfor}
```

#### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `action_suggestions` | list[dict] | 2-4 条合理的叙事方向候选项 |
| `suggestion.index` | int | 序号 |
| `suggestion.description` | str | 一句话行动描述（30 字以内） |

**生成方式**（可选，非必须）：
- **轻量版（Phase 1+）**：基于规则引擎 —— 根据玩家能力 + 场景中 NPC 的意图 + 最近事件 → 匹配预定义的 action 模板
- **完整版（Phase 3+）**：由一个廉价模型（如 Haiku）生成 3 个候选方向

**作用**：
- 缩小 LLM 的叙事空间，提高输出质量和一致性
- 不是替玩家做决定（玩家随时可以输入不同行动）
- 在 prompt 中作为可选辅助块，不强制 LLM 遵循

---

### 3.10 Output Rules（输出规范层）

不独立成块，嵌入 **System Prompt** 中，作为不可裁剪的核心部分。

```
输出格式（严格遵守，必须完整闭合）：
```json
{
  "narrative": "正文内容...",
  "state_changes": [...],
  "suggest_summary": false
}
```

state_changes 中的事件类型：
location_change, cultivation_change, status_change, npc_status,
item_added, item_removed, relationship_change, quest_accepted,
quest_completed, player_name_change, character_status,
npc_enters, npc_leaves

角色扮演行为准则：
- 只描写玩家角色在当前场景中能直接感知到的事物
- 不得在 narrative 中夹杂元评论
- 不替玩家做决定、替玩家说话、跳过时间
- 每次输出控制在当前场景的自然边界内
```

**对应 ANE**：已完整实现（`SYSTEM_PROMPT` 常量 + `output_parser.py`），无需额外改动。

---

## 4. 数据库映射表

### 4.1 Player 表新字段（`attributes` JSON 内）

```json
{
  "age": 19,
  "height": 178,
  "weight": 65,
  "appearance_brief": "黑色短发，相貌平平",
  "appearance_summary": "身材匀称略显清瘦，手掌有常年干粗活留下的老茧",
  "personality": "谨慎隐忍，懂得察言观色，三观偏向利己但保有底线",
  "spiritual_root": "五灵根",
  "talent_note": "吐纳灵气犹如老牛拉破车",
  "background_summary": "受限于底层外门弟子身份，信息茧房严重",
  "monthly_income": "3块下品灵石",
  "savings": "10块下品灵石",
  "special_constitution": "一滴精血蕴含磅礴生机，阳气极盛",
  "clothing": "灰蓝色齐云宗外门制式长袍、黑色布鞋",
  "moral_character": "节操正常",
  "sexual_knowledge": "基础",
  "fertility": "正常",
  "lifestyle_summary": "经济拮据，过往犹如透明人，无依无靠",
  "current_action": "向路过的内门弟子行礼",
  "current_pose": "面色恭敬，双手抱拳，身子微微前倾",
  "visible_state": "右手大拇指有新鲜划伤，血液已止住",
  "relations": [
    {
      "target": "齐云宗外门执事",
      "type": "上下级",
      "nature": "管理",
      "note": "按时发工资打卡干活，无多余交集"
    }
  ]
}
```

### 4.2 NPC 表新字段（`attributes` JSON 内）

```json
{
  "age": 21,
  "height": 168,
  "weight": 52,
  "appearance_summary": "清冷绝美，肌肤赛雪。黑色及腰长发用一根玉簪挽起。身材高挑，胸脯饱满伟岸，腰肢纤细不堪一握，双腿笔挺",
  "background_summary": "身为内门精英，阅历丰富，但被家族和宗门长辈保护得极好，对底层男修的龌龊心思缺乏防备",
  "spiritual_root": "极品水灵韵",
  "talent_note": "自幼展现极高天赋被收入齐云宗内门",
  "monthly_income": "500中品灵石",
  "savings": "数万",
  "special_constitution": "极品水灵韵底子，极易受孕体质",
  "moral_character": "三观端正，节操极高",
  "sexual_knowledge": "几乎为零",
  "fertility": "极易受孕",
  "lifestyle_summary": "自律，喜欢安静修炼。私生活极为干净，除了同门论道外极少外出，性经历白纸一张",
  "upper_garment": "水蓝色流仙裙",
  "upper_inner": "白色丝绸肚兜",
  "lower_garment": "水蓝色百褶长裙",
  "lower_inner": "云纹纯白绸袜",
  "footwear": "流云锦鞋",
  "one_line_relevance": "齐云宗内门精英弟子"
}
```

### 4.3 NPC 表新字段（`short_term_state` JSON 内）

```json
{
  "current_pose": "表情清冷，微微颔首，双手交叠置于小腹前",
  "visible_state": "目光落在许睿流血的手指上",
  "intended_action": "巡视药园",
  "intended_timing": "稍后",
  "intended_detail": "婉拒叶辰的邀请回洞府修炼",
  "distance_to_player": "三步远",
  "scene_action": "正在巡视第三灵药园",
  "special_perceptions": [
    {
      "type": "嗅觉异常敏锐",
      "target": "许睿血液中的奇异馨香",
      "reason": "许睿刚刚与混沌宝珠完成灵魂绑定"
    }
  ]
}
```

### 4.4 NPC 表新字段（`relations` JSON 内）

```json
{
  "addressing": "许睿",
  "addressing_term": "外门师弟",
  "entries": [
    {
      "target": "叶辰",
      "type": "追求者",
      "nature": "同门",
      "external_note": "保持礼貌距离，内心略感厌烦",
      "inner_voice": "大长老之孙实在聒噪，只盼他早日死心"
    }
  ]
}
```

### 4.5 NPC 表新字段（`equipment` JSON 内）

```json
[
  {
    "name": "寒霜剑",
    "position": "悬挂于纤细腰间左侧",
    "description": "出剑自带极寒冻气，同阶战力拔尖"
  }
]
```

### 4.6 WorldRegion 表新字段（`attributes` JSON 内）

```json
{
  "era_description": "浩瀚广袤的修仙界以神州大地为基础，各省级行政区划分为独立的修仙大域",
  "law_description": "各大宗门联合制定的《基础修真律》约束，保障低阶修士基本人权，杜绝无意义的杀戮",
  "spiritual_rules": "灵气充沛，修士以修炼为毕生追求。修为境界：凡人→炼气→筑基→金丹→元婴→化神→炼虚→合体→大乘→渡劫",
  "atmosphere": "烈阳高悬，灵气浓郁",
  "weather": "晴",
  "perceptible_objects": [
    {"name": "三阶灵土", "description": "灵气浓郁的黑色土壤，长满玉髓芝"},
    {"name": "铁线草", "description": "叶缘锯齿状的低级灵草"},
    {"name": "玉髓芝", "description": "成片生长的灵药，通体翠绿半透明"}
  ],
  "factions": [
    {"name": "齐云宗", "summary": "湖北省内中等宗门，规矩森严，占据神农架山脉"}
  ]
}
```

---

## 5. 分隔符与编码规范

### 5.1 分隔符体系

| 符号 | 用途 | 示例 |
|------|------|------|
| `｜`（全角竖线） | 同层属性分隔 | `修为：筑基初期｜位置：神农架` |
| `→` | 因果 / 时间推进 / 意图链 | `正在修炼→（半炷香后）准备出关` |
| `—` | 元信息分隔 / 范围标记 | `距离玩家—三步远` |
| `（）`（全角括号） | 嵌套子信息 / 时间注解 | `（稍后）婉拒邀请` |
| `、` | 列表项分隔（同层） | `灰蓝长袍、黑色布鞋` |
| `""`（全角引号） | 内心独白 / 直接引语 | `"实在聒噪，只盼他早日死心"` |
| `- `（短线 + 空格） | 列表项前导 | `- 林清雪｜内门精英｜筑基初期` |

### 5.2 编码规范

- **全角特殊字符**：分隔符使用全角以在纯文本中保持视觉对齐
- **半角 JSON**：`state_changes` 和 DB 中的 JSON 字段使用标准半角 JSON
- **换行**：
  - 字段之间换一行
  - 列表项之间换一行
  - 板块之间用 `————————`（16 个全角破折号）分隔
- **字段标签**：统一使用中文标签（不混用英文），如 `修为：` 而非 `Cultivation:`
- **空值处理**：字段为空时省略该行，不输出空标签（"经济："后面没东西 → 整行跳过）

---

## 6. 裁剪策略

当 LLM 上下文窗口不足时，按以下优先级裁剪：

### 6.1 裁剪优先级

| 优先级 | 板块 | 裁剪行为 |
|--------|------|---------|
| **P0 不可裁剪** | System Prompt (含 Output Rules) | 永远保留 |
| **P0 不可裁剪** | Player（基础字段：姓名、修为、位置） | 永远保留 |
| **P1 优先保留** | Constraints 硬限制 | 保留 |
| **P1 优先保留** | Scene（位置层级 + 在场人物） | 保留 |
| **P1 优先保留** | 当前交互角色（完整字段） | 保留 |
| **P2 可压缩** | Player（详细人设 → 缩写） | 移除背景描述，保留当前状态 |
| **P2 可压缩** | NPC（穿着 → 省略） | 保留身份/修为/位置/意图 |
| **P2 可压缩** | World（全部保留但压缩） | 移除次要势力，保留当前区域相关 |
| **P3 可截断** | 重要人物（最多 5 个） | 超过 5 个的只显示姓名 |
| **P3 可截断** | 短记忆区（最多 6 条） | 超过 6 条的自动摘要后删除 |
| **P3 可截断** | World Facts（最多 10 条） | 按 priority 降序截取 |
| **P4 可丢弃** | 剧情回顾（Summary） | 丢弃旧的 Summary |
| **P4 可丢弃** | 相关的未登场人物 | 丢弃 |
| **P4 可丢弃** | 推荐行动 | 丢弃 |
| **P4 可丢弃** | Constraints 软引导 | 丢弃 |

### 6.2 裁剪线程

裁剪不在 Prompt Builder 中实现——由 **Retrieval Engine** 在构建 `ActiveSet` 和 `PromptContext` 时控制数据量。Prompt Builder 只负责格式化，不做裁剪决策。

```
Retrieval Engine:
  - core_npcs: 永远全部返回（最多 NPC_COUNT_CORE）
  - nearby_npcs: 最多 8 个
  - facts: 按 priority 降序，最多 15 条
  - related_but_absent: 最多 5 个

Memory Manager:
  - conversation: 最近 6 轮（从 CONVERSATION_WINDOW_SIZE=20 收紧）
  - summary: 只返最新一条
```

---

## 7. 与 Prompt Builder 的集成方案

### 7.1 重构后的 PromptContext

```python
@dataclass
class PromptContext:
    """All the data needed to build a full Htem prompt for one turn."""

    # ── System (P0, 不可裁剪) ──
    system: str = SYSTEM_PROMPT

    # ── World (P2) ──
    world: WorldContext | None = None  # 新：结构化世界数据

    # ── Player (P0) ──
    player: PlayerContext | None = None  # 新：从 Player 模型展开

    # ── NPCs (P1-P2) ──
    interactive_npc: NPCContext | None = None  # 新：当前交互角色（完整）
    core_npcs: list[NPCContext] = field(default_factory=list)  # 精简格式
    nearby_npcs: list[NPCContext] = field(default_factory=list)

    # ── Scene (P1) ──
    scene: SceneContext | None = None  # 新：场景数据

    # ── Constraints (P1) ──
    constraints: ConstraintSet | None = None  # 已有，重构 to_prompt_block()

    # ── Agentic State (P2) ──
    agentic: AgenticContext | None = None  # 新

    # ── Memory (P3-P4) ──
    facts: list[Fact] = field(default_factory=list)
    summary: str = ""
    conversation: list[Memory] = field(default_factory=list)

    # ── Related Characters (P4) ──
    related_absent: list[NPCContext] = field(default_factory=list)

    # ── Action Suggestions (P4, 可选) ──
    suggestions: list[str] = field(default_factory=list)

    # ── User Input (P0) ──
    user_input: str = ""
```

### 7.2 Prompt Builder 组装逻辑

```python
class PromptBuilder:
    def build(self, ctx: PromptContext, budget: TokenBudget | None = None) -> str:
        blocks: list[str] = []

        # P0 blocks — always included
        blocks.append(ctx.system)                    # Output Rules
        blocks.append(self._build_world_block(ctx))  # World
        blocks.append(self._build_player_block(ctx)) # Player

        # P1 blocks
        blocks.append(self._build_npc_block(ctx))    # NPC (完整 + 精简)
        blocks.append(self._build_scene_block(ctx))   # Scene
        blocks.append(self._build_constraints_block(ctx))  # Constraints

        # P2 blocks
        blocks.append(self._build_agentic_block(ctx)) # Agentic State

        # P3 blocks — memory layers
        blocks.append(self._build_facts_block(ctx))
        blocks.append(self._build_summary_block(ctx))
        blocks.append(self._build_conversation_block(ctx))

        # P4 blocks — optional
        if ctx.related_absent:
            blocks.append(self._build_related_block(ctx))
        if ctx.suggestions:
            blocks.append(self._build_suggestions_block(ctx))

        # P0 — User Input (always last)
        blocks.append(f"【玩家输入】\n{ctx.user_input}")

        prompt = "\n\n————————\n\n".join(b for b in blocks if b)
        return prompt
```

### 7.3 板块 Block Builder

每个板块有自己的 `_build_*_block()` 方法，职责单一：
1. 接收结构化输入
2. 按本规范定义的字段格式输出
3. 空值时返回空字符串（上层跳过）

---

## 8. 实施路线图

### Phase 1+ — 立即（1-2 周）

| 步骤 | 内容 | 涉及文件 |
|------|------|---------|
| 1 | 定义 `WorldContext`, `PlayerContext`, `NPCContext`, `SceneContext`, `AgenticContext` dataclass | `prompt_builder.py`（新增） |
| 2 | 重构 `PromptBuilder.build()`，按新板块顺序组装，使用 `————————` 分隔符 | `prompt_builder.py` |
| 3 | 实现 `_build_player_block()` 新版本（结构化子字段） | `prompt_builder.py` |
| 4 | 实现 `_build_npc_block()` 区分完整/精简格式 | `prompt_builder.py` |
| 5 | `_build_scene_block()` 增加在场人物列表和环境氛围 | `prompt_builder.py` |
| 6 | 重构 `narrative_constraints.to_prompt_block()` 支持 hard/soft/triggers 分类 | `narrative_constraints.py` |
| 7 | `AgenticContext` 在 Game Engine turn 管线中自动生成 | `game_engine.py` |
| 8 | `Retrieval Engine` 增加 `related_but_absent` 检索逻辑 | `retrieval_engine.py` |
| 9 | NSFW/NTR/未成年 材料注入 | ✅ game_engine.py + 3个 json |
| 10 | 孕期安全规则 | ✅ prompt_builder.py Rule 37-40 |

### Phase 2 — 后续

| 步骤 | 内容 |
|------|------|
| 11 | NPC 模板生成时自动填充 `attributes` 子字段（穿着、心性等） |
| 12 | `WorldRegion` 模板增加 `attributes`（势力、法律、氛围） |
| 13 | 实现 `ActionSuggestions` 规则引擎 |
| 14 | `short_term_state` 的每场景清空机制 |

### Phase 3 — 远期

| 步骤 | 内容 |
|------|------|
| 15 | Token 预算感知的自动裁剪 |
| 16 | LLM 驱动的 action suggestion 生成 |
| 17 | `short_term_state` 的 AI 自动更新（从 narrative 推断 NPC 姿势/状态变化） |

---

## 附录 A：完整 Htem 示例

以下是一个完整的 Htem 输出示例（基于商业系统场景，映射到 ANE 数据源）：

```
你是一个修仙世界的叙事引擎。你的职责是讲述故事、描写场景、扮演NPC。
[System Prompt 全文...]

————————
[世界规则]
名称：神农修仙纪
历法：大荒历七四二年·秋季·正午
时代背景：浩瀚广袤的修仙界以神州大地为基础，各省级行政区划分为独立的修仙大域。湖北省境内宗门林立，人口稠密经济繁荣。
社会秩序：各大宗门联合制定的《基础修真律》约束，保障低阶修士基本人权，杜绝无意义的杀戮。文明融合了古风仙韵与凡俗市井气，凡人与修士共处同一片天地，壁垒分明又相互依存。
关键势力：
  - 齐云宗｜湖北省内中等宗门，规矩森严，占据神农架山脉
灵气规则：修为境界为凡人→炼气→筑基→金丹→元婴→化神→炼虚→合体→大乘→渡劫。灵气充沛，修士以修炼为毕生追求。

————————
[用户扮演角色]
姓名：许睿
人设：19岁，身高178cm，体重65kg，黑色短发，相貌平平丢进人堆找不着。身材匀称略显清瘦，手掌有常年干粗活留下的老茧。
性格：谨慎隐忍，懂得察言观色，三观偏向利己但保有底线，节操正常。智商在线，情商合格。
资质：五灵根，吐纳灵气犹如老牛拉破车。
修为：炼气期
经济：每月固定收入3块下品灵石，现存款10块下品灵石。
特殊体质：一滴精血蕴含磅礴生机，阳气极盛。
关系网：
  - 齐云宗外门执事→上下级/管理→按时发工资打卡干活，无多余交集
能力：
  - 混沌宝珠｜已灵魂绑定。辅助功法领悟、无限储物空间、无限灵土种植空间、自定义掩盖修为、具化混沌鼎炼丹
穿着：灰蓝色齐云宗外门制式长袍、黑色布鞋
道具：除草铁镰（清理低级灵草周边杂草所用）
正在：向路过的内门弟子行礼
表情/姿势/动作：面色恭敬，双手抱拳，身子微微前倾，右手大拇指有新鲜划伤，血液已止住
具体位置：湖北省｜神农架山脉齐云宗驻地｜外门第三灵药园边缘区

————————
[重要人物]
- 叶辰｜内门大长老嫡孙｜筑基期｜位置：第三灵药园→青石小路上｜
  正在讨好林清雪→（半炷香后）准备邀请林清雪前往武汉主城仙坊游玩｜
  距离玩家三步远

————————
[当前交互角色]
姓名：林清雪
人设：21岁，身高168cm，体重52kg，清冷绝美，肌肤赛雪。黑色及腰长发用一根玉簪挽起。身材高挑，胸脯饱满伟岸，腰肢纤细不堪一握，双腿笔挺。
性格：外冷内热，极重规矩，三观端正，节操极高。智商出众，情商内敛。
资质：极品水灵韵，自幼展现极高天赋被收入齐云宗内门。
修为：筑基初期
经济：月例500中品灵石，存款数万。
身份：内门精英
特殊体质：极品水灵韵底子，极易受孕体质。
心性：心性坚韧，廉耻心极强，处子之身。
性知识：几乎为零。
受孕体质：极易受孕。
能力：
  - 玄冰剑诀｜出剑自带极寒冻气，同阶战力拔尖
穿着：
  -上身：（水蓝色流仙裙｜白色丝绸肚兜）
  -下身：（水蓝色百褶长裙｜云纹纯白绸袜｜流云锦鞋）
  -物品：寒霜剑｜悬挂于纤细腰间左侧
位置/正在：青石小路上｜正在巡视药园→（稍后）婉拒叶辰的邀请回洞府修炼
表情/姿势/动作：表情清冷，微微颔首，目光落在许睿流血的手指上，双手交叠置于小腹前
称呼：许睿→外门师弟
关系网：
  - 叶辰→追求者/同门→保持礼貌距离，内心略感厌烦
  - "大长老之孙实在聒噪，只盼他早日死心"
异常部位：
  - 嗅觉异常敏锐→能清晰感知到许睿血液中因混沌宝珠刚刚绑定而散发的一丝奇异馨香
生活：自幼被收入齐云宗内门，一直在长辈期盼中刻苦修炼，私生活极为干净，性经历白纸一张。

————————
[当前场景]
位置层级：湖北省｜神农架山脉｜齐云宗驻地｜外门第三灵药园边缘区
具体位置：齐云宗外门第三灵药园
时间/天气：大荒历七四二年·秋季·正午时分｜晴，烈阳高悬
环境描写：连绵起伏的原始密林深处，灵气浓郁的三阶灵土，长满玉髓芝。微风拂过，带来泥土与药草的气息。
在场人物：
  - 许睿（玩家）— 正在向路过的内门弟子行礼
  - 林清雪（内门精英）— 正在巡视药园
  - 叶辰（内门大长老嫡孙）— 正在讨好林清雪
可感知物：三阶灵土、铁线草、玉髓芝
环境氛围：烈阳高悬，灵药园宁静祥和，偶有虫鸣鸟叫。

————————
[场景约束]
硬限制：
  - 此方天地灵气虽充沛，但筑基期以上修为者的灵力外放仍受天地自然压制
  - 任何角色不得展示超出其设定修为的能力
软引导：
  - 当前场景为灵药园，应以日常叙事为主，不宜出现战斗情节
  - 林清雪对底层男修的龌龊心思缺乏防备，对话中应体现这种天真
强制触发：
  - 当叶辰离开后：林清雪可能对许睿血液中的异常气息产生好奇

————————
[本轮代理]
当前叙述视角：许睿（玩家角色）
可主动行动角色：许睿、林清雪、叶辰
NPC主动行为配额：2 个
玩家输入模式：waiting
场景推进边界：直到玩家做出反应

————————
[世界事实]
- [world] 玩家许睿初入修仙世界，在齐云宗山门醒来。
- [character] 林清雪是齐云宗内门精英，筑基初期修为。
- [character] 叶辰是内门大长老嫡孙，正在追求林清雪。
- [world] 混沌宝珠已与许睿灵魂绑定。

————————
💾剧情回顾：
许睿在灵药园拔草时意外被割破手指，鲜血滴落触发机缘，成功与隐藏在泥土中的混沌宝珠灵魂绑定，随后遭遇内门精英林清雪与大长老之孙叶辰巡视药园。

————————
💾短记忆区 1/6：
- Turn 1：许睿在灵药园拔草时意外被割破手指，鲜血滴落触发机缘，成功与隐藏在泥土中的混沌宝珠灵魂绑定。随后遭遇林清雪与叶辰巡视药园。

————————
[相关的未登场人物]
- 苏婉儿｜炼丹阁实权长老之妻｜三阶炼丹师｜风韵犹存，掌握大量丹药资源
- 慕清瑶｜武汉主城城主之女｜天灵根绝世天才｜与京城顶级大宗圣子定有婚约

————————
[推荐行动]
1. 假装受宠若惊，低头感谢林清雪的夸奖，等他们离开后立刻探索混沌宝珠的功能。
2. 捂住流血的伤口，大着胆子向林清雪讨要一点止血的低阶灵药，试探这位内门师姐的底线。
3. 借着行礼的姿势，暗中催动混沌宝珠的"掩盖修为"功能，测试是否会被筑基期的林清雪看穿。

————————
【玩家输入】
（林清雪和叶辰从身边走过时）林师姐好，叶师兄好。
```

---

## 附录 B：模块职责变更清单

| 模块 | 当前职责 | 新增职责 |
|------|---------|---------|
| `database/models.py` | 7 张表 | `Player.attributes` / `NPC.attributes` / `NPC.short_term_state` / `NPC.relations` / `NPC.equipment` 的 JSON 结构规范化（不新增列，只约定 JSON 键名） |
| `prompt_builder.py` | 拼接 10 个块 | 按新板块顺序 + 新分隔符 + 每个板块独立 builder 方法 |
| `narrative_constraints.py` | 扁平 rule list | hard/soft/triggers 三层结构，支持世界观化表述 |
| `retrieval_engine.py` | core + nearby NPCs | 增加 `related_but_absent` 检索 |
| `game_engine.py` | 10 步 turn 管线 | 增加 Agentic State 生成步骤 |
| `memory_manager.py` | 三层记忆 | conversation 槽位化（展示计数） |
| `npc_manager.py` | 基础 CRUD | 初始生成时填充 `attributes`/`relations`/`short_term_state` 的 JSON 子字段 |
| `world_manager.py` | 基础 CRUD | 初始生成时填充 `attributes` 的势力/法律/氛围子字段 |
