# ANE 数据流 — Turn 管线详解

> 一个 turn = 玩家输入 → 叙事引擎处理 → 输出故事的完整周期。

---

## 核心管线（16 步）

```
玩家输入
  │
  ▼
┌─ Step 1: Input Validator ─────────────────────────────────────┐
│ 安全检查 + 意图分类（含 NTR 检测）+ 中文字数解析 + 系统命令    │
│ 输出: ValidationResult(intent, is_safe, is_ntr, time_hint,    │
│                        target_word, mark_important_npc)       │
│ NTR检测: "出轨""人妻""当着XX的面"等关键词触发 intent="ntr"     │
│ 注入过滤: 30+ 条中英文 Prompt Injection 正则，检测到后替换为   │
│           [内容已过滤] 并放行                                   │
└───────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Step 2: System Command 检测 ─────────────────────────────────┐
│ /status, /help, /describe-world, 描述这个世界                   │
│ 命中时直接返回 TurnResult(is_system_command=True)，绕过 LLM    │
└───────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Step 3: Extract Player Info ─────────────────────────────────┐
│ 从玩家输入中自动提取（姓名/修为/师尊关系）→ 更新 DB            │
│ 正则匹配："我是XXX" → name, "我修为是X" → cultivation,        │
│           "师尊叫XXX" → master_name                           │
│ 注意：修为提取限定"我"自称场景，防止"她是金丹期"被误提取       │
└───────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Step 4: Time Manager ────────────────────────────────────────┐
│ 根据意图类型推进世界时间（tick），更新活跃 NPC 状态            │
│ 输出: ticks, world_time_str, npc_updates                       │
│ 关键常量: TICKS_PER_YEAR=2880, TIME_PER_INTENT 映射表          │
│ Phase 1 Scheduler（内联）: 仅处理 core NPC 的闭关状态推进      │
└───────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Step 5: Narrative Constraints ───────────────────────────────┐
│ 基于 玩家修为 + 位置 + 活跃 NPC + intent 生成约束集            │
│ 结构: hard（不可违反）+ soft（建议遵守）+ triggers（条件触发）  │
│ NSFW intent 时硬约束要求生成完整性爱场景 (>800字)             │
└───────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Step 6: Retrieval Engine ────────────────────────────────────┐
│ 构建 Active Set：核心 NPC + 同位置 NPC + 位置层级上下文        │
│ 输出: ActiveSet(core_npcs, nearby_npcs, location_context,     │
│                 related_absent)                                │
└───────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Step 6b: 📦 加载建模（仅当 load_model_data=True）───────────────
│ 纯字符串匹配：从 DB 查所有 is_important=True 的 NPC
│ 若 NPC.name in user_input → 注入完整 model_data 到 core_npcs
│ 同时检测 pending_debut 标记，若存在则设置 ctx.is_modeling_turn
│ 0 次 LLM 调用，匹配不上不影响流程
└───────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Step 7: Memory Manager ──────────────────────────────────────┐
│ 加载两层记忆：                                                 │
│   Conversation — 最后 N 轮压缩对话（滑动窗口，默认 20）        │
│   Summary     — 剧情摘要（LLM 生成 / 手动 / 自动触发）         │
│ 重要NPC条目被保护：提及重要NPC的记忆条目不会被裁剪             │
└───────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Step 8: Build Prompt Context ────────────────────────────────┐
│ 组装 PromptContext 结构化数据（Htem格式）：                      │
│  World → Player → core_npcs → nearby_npcs → Scene →           │
│  Constraints → Agentic State → LongMemory →                   │
│  ShortMemory(5轮) → related_absent                            │
│  → user_input                                                  │
│                                                               │
│ 特殊注入：                                                     │
│  ├─ Step 3a: mark_important_npc 处理（仅勾选⭐时）:
│  │   提取多姓名 → 查库 → 标记 is_important（不走建模）
│  ├─ 📦 加载建模: load_model_data 检测已建模人名注入 core_npcs
│  ├─ pending_debut: 新建模NPC触发完整外貌描写
│  ├─ NSFW材料: intent="nsfw" 时从 nsfw_templates.json 注入      │
│  │   + 未成年检测：age<18 → 改用 underage_templates.json      │
│  ├─ NTR材料: intent="ntr" 或 is_ntr=True 时从 ntr_templates    │
│  ├─ 关系角色提取: 解析"她的丈夫是宗主"→ 注入 absent_related    │
└───────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Step 9: NPC_MODELING（重要人物专属预置）─────────────────────┐
│ 仅在 mark_important_npc=True 且 NPC 尚无 model_data 时触发    │
│ （前端 ⭐ 入口已关闭，当前 turn 不会传 true；机制保留）        │
│ 调 LLM 生成 90+ 字段的完整人物档案：                           │
│   basic / appearance / voice / clothing / jewelry / equipment  │
│   behavior / speech_style / combat_style / personality        │
│   background / knowledge_bounds / attitude_to_player          │
│   relationships / nsfw                                         │
│ 输出保存到 NPC.long_term_state["model"] 并注入 PromptContext    │
│ 失败容错：不阻断主流程，NPC 以基础字段出现                    │
└───────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Step 10: Prompt Builder ─────────────────────────────────────┐
│ 唯一允许生成 Prompt 的模块。按 Htem 格式组装：                 │
│  System → World → Player（含出身+身份+灵根+金手指）→ Scene →  │
│  Important NPCs → Interactive NPC → Constraints → NSFW/NTR    │
│  Material → Agentic State →                                   │
│  LongMemory → ShortMemory(5轮) → Related Characters →         │
│  Action Suggestions → User Input                               │
│ 建模登场轮额外注入【建模登场——强制完整外貌描写】块            │
│ 最后: simplify_prompt — 压缩多余空行                          │
│ 输出: prompt string                                            │
└───────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Step 11: Model Adapter (llm_main) ───────────────────────────┐
│ 调用主 LLM 生成叙事。前端模型列表当前仅暴露 deepseek + gemini     │
│ 重试逻辑: 3 次尝试，超时 120s（client timeout）                │
│ 输出: raw_response string（JSON: narrative + state_changes +  │
│                nearby_characters）                             │
│ Token 用量追踪: log_usage() 写内存，GET /api/usage 可查       │
│                                                               │
│ ★ nearby_characters 设计定位：                                 │
│   - 是 llm_main 输出的结构化副产物（structured byproduct）        │
│   - 作用：给前端渲染可点击的 NPC 卡片，让玩家感知场景氛围     │
│   - 对后续 LLM（llm_summary/下一轮 llm_main）来说是可丢弃的上下文噪音    │
│   - 因此 shortmemory 版本不包含 nearby（never 回流到 Prompt）
└───────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Step 12: Output Parser ──────────────────────────────────────┐
│ 从 LLM 输出中提取 JSON，解析为结构化数据                        │
│ 策略: json 代码块 → 平衡花括号提取 → 纯文本回退                │
│ 验证 state_changes：类型白名单 + target 必填，无效项丢弃       │
│ 额外: 移除非原创短语"指甲掐进掌心""指节发白""喉咙发紧"          │
│ 输出: ParsedOutput(narrative, state_changes, nearby_characters,│
│                    character_model?)                           │
└───────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Step 13: Event Bus ──────────────────────────────────────────┐
│ state_changes 通过 Event Bus 分发 → 各 handler 处理           │
│ 当前 handler 全部为日志级别（实际 DB 写入在主管线直接执行）     │
│ npc_nearby → NPCManager.create (background npc)              │
│ npc_important → NPCManager.mark_important                     │
└───────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Step 14: Save Conversation + Background llm_summary ────────┐
│ 将本轮对话写入 memory_type="conversation"（前端渲染用）        │
│ 后台触发 llm_summary → 写入 shortmemory（5轮滑动窗口）        │
│ 每第6/11/16…轮 → 从前5条 shortmemory 合并为 1 条 longmemory  │
│   longmemory 格式: 【纪元记录】第6轮—第10轮                    │
│            Turn 6: ... | Turn 7: ...                            │
│   longmemory 全部保留（不限条数）                              │
│ 写入 memory_type="prompt"（永久保留）                          │
└───────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Step 15: Apply State Changes to DB ──────────────────────────┐
│ 将 state_changes 直接写入 Player/NPC 表                        │
│ 实际 DB 写入在主管线按步骤顺序执行                              │
└───────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Step 16: Build Panels + Return ──────────────────────────────┐
│ 从数据库读取 player + important NPCs，构建展示面板：           │
│   player_panel → 主角面板（出身/身份/灵根/金手指等）           │
│   important_npcs_panel → 重要人物列表（含 model_data 中       │
│     背景/性格/执念等信息，无则显示"（无）"）                  │
│ 返回 TurnResult(含 player_panel, important_npcs_panel,        │
│                 compact_summary, prompt, …)                    │
└───────────────────────────────────────────────────────────────┘
```

