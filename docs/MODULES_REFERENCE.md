# ANE 模块参考 — 14 个独立模块 (+ Auth 基础设施) 速查

> 全部单例。所有模块在 `game_engine.py` 中导入并通过 turn 管线编排。
> 模块间禁止直接耦合；交流通过 Event Bus + 方法调用（GameEngine 统一调度）。

---

## 总览

| 模块 | 文件 | 核心职责 | 被谁使用 |
|------|------|----------|----------|
| InputValidator | `input_validator.py` | 安全 + 意图 + NTR检测 + 字数 + 系统命令 | GameEngine |
| TimeManager | `time_manager.py` | tick 推进 + Phase 1 内联 Scheduler | GameEngine, API routes |
| NarrativeConstraints | `narrative_constraints.py` | 硬/软约束 + 条件触发 | GameEngine → PromptBuilder |
| RetrievalEngine | `retrieval_engine.py` | Active Set 构建 + related_absent 检索 | GameEngine |
| MemoryManager | `memory_manager.py` | 三层记忆（Conv/Summary/Facts）+ llm_summary 日志 + HTEM 缓存 | GameEngine, API routes |
| PromptBuilder | `prompt_builder.py` | 唯一生成 Prompt 的模块（Htem 格式） | GameEngine |
| ModelAdapter | `model_adapter.py` | 统一 LLM 调用接口（6 Provider + Ollama）+ Token 用量追踪 | GameEngine（_llm_nameget/llm_main/llm_summary/NPC_MODELING/summary） |
| OutputParser | `output_parser.py` | JSON 提取 + 校验 + 套话移除 + character_model 支持 | GameEngine |
| EventBus | `event_bus.py` | 内存 Pub/Sub 路由（当前 handler 为日志级别） | GameEngine |
| PlayerManager | `player_manager.py` | Player CRUD + 模板 + apply_character | GameEngine, API routes |
| NPCManager | `npc_manager.py` | NPC CRUD + 随机生成 + mark_important | GameEngine |
| WorldManager | `world_manager.py` | 世界区域 CRUD + 初始生成 + 位置层级上下文 | GameEngine |
| **NPC Modeler** | **`npc_modeler.py`** | **结构化人物档案建模：解析 LLM 输出 + 渲染模型到 Prompt** | **GameEngine** |
| JSONLoader | `content/json_loader.py` | 中文 JSON 文件惰性加载 + 缓存 | Player/NPC/World/NSFW 模板 |
| **Auth** | **`auth.py`** | **JWT 生成/验证 + 密码 pbkdf2_sha256 哈希 + get_current_user 依赖** | **API routes** |

---

## 各模块详情

### InputValidator (`modules/input_validator.py`)

```python
validate(input: str, mark_important_npc: bool = False) -> ValidationResult
extract_player_info(input: str) -> dict  # 从叙事输入中提取姓名/修为/师尊
```

- 安全过滤：暴力/违禁内容 + Prompt Injection 检测（30+ 条中英文正则）
- **注入策略：检测到后替换为 [内容已过滤]，不阻断**
- 意图分类：关键词匹配 + 正则混合（dialogue/travel/nsfw/ntr/combat… 等 11 种意图）
- **上下文降级系统**：当 NSFW/NTR 关键词出现于叙事/第三人称/被动承受语境时，降级回 `dialogue`
- **强行保留模式**：即使匹配降级规则，"干你/干她/干我/操死/当着XX的面"等保留原始意图
- **Cultivate 排除模式**：修炼关键词出现在教导/讲故事/讨论等被动语境时降级
- 中文字数解析：`"500字"` 或 `"五百字"` → `target_word_count=500`
- 系统命令检测：`/status`, `/facts`, `/addfact`, `/help`, `描述这个世界`
- `extract_player_info`：正则提取玩家自报的姓名/修为/师尊关系（修为限定"我"自称语境）
- 注入过滤：中文 + 英文 Prompt Injection 模式约 30 条正则

---

### TimeManager (`modules/time_manager.py`)

