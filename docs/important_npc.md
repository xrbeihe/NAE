# 局内建模标记系统（完整 LLM 链路）

## 总览

局内建模系统由**互斥操作**按钮 + 快捷建模按钮（🧑/👩）+ 三层记忆架构组成：

```
前端 UI（互斥，勾一个自动取消另一个）:
  [ ] ⭐ 局内建模     → 创建/更新人物档案（走独立 /npc-modeling 端点）
  [✓] 📦 加载建模     → 检测输入中的已建模人名，自动注入完整数据到 Prompt
                       默认勾选，勾⭐时自动取消，勾📦时自动取消⭐

快捷建模按钮（推荐行动栏右侧）:
  🧑 → 用当前输入框内容构建男性角色（自动注入「（男性）」前缀）
  👩 → 用当前输入框内容构建女性角色（自动注入「（女性）」前缀）
  流程同 ⭐，但自动确定性别方向，省去用户写「她/他」的麻烦

信息查看按钮:
  ！→ 局内建模构建说明

NPC 编辑弹窗（前端 NPC 总库表）:
  每行 NPC 右侧有「查看」和「编辑」按钮
  - 查看：只读展示该 NPC 的完整 model_data（含身份、修为、外貌、关系等）
  - 编辑：可修改该 NPC 的 name / identity / cultivation / gender / age / personality / tags
  - 确认后提交到 `POST /npc-modeling/{npc_id}/edit` 更新 DB

后端记忆:
  shortmemory ×5 → 短期记忆（最近5轮摘要）
  longmemory  ∞  → 长期记忆（纪元记录，全部保留）
  facts    ∞  → 永久记忆（关系/成就/位置，按优先级排列）
```

---

## 完整处理链路

### 注意事项

- ⭐ 和 📦 互斥，前端勾选一个自动取消另一个
- 🧑/👩 快捷建模按钮自动注入性别前缀（（男性）/（女性）），不受互斥影响
- 勾选 ⭐ 时：不走 turn 管线，走 /npc-modeling → 建模完成后 return
- 勾选 📦 时（默认）：正常发 turn，检测输入中的已建模名字注入 Prompt
- 两个都不勾：正常发 turn，不注入额外模型数据

### Step A: 用户输入 + ⭐ 勾选

```
POST /sessions/{id}/npc-modeling
{input: "张海是我妈，张大强是我爸"}

→ _llm_nameget_multi(input)
  调用 LLM 提取所有中文姓名
  返回 ["张海", "张大强"]
```

### Step B: 后端比对

```
for name in names:
    查库 NPC.session_id=? AND name=?

    有记录 + is_important + 有 model_version
      → _llm_cover(name, input, existing_model)
        只更新玩家这次提到的字段，不编造未提及内容
        返回 partial updates → _deep_merge 合入

    有记录 + is_important + 无 model_version（孤儿记录）
      → new_names（弹窗确认）

    无记录
      → new_names（弹窗确认，此时不创建 DB 记录）
```

### Step C: 返回前端

```json
{
  "updated": [
    {"npc_name": "张海", "model_data": {...}}    // llm_cover 增量更新
  ],
  "new_names": ["张大强"]                          // 待确认的新名字
}
```

### Step D: 前端弹窗（仅当 new_names 非空时）

```
┌─────────────────────────────┐
│ 👤 检测到新人物              │
│                             │
│ 以下人物不在数据库中，       │
│ 是否建模？                  │
│                             │
│ ☑ 张海（已勾选）             │
│ ☑ 张大强（已勾选）          │
│                             │
│  [全部跳过]    [确认建模]    │
└─────────────────────────────┘
```

### Step E: 确认后全量建模

```
POST /sessions/{id}/npc-modeling/confirm
{input: "张海是我妈，张大强是我爸", name: "张大强"}

→ 创建 NPC 记录（此时才写 DB）
→ _run_npc_modeling()
  prompt 包含:
    - 玩家当前角色名（解决"我"指代问题）
    - 年龄约束（≤40岁，除非玩家明确说）
    - 身世约束（玩家不提则不编造）
    - 关系理解（spouse=已婚 vs lover=未婚）
    - 90+ 字段 JSON Schema
→ LLM 返回完整模型
→ 写入 NPC.long_term_state["model"]
→ 写 pending_debut=True 标记
→ 同步到 NPC.identity / cultivation / gender / age / personality
```

---

## turn 管线中的 📦 加载建模

每次发 turn 时自动执行（仅在未勾选 ⭐ 时生效，因为 ⭐ 会在前端阻止 turn 发送），**0 次 LLM 调用**，且会在 load_model_data 块内同时处理 `pending_debut` 标记（覆盖不在 active_set 中、但被用户输入匹配到的已建模 NPC）：

```python
# 纯字符串匹配
all_important_npcs = query DB where session_id=? AND is_important=True
for db_npc in all_important_npcs:
    if db_npc.name in user_input:              # ← Python 字符串包含判断
        lts = dict(db_npc.long_term_state or {})
        # pending_debut 在此处理（覆盖 active_set 之外注入的 NPC）
        if lts.pop("pending_debut", False):
            ctx.is_modeling_turn = True
            db_npc.long_term_state = lts
        ctx.core_npcs.append(npc_to_context(db_npc))  # 注入完整 model_data
```