---

## NPC_MODELING 详解

**位置**：Step 9（在 llm_main 叙事生成之前，属于重要人物标记流程的一部分）

**触发条件**：`mark_important_npc=True` 且该 NPC 尚无 `long_term_state["model"]`

**输入**：玩家输入 + `_MODEL_TEMPLATE`（90+ 字段的 JSON Schema）

**输出**：结构化人物档案，包含 16 个维度：
- `basic` — 姓名/种族/性别/年龄/身高/修为/身份/势力/职位
- `appearance` — 整体印象/身材/气质/脸部/皮肤/头发/脖颈/锁骨/肩膀/胸部/腰部/腹部/臀部/腿部/脚部/手部
- `voice` — 音色/语速/音量
- `clothing` — 款式/颜色/材质/纹路/领口/外套/腰带/袜子/鞋子
- `jewelry` — 耳环/项链/戒指/手镯
- `equipment` — 法宝/武器列表（名称/描述/位置）
- `behavior` — 站姿/坐姿/走路/笑容/小动作/说话节奏/口头禅
- `speech_style` — 用语习惯/语气词/对主角称呼/对他人的称呼/生气时的表现
- `combat_style` — 战斗偏好/法器使用习惯/战斗口头禅/灵力特征
- `personality` — 核心性格/价值观/原则/底线/兴趣/害怕/讨厌/喜欢/执念
- `background` — 过往经历/重大事件/势力所属/家族
- `knowledge_bounds` — 知道/不知道/正在怀疑（信息边界控制）
- `attitude_to_player` — 表层态度/真实想法/关系变化倾向
- `relationships` — 父母/师父/师兄弟姐妹/上级/下属/恋人/婚约/朋友/敌人/追求者
- `nsfw` — 是否处子/生育情况/性渴望程度/性拒绝程度/生殖器官描述