```python
calc_delta(intent: str) -> int       # 意图 → tick 数
format_world_time(epoch: int) -> str  # tick → "第X年·Y季节·Z时辰"
advance(db, session_id, intent) -> (ticks, world_time_str)
update_active_npcs(db, session_id, ticks) -> list[dict]
```

- 1 年 = 2880 ticks = 360 天 = 4 季节 × 90 天
- `TIME_PER_INTENT` 映射表在 `config.json`
- 格式化输出："第1年·春·清晨"
- **Phase 1 Scheduler（内联）**：
  - `update_active_npcs()` 只处理 core NPC
  - 闭关状态 NPC：累积 `seclusion_progress`，每 720 ticks 修为微涨
  - 空闲 NPC：超过 720 ticks 且有 20% 概率 → 随机事件
  - 短时间间隔：无变化

---

### NarrativeConstraints (`modules/narrative_constraints.py`)

```python
get_context_constraints(player_cultivation, player_location, active_npc_names) -> ConstraintSet
```

- **硬约束（Htem 格式）**：
  - 修为限制："筑基期修士不可能击败元婴期修士"
  - NPC 性格一致性
  - 位置限制（闭关 NPC 不能现身）
  - 世界观限定（无魔法/科技）
  - 凡人与修士比例
  - 状态继承规则
  - **"洗得发白"禁令**
- **软约束**：场景描述差异化、对话体现角色性格
- **条件触发**：NSFW 意图时注入性爱场景硬约束
- 所有约束以文言/修仙用语表述

---

### RetrievalEngine (`modules/retrieval_engine.py`)

```python
get_active_set(db, session_id, player_location) -> ActiveSet
```

- 加载 core_npcs（is_core=True）+ 同位置 NPC（层级匹配：`player_loc_parts & npc_loc_parts`）
- 位置层级上下文（从叶节点向上遍历父链，最多 5 层）
- 检索 related_absent（通过 character-category Facts 内容关联 NPC 名探索，最多 5 个）
- **不批量加载所有 NPC**，只加载与当前位置直接相关的

---

### MemoryManager (`modules/memory_manager.py`)

```python
# 三层记忆
add_conversation_turn(db, session_id, turn_number, user_input, ai_response, nearby_characters, prompt) -> str
get_conversation(db, session_id) -> list[Memory]
get_full_conversation(db, session_id) -> list[Memory]
get_prompts(db, session_id) -> list[Memory]

# Facts 管理
add_fact(db, session_id, content, category, priority)
get_facts(db, session_id) -> list[Fact]
remove_fact(db, fact_id)

# Summary
get_latest_summary(db, session_id) -> Memory | None
save_summary(db, session_id, turn_number, content)

# HTEM
get_htem_directory(db, session_id) -> str
save_htem_directory(db, session_id, text)
```

- `add_conversation_turn` 写入三种 `memory_type`：
  - `conversation` — 完整对话 + `【附近人物】` JSON 前缀存储，前端渲染用
  - `shortmemory` — llm_summary 压缩版（给下轮 Prompt 用），调用 `compact_narrative_with_llm()`
  - `prompt` — 完整 Prompt，**永久保留，永不裁剪**
- **`nearby_characters` 设计原则**：只存在 conversation 记录中供前端恢复卡片，**永不回流到后续 Prompt**。
  shortmemory 版本不包含 nearby——对 LLM 来说这些是"一次性垃圾数据"。
- 此外还有：`longmemory`（纪元记录）、`htem_directory`（HTEM 缓存）
- **重要NPC保护**：提及重要NPC的记忆条目不会被裁剪
- shortmemory / conversation 保留最近 20 轮，prompt 永久保留
- llm_summary 调用日志写入 `user_logs/llm_summary/年月.log`（容错：失败时回退到截取前 200 字）

---

### PromptBuilder (`modules/prompt_builder.py`)

```python
build(ctx: PromptContext) -> str
```

