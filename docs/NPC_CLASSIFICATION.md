# NPC 分类与生命周期

## 三种 NPC

ANE 的 NPC 分为三个互斥的类型，生命周期和数据流向各不相同。

### 1. 重要 NPC（player-marked important）

| 属性 | 值 |
|------|-----|
| `is_important` | `True` |
| `npc_type` | —（无用，不判断） |
| 数据持久化 | DB，`long_term_state["model"]` 含完整 90+ 字段档案 |
| 位置追踪 | 每次 state_changes 中的 `npc_status` / `character_status` 更新位置 |
| Active Set | 按位置层级匹配纳入当前轮 |

**产生方式**（唯一入口）：

```
用户勾选 ⭐ + 输入中提及 NPC 姓名
  → POST /sessions/{id}/npc-modeling  （或 turn 管线中的 mark_important_npc 标记）
  → NPC Modeler LLM 生成完整人物档案
  → NPC.is_important = True, NPC.long_term_state["model"] = 完整模型
```

**Prompt 渲染**：
- 所有 `is_important=True` 的 NPC 进入 `[重要人物]` 块（全量 `model_data` 渲染）
- 第一轮 appearance 触发 `pending_debut` → 强制完整外貌描写

### 2. llmmain 自由生成的有名 NPC（offstage NPCs）

| 属性 | 值 |
|------|-----|
| `is_important` | `False` |
| `is_alive` | `True` |
| 数据持久化 | DB，基础字段（name / identity / cultivation / gender / location） |
| 关系网 | `NPC.relations` 中 `entries[].target = 玩家`，由 llmmain 的 `relationship_change` 维护 |
| Active Set | 按位置层级匹配纳入当前轮 |

**产生方式**（唯一入口）：

```
llmmain 输出中包含 offstage_npcs 数组
  → GameEngine Step 15 逐条写入 DB
  → 自动建立与玩家的初始关系
```

**offstage_npcs 输出条件**（System Prompt 中定义）：

> 本轮叙事中描写的、有明确姓名/身份/特征的非路人角色，但在正文中你没有透露其名字。
> 
> 输出条件（满足任意一条）：
> 1. 对玩家有实质影响 — 重要战斗、关键救治、抢夺/赠与重要物品或功法
> 2. 未完叙事线索 — 欠债、寻仇、约定、秘密、身世关联、宝物去向
> 3. 玩家主动点名 — 输入中明确提到姓名（即使未见过面）
> 4. 特殊身份 — 城主/峰主/宗主/圣子圣女；丹阵炼符医毒等特殊技艺持有者；情报网/黑市/商会负责人；榜上有名的人物
> 5. 特殊血脉/体质/传承/种族 — 先天灵体、特殊血脉觉醒者、异族、开了灵智的妖兽/器灵、能交流的非生物存在
> 6. 宿命关联 — 血缘知情人、灭门关联者、宿敌/宿命对手、因主角而命运改变的无辜者
> 7. 知道主角秘密的人 — 看到不该看的、受托保守秘密
> 
> 不输出：一次性路人、带路党、围观喊价的、跑腿小二、街头小贩、城门守卫、同路旅人、纯粹功能性NPC、炮灰、小弟

**关系网处理**：
- `offstage_npcs` 的 `relation` 和 `attitude` 字段建立 NPC→玩家的初始关系
- 后续 `relationship_change` state_changes → 通过 `bg_task_runner.run_relationship()` 异步写入 `NPC_Relationship` 表
- 任意 llmmain 输出的 `relationship_change` 都会触发一次后台关系处理

**Prompt 渲染**：
- 同位置时进入 `[重要人物]` 块（通过 `is_important` 过滤——非重要 NPC `is_important=False`，**不进入** `[重要人物]` 块）
- 当前轮次中按列表 `ctx.core_npcs`（即 `active_set.present_npcs`）全部传入 Prompt
- 但是 `_build_important_npcs_block()` 只渲染 `is_important=True` 的 NPC，所以非重要的 offstage NPC **不会以完整 detail 格式出现在 Prompt 中**

**⚠️ 已确认问题**：`_build_important_npcs_block()` 中 `is_important=True` 过滤意味着非重要的 offstage NPC 虽然有档案字段（age / gender / cultivation / identity / personality），但**不会被渲染到 Prompt 中**。它们的 `npc_to_context()` 数据仍然在 `ctx.core_npcs` 中，但没有渲染路径。建议方案：在 `_build_important_npcs_block()` 中增加"其他在场 NPC"的简略渲染（精简格式：姓名/性别/身份/修为）。

### 3. background NPC（一次性路人）

| 属性 | 值 |
|------|-----|
| 数据持久化 | **不入 DB** |
| 位置追踪 | 无 |
| Active Set | **不参与** |

**产生方式**（唯一入口）：

```
llmmain 输出中的 nearby_characters 数组（3 个，1 男 2 女）
  → OutputParser 提取
  → 存入 conversation 记录（【附近人物】JSON 前缀）
  → 前端渲染可点击卡片
```

**关键设计**（不回流的副产物）：

```
llmmain 输出
  ├── narrative          → 流入下一轮 Prompt（通过 shortmemory）
  ├── state_changes      → 写入 DB，间接影响后续轮
  ├── offstage_npcs      → 写入 DB，成为"有名 NPC"
  ├── nearby_characters  → 仅存 conversation，永不回流
  └── recommendations    → 仅存 conversation，前端展示
```

- `compact` 版本（llm_summary → shortmemory）**不包含** nearby_characters
- 下一轮 LLM **看不见**上一轮的路人
- 前端从 `conversation` 记录恢复历史时重新渲染卡片

---

## Active Set 构成

```
RetrievalEngine.get_active_set()
  → 查全部 NPC（DB 中 session 下所有 NPC）
  → 按玩家位置做层级匹配（精确匹配 + 分词交集）
  → 返回 List[NPC] (present_npcs) + location_context + related_absent
```

- **不再区分** `core_npcs` 和 `nearby_npcs`（原 `is_core` 列已删除）
- 所有 DB 中的 NPC 平等参与位置匹配
- `related_absent` 从 Fact 表反向查找：查 character 类别的 Fact → 提取 NPC 名称 → 排除已在 present 的 → 返回最多 5 个

---

## 关系网异步处理

```
llmmain 输出 relationship_change
  → GameEngine Step 15: 跳过（只记日志）
  → db.commit() 后:
    asyncio.ensure_future(bg_task_runner.run_relationship(
        session_id, state_changes, offstage_npcs,
        recently_modeled, user_id,
    ))
  → run_relationship() 处理:
    - 解析 relationship_change 中的 target/type/value
    - 写入 NPC_Relationship 表（source_npc → target_npc）
    - 处理 offstage NPC 的 attitude/relation
    - 新建模 NPC 的初识关系
```

---

## 变更历史

| 日期 | 变更 |
|------|------|
| 2026-07-28 | 删除 `is_core` 列及全部相关代码 |
| 2026-07-28 | 删除初始 NPC 批量生成逻辑 |
| 2026-07-28 | 删除 NPCManager._random_name() 和 npc_templates 依赖 |
| 2026-07-28 | 删除 WorldContext 块（不注入世界背景数据） |
| 2026-07-28 | NPCContext / PlayerContext 增加 gender 字段 |