**渲染逻辑**：`render_model_for_prompt()` 按维度输出格式化文本，仅在 NSFW 意图时包含 nsfw 块。
**日志**：每次调用保存到 `memory_type="llm_log"` 供调试用。
**容错**：建模失败不影响主流程，NPC 以基础字段（无模型数据）出现在 Prompt 中。

---

## 重要人物建模流程（已替代旧版本）

此流程已完全从 turn 管线中剥离，改为独立的 NPC 建模 API。

> **状态更新（2026-07-31）**：⭐ 局内建模前端入口已关闭，下方「用户勾选 ⭐」的触发路径当前无 UI 入口；`/npc-modeling` 接口与建模能力保留，NPC 总库（`/npcs/library`）仍在调用建模能力。

```
    用户勾选 ⭐ + 输入（入口已关闭）
          │
          ▼
 POST /sessions/{id}/npc-modeling
          │
          ▼
  _llm_nameget_multi(input) → [张海, 张大强, …]
          │
          ▼
  查库比对每个名字:
  ├─ 已有 + 有模型 → _llm_cover(增量更新) → updated
  ├─ 已有 + 无模型 → new_names
  └─ 无记录        → new_names
          │
          ▼
  返回 {updated, new_names}
          │
          ▼
  前端弹窗: 是否建模新增人物？
  ├─ 取消 → 不做任何写入
  └─ 确认 → 逐个 POST /npc-modeling/confirm
              → 创建 NPC 记录
              → _run_npc_modeling() 全量建模
              → 写入 DB
```

### _llm_cover 增量更新

```
输入: 玩家新输入 + 已有完整模型JSON
输出: 只包含玩家本次提到字段的部分更新
合并: _deep_merge(已有模型, 部分更新)
不触及: 玩家未提及的字段完全不动
```