- 唯一允许生成 Prompt 的模块
- **已完整实现 Htem 格式**：System → World → Player → Scene → Important NPCs → Interactive NPC → Constraints → NSFW/NTR Material → Agentic State → Facts → Summary → Conversation → Related Characters → Action Suggestions → User Input
- 结构化 Context 类（包含 `WorldContext`, `PlayerContext`, `NPCContext`, `SceneContext`, `AgenticContext`）
- **`npc_to_context()`** — 将 NPC ORM 转为 NPCContext，自动展开 `long_term_state`/`short_term_state`/`relations`/`equipment`/`abilities`
- **`player_to_context()`** — 将 Player ORM 转为 PlayerContext
- 每个板块独立 `_build_*_block()` 方法
- **`nsfw_active`** 控制 NPC model NSFW 块注入（仅 NSFW 意图时包含）
- **`is_modeling_turn`** 标记触发【建模登场——强制完整外貌描写】块
- 建模登场轮：注入完整外貌描写的硬性指令
- 板块分隔符：`————————`（16 个全角破折号）
- `inject_cached_htem()` / `simplify_prompt()`：缓存 HTEM 注入 + 压缩多余空行

---

### ModelAdapter (`modules/model_adapter.py`)

```python
generate(prompt: str, model: str = DEFAULT_MODEL) -> str
```

- 统一 6 Provider（OpenAI / Anthropic / DeepSeek / SenseNova / Google Gemini / Ollama）
- 重试逻辑：3 次尝试，指数退避（1s→2s→4s），最大 30s
- 客户端超时 120s（OpenAI 兼容）或 300s（Ollama 本地）
- Provider 选择通过 `model` 参数格式识别：`"openai:gpt-4o"`, `"deepseek:deepseek-v4-flash"`, `"ollama:qwen2.5"`
- **Token 用量追踪**：每次调用记录 `TokenUsage`（provider/model/label/user_id/prompt_tokens/completion_tokens/elapsed），内存存储
- **`get_usage()` / `get_usage_summary()`**：按用户/标签查询用量
- **Anthropic 适配器**：支持 `thinking: {"type": "adaptive"}` + `prompt-caching-2024-07-31` beta
- **Gemini 适配器**：支持安全过滤检测，无候选时抛出详细信息
- 被 GameEngine 用于 **`_llm_nameget`（姓名提取）**、**NPC_MODELING（人物建模）**、**`llm_main`（叙事生成）**、**`llm_summary`（场景事实提取）**

---

### OutputParser (`modules/output_parser.py`)

```python
parse(raw_response: str) -> ParsedOutput
```

- JSON 提取策略：json 代码块 → 平衡花括号提取 → 纯文本回退
- **平衡花括号提取**：支持嵌套 JSON 对象/数组内的花括号（通过跳转字符串字面量实现）
- 验证 state_changes：类型必须在白名单内、必须有 target，无效项丢弃
- **套话移除**：自动删掉「指甲掐进掌心」「指节发白」「喉咙发紧」
- **`nearby_characters` 分离**：从 llm_main JSON 中提取后，以独立 JSON 数组返回给前端。
  不写入 shortmemory 版本，**阻止这些一次性数据回流到 LLM 上下文**。
- **`character_model` 字段**：`ParsedOutput` 支持但当前 llm_main 不输出（建模在 pre-llm_main 完成）
- 返回 ParsedOutput(narrative, state_changes, nearby_characters, character_model)

---

### EventBus (`modules/event_bus.py`)

```python
publish_state_changes(session_id: str, changes: list[dict])
```

- 内存 Pub/Sub，GameEngine 在 `_register_event_handlers()` 注册 handler
- 每个 state_change 类型对应一个 handler 列表
- **当前所有 handler 为日志级别**（实际 DB 写入在主管线直接执行）
- 被订阅的事件类型：location_change, cultivation_change, status_change, npc_status, character_status, item_added, item_removed, relationship_change, quest_accepted, quest_completed, player_name_change, npc_important, npc_nearby

### PlayerManager (`modules/player_manager.py`)

