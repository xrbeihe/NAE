# ANE API 端点速查

> 基础路径：`http://127.0.0.1:8002`
> 认证方式：JWT Bearer Token（`Authorization: Bearer <token>`）
> 除 `/auth/register`、`/auth/login`、`/api/health`、`GET /worldviews` 外，所有端点需要登录（世界观管理端点为可选认证）。
> 端口以 `config.json` + `.env` 的 `ANE_PORT` 为准（CI 部署固定 8002）。

---

## 认证

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| POST | `/auth/register` | `RegisterRequest` | `AuthResponse` (201) | 注册新账号 |
| POST | `/auth/login` | `LoginRequest` | `AuthResponse` | 登录获取 JWT token |
| GET | `/auth/me` | — | `AuthResponse` | 获取当前用户信息（需登录） |

### `AuthResponse`
```json
{
  "token": "eyJhbG...",
  "user_id": "e21191ce9f36",
  "username": "admin",
  "display_name": "管理员"
}
```
Token 有效期 7 天。前端存储在 `localStorage`，每次请求自动附加。

---

## Session 管理

所有端点要求 `Authorization: Bearer <token>`，且数据按用户隔离（每个用户只能看到/操作自己的 session）。

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| POST | `/sessions` | `CreateSessionRequest` | `CreateSessionResponse` (201) | 创建新世界会话 |
| GET | `/sessions` | — | `list[SessionSummary]` | 列出所有会话（按创建时间倒序） |
| GET | `/sessions/{session_id}` | — | `SessionSummary` | 获取会话详情（含完整对话历史 + 所有 prompts） |
| DELETE | `/sessions/{session_id}` | — | `DeleteSessionResponse` | 删除会话及所有关联数据（级联删除） |
| POST | `/sessions/{session_id}/abandon` | — | `{ok}` | 标记会话为废弃（zero-turn 会话，用户关标签页时发） |

### `POST /sessions` — 创建时会做：
1. 创建 `WorldSession` 记录（含 `worldview` 指定世界观包，默认 xianxia_v1）
2. 按世界观包生成世界区域（`world_templates.json` 的 regions/sects+settlements）
3. 创建玩家 stub（默认名/出生地按世界观包 `player_defaults` + 地理）
4. 返回 session_id + 初始世界状态

请求体（`CreateSessionRequest`）：
```json
{"name": "未命名世界", "worldview": "xianxia_v1"}
```
- `worldview`：可选，`^[a-z0-9_]{1,48}$`，无效或不存在返回 400 + 可用列表

### `GET /sessions/{session_id}` — 返回的额外数据
- `conversation`: 完整对话历史（`[{turn_number, content}]`，格式为【玩家】xxx\n【AI】yyy\n【附近人物】[...]）
- `npc_names`: 核心 NPC 的姓名列表
- `world_intro`: 世界简介文本（角色创建时生成，刷新后仍可查看）
- `prompts`: 全量 Prompt 历史（`[{turn_number, content}]`，按 `memory_type="prompt"` 永久保留）
- `player_location`: 玩家当前位置

---

## Turn 处理

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| POST | `/sessions/{session_id}/turn` | `TurnRequest` | `TurnResponse` | 处理一轮玩家操作 |

### `TurnRequest`
```json
{
  "input": "玩家输入（最长 4000 字）",
  "model": null,                     // 可选，模型 ID 如 "deepseek:deepseek-v4-flash"
  "mark_important_npc": false        // ⭐ 重要人物标记（前端 ⭐ 局内建模入口已关闭，默认不再传 true）
}
```