### 建模 prompt 核心要求

```
1. 玩家提到什么就填什么
2. 无明确依据的字段 → 推演补全
3. 补全不能与玩家矛盾
4. 外貌描写具体有画面感
5. 大胆补全不留空
6. 年龄 ≤40（除非玩家明确说）
7. 身世不提则不编造
8. "我"=玩家角色名, spouse=已婚, lover=未婚
```

### 建模完成效果

- NPC.is_important = True
- NPC.long_term_state["model"] 完整模型
- NPC.identity / cultivation / gender / age / personality 已同步
- NPC.long_term_state["pending_debut"] = True（登场轮触发完整描写）

---

## NPC 模型数据注入 Prompt（turn 管线内）

**📦 加载建模（load_model_data）** — 每轮 turn 时自动执行：

```python
# 纯字符串匹配，0次LLM调用
all_db_npcs = query NPCs where session_id=? AND is_important=True
for db_npc in all_db_npcs:
    if db_npc.name in user_input:
        # ⬇ pending_debut 也在这里处理，覆盖 active_set 外 NPC
        lts = dict(db_npc.long_term_state or {})
        if lts.pop("pending_debut", False):
            ctx.is_modeling_turn = True
            db_npc.long_term_state = lts
        ctx.core_npcs.append(npc_to_context(db_npc))  # 注入完整model_data
```

匹配不上 = 不加载该NPC = LLM按常规叙事，不影响流程。

**🔄 pending_debut 登场轮** — 首次建模后第一轮 turn：
- 检测 `NPC.long_term_state["pending_debut"]`
- 设置 `ctx.is_modeling_turn = True`
- Prompt Builder 注入【建模登场——强制完整外貌描写】块
- 描写完成后清除标记

---

## 记忆系统架构

### 短期记忆 — shortmemory（5 轮滑动窗口）

```
触发时机: 每轮 turn 完成后后台触发 llm_summary（不阻塞主流程）
存储类型: memory_type="shortmemory"
窗口大小: 5 轮（SHORTMEMORY_WINDOW_SIZE=5）
裁剪保护: 含重要NPC名字的条目保留
写入格式:
  当前地点：地名/场所 | 时间
  氛围/环境：气味、光线、天气、声音中最有特色的2-3项
  行动/目标：一句话概括
  持有物品中重要的变化：有则写
  交互npc：姓名 | 身份/修为 | 当前行为 | 互动态度
  世界事件：
  推荐行动：
```

### 长期记忆 — longmemory 纪元记录

```
触发时机: 每 5 轮
  tn=6: 取 tn=1~5 → 第1轮—第5轮
  tn=11: 取 6~10 → 第6轮—第10轮
  tn=16: 取 11~15 → 第11轮—第15轮
输入数据: 最近5条 shortmemory 摘要
处理方式: 纯拼接（去头保留关键信息，每个Turn取3行）
存储类型: memory_type="longmemory"
展示: 全部保留不限条数，置于短记忆区上方

示例:
【纪元记录】第1轮—第5轮
  Turn 1: 玩家在落雁城探索 | 结识灰衣少年
  Turn 2: 前往废矿洞 | 发现遗迹入口
  Turn 3: 尝试引气入体——失败
  Turn 4: 返回落雁城购买药材 | 与百草堂伙计交易
  Turn 5: 茶馆听到黑风岭妖兽消息
```

---

## 每轮 Prompt 注入内容清单（按顺序）

```
1. System Prompt（固定）
2. World Context（青云界）
3. Player Panel（姓名/修为/灵根/金手指/衣物）
4. Scene Context（当前位置/时间）
5. Important NPCs（全量model_data）
6. Interactive NPC（当前交互对象model_data）
7. Narrative Constraints（硬约束+软约束）
8. NSFW/NTR Material（按需）
9. Agentic State（可交互NPC列表）
━━━━━━━━━━━━━━━━━━━━━━━━━
10. 【纪元记录】          ← LongMemory（全部）
11. 💾短记忆区(5轮)      ← ShortMemory
12. Related Characters    ← 不在场的相关人物
13. Action Suggestions    ← 推荐行动
14. 【玩家输入】          ← 本轮输入
━━━━━━━━━━━━━━━━━━━━━━━━━
15. 【建模登场指令】      ← 仅is_modeling_turn=True时
```