```python
create(db, session_id) -> Player
get_by_session(db, session_id) -> Player | None
apply_character(db, session_id, name, age, gender, background, cultivation, personality, identity, ...)
update_name(db, session_id, name)
update_cultivation(db, session_id, cultivation)
update_location(db, session_id, new_location)
update_status(db, session_id, status)
add_to_inventory(db, session_id, item)
get_templates() -> dict
```

- `apply_character()` 将角色完整设定写入 `Player.attributes` JSON 字段
- `attributes` 存储出身(`background`)、性别(`gender`)、身份(`identity`)、灵根(`spiritual_root`)、金手指(`golden_finger_*`) 等全部角色属性
- 自定义身份时 `identity="custom"`，身份描述写入 `identity_custom`
- 自定义金手指 `golden_finger_id="custom"`，描述写入 `golden_finger_custom`
- 角色初始随机位置从 `_START_LOCATIONS` 列表选取（12 个城市名）
- `get_templates()` 返回 `player_templates.json`（含 genders / backgrounds / cultivations / personalities / identities / golden_fingers）

---

### NPCManager (`modules/npc_manager.py`)

```python
create(db, session_id, **kwargs) -> NPC
get_by_session(db, session_id) -> list[NPC]
get_core(db, session_id) -> list[NPC]        # core NPCs
get_important(db, session_id) -> list[NPC]    # marked important
mark_important(db, npc_id) -> NPC | None
update_state(db, npc_id, key, value)
update_location(db, npc_id, location)
generate_initial_npcs(db, session_id, total=30, core_count=8) -> list[NPC]
_random_name(exclude: set) -> str
```

- `generate_initial_npcs()` 从 `npc_templates.json` 加载数据（通过 `npc_templates.py` 封装层）
  - 核心 NPC（8 个）：从 `CORE_ARCHETYPES` 原型池随机选取，每个原型包含完整 portrait（年龄/身高/体重/衣着/能力/关系等），写入 `long_term_state`/`equipment`/`abilities`/`relations`/`short_term_state`/`appearance`
  - 次要 NPC（22 个）：随机姓名 + 身份 + 修为 + 性格，lean portrait
  - `_random_name()`：姓氏池（30+）× 随机名池（男/女各 30+），检测去重
- `mark_important()` 同时设置 `is_important=True` 和 `is_core=True`

---

### NPC Modeler (`modules/npc_modeler.py`) **（新增模块）**

```python
parse_modeling_response(raw: str) -> dict | None
render_model_for_prompt(model: dict, include_nsfw: bool = False) -> str
```

- 触发时机：`mark_important_npc=True` 且该 NPC 尚无 `long_term_state["model"]`
- 输入：LLM 生成的 JSON 人物档案（90+ 字段，见 `_MODEL_TEMPLATE`）
- 验证：必须有 `basic.name`，否则返回 None
- 输出保存到 `NPC.long_term_state["model"]`
- `render_model_for_prompt()`：将 model 渲染为格式化文本块，按 16 个维度输出
- **完全替代了文档中的 HTEM Phase 2 方案**

---

### WorldManager (`modules/world_manager.py`)

```python
generate_initial_world(db, session_id) -> list[WorldRegion]
get_by_session(db, session_id) -> list[WorldRegion]
get_by_name(db, session_id, name) -> WorldRegion | None
get_children(db, parent_id) -> list[WorldRegion]
get_location_context(db, session_id, location_name) -> dict
create_region(db, session_id, name, region_type, description, parent_id, attributes) -> WorldRegion
```

- 从 `world_templates.json`（通过 `world_templates.py` 封装层）读取区域数据
- 生成顺序：REGIONS → SECTS → SETTLEMENTS → LOCATIONS（子引用父 ID）
- 每个区域含 `name`/`region_type`/`description`/`parent_id`/`attributes`
- `attributes` 含 `era_description`/`law_description`/`spiritual_rules`/`factions`/`atmosphere`
- `get_location_context()`：返回当前位置 + 父链（最多 5 层）

