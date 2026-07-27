# State Changes 写回闭环 — 2026-07-27 重构记录

> LLM 输出的状态变化现在真正写回 Player/NPC 数据库表，形成 DB → Prompt → LLM → state_changes → DB 的完整闭环。

---

## 背景

此前 Step 15 对 `state_changes` 的处理只写 Facts（历史日志），Player 表的 `cultivation`、`inventory`、`attributes` 等字段一旦创建就永远不变。LLM 每轮看到的都是创建时的旧数据，叙事和数据库脱节。

## 改动

### 1. Step 15 重写（`game_engine.py:563-652`）

删除所有自动写 Facts 的逻辑，改为直接写回 Player/NPC 表：

| state_change 类型 | 写回目标 |
|---|---|
| `cultivation_change` | `player.cultivation` |
| `location_change` | `player.location` |
| `item_added` | `player.inventory.append({name, description})` |
| `item_removed` | `player.inventory` 过滤匹配项 |
| `player_name_change` | `player.name` |
| `status_change {field}` | `player.attributes["field"]` |
| `status_change {field:"_extensions"}` | `json.loads` → `player.attributes["_extensions"]`（dict） |
| `npc_status / character_status` | `npc.cultivation / location / identity` |
| `economy_change` | `player.attributes["_savings_amount"]` 纯数字加减 |
| `relationship_change` | 由 `_run_bg_relationship` 异步处理（不变） |

### 2. 扩展系统 `_extensions`（`prompt_builder.py` + `game_engine.py`）

玩家/LLM 可以自由定义跟踪栏目（灵兽、任务进度、签到等），无需改代码：

- **写**：`status_change {target:"player", field:"_extensions", value:'{"栏目":"值"}'}`
- **读（Prompt）**：`extension: 栏目→值 / 栏目2→值2`
- **读（面板）**：`扩展：栏目→值 / 栏目2→值2`
- 子 dict 自动展平：`签到系统→已签到天数:3 | 连续签到:3`

### 3. 经济系统 `economy_change`（`game_engine.py` + `prompt_builder.py`）

纯数字存储，代码做加减法：

```json
{"type": "economy_change", "target": "player",
 "change": -3, "unit": "块下品灵石", "reason": "购买辟谷丹"}
```

- 存储：`_savings_amount`（int）+ `_savings_unit`（str）
- Prompt：`经济：每月固定收入1-2块下品灵石，现存款7块下品灵石。`
- 面板：`灵石：7块下品灵石`
- 旧字符串 `savings` 作为 fallback 保留

### 4. 玩家面板精简（`game_engine.py:684-747`）

- **去掉**：出身、月入、talent_note（修饰语）
- **新增**：位置、体质（有则显）、物品（非空时显）
- 性格加回
- 全部一行，` ｜ ` 分隔，CSS `white-space: pre-wrap` 自动折行

### 5. Prompt 文档更新（`prompt_builder.py:124-144`）

state_changes 各类型用法说明，含 LLM 可用的 field 列表。

---

## 涉及文件

| 文件 | 改动 |
|---|---|
| `backend/ane/game_engine.py` | Step 15 重写 + Step 16 面板精简 + 扩展+经济写回 |
| `backend/ane/modules/prompt_builder.py` | PlayerContext/NPCContext 加 `savings_amount/unit/extensions` 字段 + 渲染 + Prompt 文档 |