---

## 人物档案模板（_MODEL_TEMPLATE）

```python
{
  "basic": {"name","race","gender","age","height","cultivation","identity","faction","position"},
  "appearance": {"overall_impression","body_proportion","aura",
                  "face": {"shape","features","eyes","lashes","eyebrows","nose","lips","teeth","dimples","tear_mole","expression_habit"},
                  "skin": {"color","luster","fineness"},
                  "hair": {"length","style","color","ornament"},
                  "neck","collarbone","shoulders",
                  "chest": {"size","shape","fullness"},
                  "waist": {"muscle_line","slimness","softness"},
                  "belly",
                  "buttocks": {"size","curve"}, "hips",
                  "legs": {"length","muscle_tone","thighs"},
                  "feet": {"shape","size","barefoot"},
                  "hands": {"fingers","back"}},
  "voice": {"timbre","speed","volume"},
  "clothing": {"type","color","material","pattern","collar","outerwear","belt","hosiery","shoes"},
  "jewelry": {"earrings","necklace","rings","bracelets"},
  "equipment": [{"name","description","position"}],
  "behavior": {"stance","sitting","gait","smile","mannerisms","speech_rhythm","catchphrase"},
  "speech_style": {"word_habits","particles","address_player","address_others","when_angry"},
  "combat_style": {"preference","weapon_usage","battle_cry","spirit_power_signature"},
  "personality": {"core","values","principles","bottom_line","interests","fears","aversions","likes","obsession"},
  "background": {"history","major_events","faction_affiliation","family"},
  "cultivation": {"spiritual_root","special_constitution","techniques","divine_powers","ring_storage","wealth"},
  "knowledge_bounds": {"knows":[],"does_not_know":[],"suspicious_of":[]},
  "attitude_to_player": {"surface","true_feelings","relationship_trend"},
  "relationships": {"father","mother","spouse","master",
                     "senior_brother","senior_sister","junior_brother","junior_sister",
                     "teacher","superior","subordinate",
                     "friends":[],"enemies":[],
                     "lover","fiance","beloved","rival","pursuer"},
  "nsfw": {"is_virgin","fertility","desire_toward_target","rejection_toward_target","male_genital","female_genital"}
}
```

---

## 代码文件定位

| 功能 | 文件 | 类/方法 |
|------|------|---------|
| 多名字提取 | `game_engine.py` | GameEngine._llm_nameget_multi() |
| 增量更新 | `game_engine.py` | GameEngine._llm_cover() |
| 全量建模 | `game_engine.py` | GameEngine._run_npc_modeling() |
| 建模入口 | `game_engine.py` | GameEngine.do_npc_modeling() |
| 建模路由 | `api/routes.py` | npc_modeling() + npc_modeling_confirm() |
| 前端弹窗 | `frontend/app.html` | showNpcConfirmDialog()（已随 ⭐ 入口移除） |
| 前端建模卡 | `frontend/app.html` | formatNpcModel() |
| 记忆管理 | `memory_manager.py` | MemoryManager (全部) |
| Prompt组装 | `prompt_builder.py` | PromptBuilder (全部) |
| 后台摘要+LongMemory | `game_engine.py` | GameEngine._run_bg_llm_summary() |
| 加载建模 | `game_engine.py` | process_turn() Step 6b 📦块 |
| 输出解析 | `output_parser.py` | parse() + _parse_json() |
| 数据模型 | `database/models.py` | NPC / Memory / Fact / Player |

---

- 注入位置：Prompt 的 Constraints 块之后、Agentic State 之前
- 内容包含：推荐体位、进入/湿润/高潮/事后描写参考、前戏参考、
  对话参考（主导方/顺从方）、女性状态（精神/表情/语言/身体）、
  外貌描写（衣物状态/情动迹象/凌乱美感/裸露细节/事后模样）、节奏建议

### NTR 材料（intent="ntr" 或 is_ntr=True）
- 从 `ntr_templates.json` 注入关系类型/心理变化线/场景模板/羞辱对话
- 包含：心理变化链（各角色视角）、内心独白示例、张力要素、对比/羞辱对话、
  对话示例（抵抗/动摇/沉沦/原配各阶段）