匹配不上 = 这轮不加载该 NPC 的精确数据 = LLM 按常规叙事，不会崩溃。

---

## 记忆系统架构

### 短期记忆 — shortmemory（5 轮滑动窗口）

```
每轮后台 llm_summary 自动生成（不阻塞主流程）
写入 memory_type="shortmemory"
窗口: 最近 5 轮
内容: 地点/时间/氛围/行动/交互NPC/世界事件/推荐行动
裁剪: 保护含重要NPC名字的条目不裁剪
```

### 长期记忆 — longmemory 纪元记录（每 5 轮生成一条）

```
触发: 第 6、11、16、21……轮（(tn-1) % 5 == 0）
输入: 最近 5 条 shortmemory 摘要
处理: 纯拼接（去头保留关键信息，每个 Turn 取 3 行）
输出: 【纪元记录】第1轮—第5轮
      Turn 6: 玩家在落雁城探索 | 结识灰衣少年
      Turn 7: 前往废矿洞 | 发现遗迹入口
      Turn 8: 尝试引气入体——失败
      Turn 9: 返回落雁城购买药材
      Turn 10: 在茶馆听到黑风岭妖兽消息
存储: memory_type="longmemory"
展示: 全部保留，置于【短记忆区】上方
```

### 永久记忆 — Facts（只增不减）

```
自动写入（由 state_changes 中的事件触发）:

  npc_important           → character(priority=10)
  relationship_change     → relationship(priority=10)  ← 永不裁剪
  cultivation_change      → achievement(priority=8)
  location_change(玩家)   → travel(priority=6)
  quest_accepted/completed→ achievement(priority=9)
  marriage                → relationship(priority=10)  ← 永不裁剪
  death                   → achievement(priority=10)
  breakthrough            → achievement(priority=10)

展示: Prompt 中分为【人物关系（永久）】和【世界记录】两个区块
      关系 Facts 永远排在前面
```

---

## 建模 Prompt 核心要求

```
1. 玩家提到什么就填什么，不忽略任何信息
2. 没有明确依据的字段，根据身份/修为/背景推演补全
3. 补全内容不能与玩家明确说的事实矛盾
4. 外貌描写要具体有画面感
5. 大胆补全不留空——玩家不满意后续可修改
6. 年龄 ≤40 岁（除非玩家明确给出年龄）
   修为→年龄参考：筑基以下≤25, 金丹30-80(外貌20+), 元婴50-200(外貌30+)
7. 身世背景: 玩家不提则 history 留空
8. 关系理解: "我"=玩家角色名
   spouse=已婚夫妻, lover=恋爱中未婚
   弟子/师兄/师尊/道侣 按亲属称谓正确填写
```

---

## 数据库隔离

| 层 | 隔离机制 |
|----|---------|
| 用户 | JWT 认证，每个用户只能操作自己的 session |
| Session | `_get_users_session()` 校验 `session.user_id == user.id` |
| 表 | 所有业务表通过 `session_id FK` 关联到 sessions 表 |
| 数据 | 不同用户的 NPC/记忆/Facts 天然不可见 |

---

## 代码位置

| 模块 | 文件 | 关键方法 |
|------|------|---------|
| 多名字提取 | `game_engine.py` | `_llm_nameget_multi()` |
| 建模入口 | `game_engine.py` | `do_npc_modeling()` |
| 增量更新 | `game_engine.py` | `_llm_cover()` |
| 全量建模 | `game_engine.py` | `_run_npc_modeling()` |
| 建模 prompt | `game_engine.py` | `_run_npc_modeling()` 内联 |
| turn 管线 | `game_engine.py` | `process_turn()` |
| 后台摘要 | `game_engine.py` | `_run_bg_llm_summary()`（含 longmemory 生成） |
| 加载建模 | `game_engine.py` | `process_turn()` Step 8 📦 块 |
| 自动 Facts | `game_engine.py` | `process_turn()` Step 15 |
| 记忆管理 | `memory_manager.py` | `add_summary_entry() / add_longmemory_entry() / add_fact()` |
| Prompt 展示 | `prompt_builder.py` | `_build_important_npcs_block() / _build_conversation_block() / _build_facts_block()` |
| 路由 + 确认 | `api/routes.py` | `npc_modeling() / npc_modeling_confirm()` |
| 请求 Schema | `api/schemas.py` | `NpcModelingRequest / NpcModelingResponse / NpcModelingConfirmRequest` |
| 前端弹窗 | `frontend/app.html` | `showNpcConfirmDialog() / formatNpcModel()` |
| 快捷建模 | `frontend/app.html` | `startModeling(gender)` → 🧑/👩 |
| 记忆面板 | （💭 按钮已关闭，暂时不可用） |
| 面板 Schema | `api/schemas.py` | `MemoryPanelEntry / MemoryPanelResponse` |