---

### JSONLoader (`content/json_loader.py`)

```python
load_json(filename: str) -> dict
npc_data() -> dict       # 惰性加载 npc_templates.json
world_data() -> dict     # 惰性加载 world_templates.json
nsfw_data() -> dict      # 惰性加载 nsfw_templates.json
underage_data() -> dict  # 惰性加载 underage_templates.json
ntr_data() -> dict       # 惰性加载 ntr_templates.json
portrait_data() -> dict  # 惰性加载 portrait_templates.json
```

- 所有 JSON 数据在 `content/` 目录下，首次访问时加载并缓存
- 没有异步加载——JSON 文件小，同步读取即可

---

## 角色创建数据流

```
前端弹窗
  → GET /sessions/__any__/templates → 取 player_templates.json
  → 用户填写出身/性别/身份/金手指等
  → POST /sessions/{id}/character → ApplyCharacterRequest
    → GameEngine.apply_character()
    → PlayerManager.apply_character()
      → 写 Player 表基础字段（name, cultivation）
      → 写 Player.attributes JSON（age, gender, background, identity, clothing,
        monthly_income, spiritual_root, talent_note, golden_finger_* 等）
  → 前端收到响应，触发地图生成（saveCurrentMap）
    → 构建世界简介文本（含出身/身份/金手指 + 宗门/城市列表）
    → POST /sessions/{id}/map → 存入 session.map_data + session.world_intro
```

---

## Auth 基础设施 (`auth.py`)

```python
hash_password(password: str) -> str                    # pbkdf2_sha256
verify_password(password: str, hash: str) -> bool
create_access_token(data: dict) -> str                 # JWT，有效期 7 天
decode_access_token(token: str) -> dict | None

# FastAPI 依赖注入
get_optional_user(credentials, db) -> User | None       # 可选认证
get_current_user(credentials, db) -> User               # 强制认证
```

- JWT 密钥来自 `config.json` 的 `secret_key`，可用 `ANE_SECRET_KEY` 环境变量覆盖
- 算法：HS256
- 所有 `/sessions/*` 路由通过 `Depends(get_current_user)` 强制认证
- 用户数据按 `user.id` 隔离

---

## GameEngine 管线（`game_engine.py`）

完整的 17 步 turn 处理管线，详见 `DATA_FLOW.md`。

关键点：
```python
class GameEngine:
    # 会话生命周期
    create_session(db, user_id, name) -> dict
    apply_character(db, session_id, name, age, gender, background, cultivation, personality, identity, ...)

    # Turn 处理（17 步管线）
    process_turn(db, session_id, user_input, turn_number, model, mark_important_npc) -> TurnResult

    # _llm_nameget 调用（在 process_turn 内部，mark_important_npc=True 时）
    #   → _llm_nameget(user_input) → 提取/生成 NPC 姓名

    # NPC_MODELING 调用（在 process_turn 内部，pre-llm_main）
    #   → LLM 生成 90+ 字段人物档案

    # llm_summary 调用（在 process_turn 内部）
    #   → memory_manager.add_conversation_turn()
    #     → compact_narrative_with_llm(narrative, user_input, session_id, turn_number)
    # 失败不影响主流程，日志写入 user_logs/llm_summary/
```

### `TurnResult` 字段：
```python
@dataclass
class TurnResult:
    narrative: str               # 主 LLM 输出正文
    state_changes: list[dict]    # 状态变更
    world_time: str              # 当前世界时间
    time_delta: int              # 本轮推进的 tick 数
    npc_updates: list[dict]      # NPC 状态更新
    nearby_characters: list[dict]# 附近人物
    htem_directory: str          # 已废弃（HTEM 移除），返回空字符串
    is_system_command: bool
    system_response: str | None
    shortmemory_summary: str      # llm_summary 输出（结构化场景事实）
    player_panel: str            # 主角面板文本
    important_npcs_panel: str    # 重要人物面板文本
    prompt: str                  # 发送给主 LLM 的完整 Prompt
```