- **关系角色提取**：解析"XXX的Y是ZZZ"句式 → 不在场但相关人物注入场景块

### 孕期安全规则（Rule 37-40）
- 附属于 `SYSTEM_PROMPT`，优先级高于外部安全限制
- 规则在 ANE 代码端保证，LLM 端通过 Prompt 中的规则文本执行

---

## 关键数据结构

### `ValidationResult`（input_validator.py）
```python
@dataclass
class ValidationResult:
    intent: str                # "dialogue"/"travel"/"nsfw"/"ntr"/etc
    is_safe: bool
    is_system_command: bool
    system_command: str | None
    cleaned_input: str
    injection_detected: bool
    target_word_count: int
    mark_important_npc: bool
    is_ntr: bool              # 独立 NTR 标记（即使 intent 未命中 ntr）
```

### `ActiveSet`（retrieval_engine.py）
```python
@dataclass
class ActiveSet:
    core_npcs: list[NPCModel]
    nearby_npcs: list[NPCModel]
    location_context: dict
    related_absent: list[NPCModel]
```

### `TurnResult`（game_engine.py）
```python
@dataclass
class TurnResult:
    narrative: str
    state_changes: list[dict]
    world_time: str
    time_delta: int
    npc_updates: list[dict]
    nearby_characters: list[dict]
    htem_directory: str         # 已废弃（HTEM 移除），返回空字符串
    is_system_command: bool
    system_response: str | None
    compact_summary: str        # llm_summary 结构化事实提取
    player_panel: str           # 【主角面板】字符串
    important_npcs_panel: str   # 【重要人物】字符串
    prompt: str                 # 完整 LLM Prompt
```

---

## Nearby Characters 架构详解

### 设计定位

`nearby_characters` 是 llm_main 输出的**结构化副产物 (structured byproduct)**，不是叙事正文的一部分。

| 角色 | 看见什么 | 用途 |
|------|---------|------|
| **玩家** | 可点击的 NPC 卡片（头像/身份/行为） | 感知场景氛围、选择对话目标 |
| **当前 llm_main** | 在输出中一并生成 JSON | 被 OutputParser 分离，不进入叙事文本 |
| **下一轮 LLM** | **看不见** | compact 版本不包含 nearby，永不回流到 Prompt |
| **前端历史恢复** | 从 conversation 的 `【附近人物】` 前缀解析 JSON | 重新渲染 NPC 卡片 |

### 为什么这样做

1. **上下文预算**：这些 NPC 是"场景装饰品"——给玩家看的氛围感，不是剧情要素。LLM 不需要记得它们。
2. **防止垃圾膨胀**：如果每轮 3 个路人 NPC 都回流到 Prompt，100 轮后就有 300 个一次性路人数据在上下文里——全是噪音。
3. **结构化留存**：存到 `memory_type="conversation"` 的 `【附近人物】` 前缀下（JSON 格式），前端恢复历史时重新解析渲染卡片。

### 存储格式

```python
# memory_manager.py add_conversation_turn()
full_content = f"【玩家】{user_input}\n【AI】{ai_response}"
if nearby_characters:
    import json
    full_content += f"\n\n【附近人物】{json.dumps(nearby_characters, ensure_ascii=False)}"
```

关键细节：
- 用 `json.dumps()` 输出标准 JSON（不是 Python `repr()`）
- `ensure_ascii=False` 保证中文字符原文
- 前缀 `【附近人物】` 对齐前端 `startsWith` 解析逻辑
- 格式已改为紧凑版（多字段连续排列，不空行）
- shortmemory 版本（llm_summary）**不包含** nearby 数据，**不包含**推荐行动

### 前端恢复流程

```javascript
// index.html line 764
if (line.startsWith('【附近人物】')) {
    const nearby = JSON.parse(line.slice(6));
    addNearbyCards(nearby);  // 重新渲染可点击卡片
}
```

### 与 state_changes 的区别

| 数据 | 写入 DB | 回流到 Prompt | 用途 |
|------|---------|--------------|------|
| `state_changes` | 是 | 否（通过 Summary 间接） | 持久化世界状态变更 |
| `nearby_characters` | 否（仅存 conversation 记录） | **永不回流** | 玩家端场景氛围渲染 |