### `TurnResponse`
```json
{
  "narrative": "故事正文",
  "state_changes": [{"type": "...", "target": "...", "field": "...", "value": "..."}],
  "world_time": "第1年·春·清晨",
  "time_delta": 2,
  "npc_updates": [],
  "nearby_characters": [],
  "htem_directory": "",
  "is_system_command": false,
  "system_response": null,
  "prompt": "完整 LLM Prompt（调试用）",
  "shortmemory_summary": "llm_summary 结构化事实提取内容",
  "player_panel": "【主角面板】\n姓名：陆星河 ｜ 男 ｜ 14岁\n出身：家族旁支\n…",
  "important_npcs_panel": "【重要人物】\n（无）"
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| `nearby_characters` | llm_main 输出的结构化副产物——3 个场景路人 NPC，前端渲染可点击卡片。**不会回流到后续 LLM Prompt**（shortmemory 版本不含此数据）。详见 DATA_FLOW.md 的 Nearby Characters 架构详解 |
| `shortmemory_summary` | llm_summary 提取的短期记忆（场景事实），格式已改为紧凑单列版，包含当前地点/氛围/行动/物品/交互NPC/世界事件/推荐行动 |
| `player_panel` | 当前主角面板文本（出身/身份/灵根/金手指等），直接在前端系统消息中展示 |
| `important_npcs_panel` | 已标记为重要的 NPC 列表（含 model_data 中的背景/性格/执念），无则显示"（无）" |
| `recommendations` | 推荐行动列表（最多 10 条），前端显示在输入区上方推荐栏 |

---

## NPC 建模

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| POST | `/sessions/{session_id}/npc-modeling` | `NpcModelingRequest` | `NpcModelingResponse` | 提取输入中的所有人名，比对库，对有模型的NPC增量更新，返回新名字列表。**前端 ⭐ 局内建模入口已关闭**，本接口当前无 UI 触发（仍保留） |
| POST | `/sessions/{session_id}/npc-modeling/confirm` | `NpcModelingConfirmRequest` | `NpcModelingConfirmResponse` | 确认后对单个新NPC创建全量建模。同上，前端无入口 |

### `NpcModelingRequest`
```json
{"input": "张海是我妈，张大强是我爸"}
```

### `NpcModelingResponse`
```json
{
  "updated": [{"npc_name": "张海", "model_data": {...}}],
  "new_names": ["张大强"]
}
```

### `NpcModelingConfirmRequest`
```json
{"input": "张海是我妈，张大强是我爸", "name": "张大强"}
```

### `NpcModelingConfirmResponse`
```json
{"npc_name": "张大强", "model_data": {...}}
```

---

## 记忆面板

| 方法 | 路径 | 响应 | 说明 |
|------|------|------|------|
| GET | `/sessions/{session_id}/summaries?from_turn=N` | `SummariesResponse` | 获取最近 3 条 shortmemory 摘要（📕 悬浮窗用） |
| GET | `/sessions/{session_id}/memories` | `MemoryResponse` | 返回 shortmemory 列表 + longmemory 列表（📘 弹窗用） |

### `MemoryResponse`
```json
{
  "short": [{"turn_number": 6, "content": "..."}],
  "long": [{"turn_number": 11, "content": "..."}]
}
```

---

## 关系网

| 方法 | 路径 | 响应 | 说明 |
|------|------|------|------|
| GET | `/sessions/{session_id}/relationship-graph` | `{session_id, edges[]}` | 获取关系网图数据（🚻 弹窗用） |

返回 `edges[]` 每个元素：`{source, target, type, description, affinity}`。

---

## 角色创建

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| POST | `/sessions/{session_id}/character` | `ApplyCharacterRequest` | 玩家详情（含金手指信息） | 应用玩家选择的角色设定 |
| GET | `/sessions/{session_id}/templates` | — | 模板数据 | 获取角色创建可选列表 |

### `ApplyCharacterRequest`
```json
{
  "name": "林逸",             // 玩家姓名，必填（form 路径可省略，由 fields 提供），最长 20 字
  "age": 19,                  // 12-999，默认 19
  "gender": "男",             // 男/女
  "background": "家族旁支",   // 出身背景
  "cultivation": "凡人",       // 修为/能力等级
  "personality": "谨慎隐忍",  // 性格选择
  "identity": "外门弟子",      // 身份
  "golden_finger_id": "heavenly_book",
  "golden_finger_custom": "",
  "identity_custom": "",
  "fields": {}                 // 可选：世界观有 form.json 时，前端发送此扁平 map（{field_key: value}）
}
```

**双路径**：
- **旧路径**：显式传 name/age/gender/... 顶层字段
- **form 路径**：世界观包带 `form.json` 时，前端收集表单为 `fields` map 提交；后端按 form spec 通用写入（`store` 决定写 player 列或 attributes，`derive` 联动选项字段，`option_map` 映射卡片）。两种路径并存，form 优先。

角色创建响应包含金手指信息：
```json
{
  "session_id": "...",
  "player_name": "林逸",
  "cultivation": "凡人",
  "identity": "外门弟子",
  "golden_finger_name": "天书推演",
  "golden_finger_tagline": "怀中的古书微微发热…"
}
```

### 角色创建模板数据（`GET /sessions/{session_id}/templates?worldview=可选`）

返回世界观包的 `player_templates.json` + 附加字段，前端渲染角色创建弹窗：
- `genders` / `backgrounds` / `cultivations` / `personalities` / `identities` / `golden_fingers` — 选项数据（按世界观）
- `ui` — 前端文案（labels/create_button/modal_title/initial_recommendations）
- `player_defaults` — 默认名/能力/存款单位
- `form` — form.json（声明式表单 spec，可能为 null → 前端回退 legacy 表单）
- `world_templates` — 世界地理（供 has_sects/has_golden_fingers 显隐判断）
- `npc_templates` — NPC 提示库数据（姓名池/原型/quick-pick，供「新建建模NPC」弹窗）
- `modeler_schema` — 包内 `modeler/schema.json`（NPC 建模字段树，驱动编辑弹窗动态渲染）

`?worldview=` 查询参数指定包；`__any__` 会话 id 时用该参数，否则优先取会话绑定的世界观。

---

## 世界观管理（作者工具链）

`/worldviews/*` 端点管理世界观包（设计器 designer.html 调用）。GET 列表/校验可匿名，写操作需登录。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/worldviews` | 列出已安装世界观（id/name/description/version/tags） |
| GET | `/worldviews/{id}/validate` | 校验包，返回 `{ok, errors[], warnings[]}`（缺文件/坏 JSON/语义检查） |
| POST | `/worldviews/{id}/reload` | 清该包 loader 缓存（改文件后调用） |
| POST | `/worldviews/generate` | 填短表单生成完整包 zip（见下） |
| POST | `/worldviews/upload` | 上传 zip 安装新包（自动校验，默认包 xianxia_v1 受保护） |
| DELETE | `/worldviews/{id}` | 删除包（默认包受保护） |
| GET/PUT | `/worldviews/{id}/form` | 读写 form.json（声明式角色表单） |
| GET/PUT | `/worldviews/{id}/ui` | 读写 ui.json（按钮/标题/称呼/初始推荐） |
| GET/PUT | `/worldviews/{id}/data/{file}` | 读写白名单 JSON 工件（player/world/npc_templates、constraints、events、world_facts 等） |

### 开源世界观共享（v1.2）

`/worldviews/*` 下的开源共享端点（所有登录用户可推送/评分/使用，前端：designer「📤 开源」+ 主页面「🌐 开源世界观广场」）。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/worldviews/share` | 推送已安装包到共享库，body `{worldview_id, title?, description?, tags?: []}` |
| DELETE | `/worldviews/share?worldview_id=` | 撤销自己的开源（仅作者本人） |
| GET | `/worldviews/shared` | 列出所有开源包（含 avg_rating / rating_count / installed / mine / my_rating / author） |
| POST | `/worldviews/shared/{id}/rate` | 评分 1-5，body `{rating}`（同用户重复评覆盖） |
| POST | `/worldviews/shared/{id}/install` | 安装到本机（确认包在磁盘 + 重载 loader） |

### `POST /worldviews/generate` 请求体
```json
{
  "id": "my_world", "name": "我的世界", "description": "一句话设定",
  "genre": "fantasy",              // fantasy / modern / scifi / xianxia
  "power_name": "工艺等级", "money_name": "金币", "role_label": "旅人",
  "professions": "发明家、机械师", "places": "齿轮工坊、蒸汽广场",
  "create_button": "踏入蒸汽城",
  "world_setting": "长描述（可选）", "era": "时代背景（可选）",
  "factions": "势力（可选）", "taboos": "禁忌（可选）",
  "npc_names": "姓氏池（可选）", "golden_fingers": "特殊能力（可选）",
  "event_theme": "事件主题（可选）",
  "ip_based": true, "ip_work": "火影忍者"   // IP 世界观选项
}
```
返回完整世界观包 zip（11-12 个文件）。生成器自动拼接通用叙事内核（shell+kernel），作者无需写通用 prompt。

### 世界观包工件
每包 `backend/ane/worldviews/<id>/` 包含：manifest.json / system_prompt.txt / intent_keywords.json / constraints.json / world_templates.json / player_templates.json / npc_templates.json / panel.json / ui.json / events.json / form.json / modeler/role.txt / modeler/age_rules.txt / world_facts.json（IP 可选）。完整规范见 `docs/WORLDVIEW_PACK_SPEC.md`。

---

**宗门过滤规则**：以 圣地/宗/门/宫/阁/殿/谷/观/派 结尾，且通过 NSFW 关键词检测。
**城市过滤规则**：以"城"结尾，且通过 NSFW 关键词检测。
**NSFW 过滤关键词**：魔法少女、精灵、矮人、兽人、触手、便利店等非修仙词汇。

---

## 陪伴对话（1v1，v1.3）

独立于世界管线 `/sessions/*` 的 1v1 虚拟角色陪伴对话（前缀 `/chat`）。数据由 `companion_engine` 驱动，会话用 `worldview="companion_v1"` 标记，不生成世界区域。

| 方法 | 路径 | 请求体 | 说明 |
|------|------|--------|------|
| GET | `/chat/characters` | — | 可选的陪伴角色：UserNPC 总库 + UserCard 角色卡合并列表（`source: npc/card`，卡片带 `initial_relationship`/`clinginess`） |
| POST | `/chat/sessions` | `{npc_id?`, `card_id?`, `name?}` | 开启 1v1 会话（card_id 或 npc_id 二选一） |
| GET | `/chat/sessions` | — | 列出当前用户 1v1 会话 |
| GET | `/chat/sessions/{id}` | — | 完整对话历史 |
| GET | `/chat/sessions/{id}/memories` | — | 关系记忆（「TA 记得什么」面板） |
| GET/PUT | `/chat/sessions/{id}/nudge-settings` | `{idle_seconds}` | 主动搭话阈值（0=粘人，86400=几乎不主动，默认 30 分钟） |
| GET | `/chat/sessions/{id}/nudge` | — | 角色主动搭话轮询（超阈值返回开场白/主动搭话，否则 null） |
| POST | `/chat/sessions/{id}/message` | `{input, model?}` | 发消息 → `{reply, emotion, relationship_note, npc_name, prompt}` |
| DELETE | `/chat/sessions/{id}` | — | 删除 1v1 会话（级联删 NPC/记忆） |

**设计要点**：
- 关系记忆存 `Memory(memory_type="companion")`，内容带 `[第N轮]` 前缀，`get_relationship_memory` 读取时剥离
- `nudge` 受双阈值控制：距最后对话 + 距上次主动搭话均超阈值才触发，触发后写入 `_last_nudge_ts` 冷却
- 角色卡创建时 `clinginess`（粘人度）可覆盖默认 nudge 阈值

## 角色卡（card-editor，v1.3）

角色卡制作工具（前缀 `/cards`），独立于 NPC 建模链：由结构化表单制作恋爱向 1v1 卡片，不经过 LLM 建模。前端页面 `/card-editor`。

| 方法 | 路径 | 请求体 | 说明 |
|------|------|--------|------|
| GET | `/cards/schema` | — | 编辑器表单源：`{schema, labels, selects}`（字段树 + 中文标签 + 下拉选项） |
| GET | `/cards` | — | 列出当前用户角色卡 |
| POST | `/cards` | `{name, card_data?, tags?}` | 新建（同名 409；card_data 经 normalize 补全缺省） |
| GET | `/cards/{card_id}` | — | 读取完整角色卡 |
| PUT | `/cards/{card_id}` | `{name, card_data, tags}` | 整卡替换保存 |
| DELETE | `/cards/{card_id}` | — | 删除（活跃会话持创建时快照，不受影响） |
| POST | `/cards/import` | `{source_npc_id, name?}` | 从 UserNPC 总库预填（手动物理映射：basic→identity、personality、appearance） |
| POST | `/cards/from-novel` | multipart：`file`(txt≤8MB) + `depth?` | 上传小说，LLM 提取候选角色列表 `{characters:[{name, reason}]}` |
| POST | `/cards/from-novel/character` | multipart：`file` + `character` + `relationship_note?` + `name?` + `depth?` | 选定角色，读小说抽样片段 → LLM 填卡 → 存 UserCard |

`depth` 读取深度：`快速/标准/深度/全文` 或数字（前 N 章）。快速=前 1/3、标准=前 2/3、深度=全部（默认标准）。

**card_data 字段树**（`card_schema.CARD_SCHEMA`）：`identity`（姓名/性别/年龄/职业/人设/背景）、`appearance`（整体印象/脸型/眼眸/头发/身材/穿着）、`personality`（核心/价值观/怪癖/喜好）、`speech_style`、`initial_relationship`（初始关系）、`relationship_behavior`（关系行为）、`clinginess`（粘人度）、`opening`（开场白）。

**开场白场景化**：`opening` 不再被机械复述。渲染进 prompt 时降级为「开场基调」——LLM 根据关系类型（主仆/恋人/陌生人等）+ 正在发生的场景生成开场（环境/姿态/神情/真实反应），greeting 仅作风格参考。第一轮对话注入场景块，禁止空泛招呼。

---

## 日志 & 调试

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| GET | `/api/logs` | `lines=80, user_id=可选` | 返回后端/前端日志尾部；指定 user_id 则返回该用户的 frontend.log + backend.log |
| POST | `/api/log` | JSON体 | 前端浏览器日志 POST 到此端点（含 user_id 时同时写入 user_logs/<id>/） |
| POST | `/api/log/backend` | JSON体 | 后端子模块日志 POST 到此端点（按 user_id 分流） |
| POST | `/api/clear-logs` | — | 清空**当前用户**的 frontend.log + backend.log（按 user_id 分目录，不触碰其他用户日志） |

### 日志目录结构
```
backend.log                  ← 全量后端日志
frontend.log                 ← 全量前端日志
user_logs/
  └── <user_id>/
      ├── frontend.log       ← 该用户的前端日志
      ├── backend.log        ← 该用户可关联的后端错误日志
      └── llm2/
          ├── <user_id>/     ← 分用户的 llm_summary 调用记录
          └── 年月.log       ← 全局 llm_summary 调用记录（prompt + output + status）
```

### Token 用量追踪

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| GET | `/api/usage` | `user_id=可选` | 返回每次 LLM 调用的用量记录 |
| GET | `/api/usage/summary` | `user_id=可选` | 按 label 聚合的用量摘要 |

返回格式（summary）：
```json
{
  "total_tokens": 150000,
  "by_label": {"llm_main": 80000, "NPC_MODELING": 30000, "llm_summary": 40000},
  "timing": {
    "llm_main": {"count": 20, "total_seconds": 180.5, "avg_seconds": 9.0}
  }
}
```

---

## 时间跳过

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| POST | `/sessions/{session_id}/time-skip` | `TimeSkipRequest` | 新时间状态 | 跳过 N tick，无 LLM 调用 |

**与 turn 中 time_skip 意图的区别**：此端点只做 tick 推进 + NPC 状态更新，不生成任何故事。
此外会写入一条 Facts："时光流逝，世界来到了{world_time}。"

---

## 系统端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查（start.bat 轮询用）|
| GET | `/api/models` | 可用模型列表（deepseek + gemini）|

### `GET /api/models` 返回结构
```json
{
  "models": [
    {"id": "deepseek:deepseek-v4-flash", "provider": "deepseek", "name": "deepseek-v4-flash",
     "available": true, "source": "cloud"}
  ],
  "default_model": "…"
}
```

当前列表仅包含 2 个模型（配置了 API Key 则可用）：
- deepseek、gemini
- 其余 provider（openai / sensenova / claude / ollama）的**适配器仍注册**（`model_adapter.py` 可用），但已从选择列表移除
- 排序：先 available，再 name

---

## 启动

后端默认监听 **`0.0.0.0:8002`**（`HOST`/`PORT` 配置在 `config.json` + `.env` 可覆盖，CI 部署固定 8002）。