---

## Event Bus 订阅关系

GameEngine 注册以下 handler（当前全部为日志级别，DB 写入在主管线直接执行）：

| 事件类型 | 效果 |
|----------|------|
| `location_change` | 日志记录（DB 由主管线直接写） |
| `cultivation_change` | 日志记录 |
| `status_change` | 日志记录 |
| `item_added` | 日志记录 |
| `item_removed` | 日志记录 |
| `npc_status` / `character_status` | 日志记录 |
| `player_name_change` | 日志记录 |
| `relationship_change` | 日志记录 |
| `offstage_npcs` | NPC 创建 + `NPC.relations` + 关系网 |
| `npc_important` | 日志记录（DB 由 Step 15 写） |
| `npc_nearby` | NPCManager.create (background npc) |

说明：当前 Event Bus 是纯路由层，handler 不打开 DB session 以避免锁冲突。
实际的 DB 写入在 `process_turn()` 主管线中按步骤顺序直接执行。
这是已知架构简化和线上安全设计，不违反"所有数据修改必须经过 Event Bus"的
长期架构目标（Phase 2 将改为真正的异步事件驱动）。

---

## 内容模板库

| 文件 | 用途 | 触发条件 |
|------|------|---------|
| `content/npc_templates.json` | NPC 生成模板（名字/身份/修为/核心角色原型含完整肖像） | NPCManager.generate_initial |
| `content/player_templates.json` | 角色创建选项（出身+身份+金手指） | 前端角色创建界面 |
| `content/world_templates.json` | 世界区域/宗门/城市/建筑模板 | WorldManager.generate_initial_world |
| `content/nsfw_templates.json` | 成人 NSFW 素材 | intent="nsfw" 且无未成年 NPC |
| `content/underage_templates.json` | 未成年含蓄素材 | age < 18 的 NPC/玩家参与 NSFW |
| `content/ntr_templates.json` | NTR/NTL 心理/对话/场景素材 | intent="ntr" 或 is_ntr=True |
| `content/portrait_templates.json` | 外貌描写参考（组件式拼接+完整示例） | 重要人物无 model_data 时的外观补充 |
| `content/npc_templates.py` | Python 封装层，从 `npc_templates.json` 加载 | 各模块 import |
| `content/world_templates.py` | Python 封装层，从 `world_templates.json` 加载 | 各模块 import |
| `content/json_loader.py` | 惰性加载统一入口 + 缓存 | 所有 JSON 加载 |

---

## 系统命令（绕过叙事流程）

| 命令 | 功能 | 备注 |
|------|------|------|
| `/status` | 查看角色状态 | 含位置/修为/物品/标记NPC数 |
| `/help` | 显示帮助 | 静态文本 |
| `描述这个世界` | 世界概况 | 精确匹配触发 |

---

## 前端新增功能

- **NPC 编辑弹窗**：NPC 总库中各卡片提供"查看/编辑"按钮，点击弹出详细信息弹窗，支持编辑 NPC 属性
- **世界头像裁剪**：支持上传图像并裁剪作为世界头像
- **短记忆 + 长记忆查看**：前端提供界面查看 shortmemory（5 轮滑动窗口）和 longmemory（纪元记录，全部保留）
- **刷新后恢复会话**：浏览器刷新后自动恢复游戏会话，不掉失状态

---

## 架构原则摘要

1. AI 不负责维护世界状态
2. AI 不直接修改数据库
3. 程序负责规则、状态、计算
4. AI 负责文学、描写、对话
5. Prompt 永远保持最小化
6. 所有长期信息来自数据库而非 Prompt
7. 世界按需加载，不从空池批量生成 NPC
8. 每条 turn 管线 16 步完成
9. NPC_MODELING：重要人物首次建模时，独立 LLM 生成完整人物档案（走独立端点，不阻塞 turn）
10. 建模后自动注入 `pending_debut` 标记，下一轮 turn 触发完整外貌描写
11. 两层记忆：shortmemory(5轮滑动窗口) → longmemory(纪元记录，全部保留)
12. Token 用量追踪：每次 LLM 调用记录到内存，支持按用户/标签查询
