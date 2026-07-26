# ANE API 端点速查

> 基础路径：`http://127.0.0.1:8001`
> 认证方式：JWT Bearer Token（`Authorization: Bearer <token>`）
> 除 `/auth/register`、`/auth/login`、`/api/health` 外，所有 `/sessions/*` 端点需要登录。

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
1. 创建 `WorldSession` 记录
2. 创建世界区域（约 7 区域，含区域属性和层级）
3. 创建玩家 stub（初始名"无名修士"，随机初始位置）
4. **生成 30 个初始 NPC**（8 核心 + 22 次要，从 `npc_templates.json` 原型池随机选取）
5. 返回 session_id + 初始世界状态

### `GET /sessions/{session_id}` — 返回的额外数据
- `conversation`: 完整对话历史（`[{turn_number, content}]`，格式为【玩家】xxx\n【AI】yyy\n【附近人物】[...]）
- `npc_names`: 核心 NPC 的姓名列表
- `htem_directory`: 当前保存的 HTEM 角色目录（保留兼容，实际被 NPC_MODELING 替代）
- `map_data`: 世界地图数据（如已保存）
- `world_intro`: 世界简介文本（保存地图时存入，刷新后仍可查看）
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
  "model": null,                     // 可选，模型 ID 如 "openai:gpt-4o"
  "mark_important_npc": false        // 勾选 "重要人物" 时传 true
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
| `htem_directory` | 已废弃（HTEM 已被 NPC_MODELING 替代），始终返回空字符串 |

---

## NPC 建模

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| POST | `/sessions/{session_id}/npc-modeling` | `NpcModelingRequest` | `NpcModelingResponse` | 提取输入中的所有人名，比对库，对有模型的NPC增量更新，返回新名字列表 |
| POST | `/sessions/{session_id}/npc-modeling/confirm` | `NpcModelingConfirmRequest` | `NpcModelingConfirmResponse` | 确认后对单个新NPC创建全量建模 |

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
| GET | `/sessions/{session_id}/memory-panel` | `MemoryPanelResponse` | 💭 记忆面板（暂时关闭） |

### `MemoryPanelEntry`
```json
{
  "category": "longmemory | fact_relationship | npc_important | shortmemory",
  "turn_number": 6,
  "content": "..."
}
```

---

## 角色创建

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| POST | `/sessions/{session_id}/character` | `ApplyCharacterRequest` | 玩家详情（含金手指信息） | 应用玩家选择的角色设定 |
| GET | `/sessions/{session_id}/templates` | — | 模板数据 | 获取角色创建可选列表 |

### `ApplyCharacterRequest`
```json
{
  "name": "林逸",             // 玩家姓名，必填，最长 20 字
  "age": 19,                  // 12-999，默认 19
  "gender": "男",             // 男/女
  "background": "家族旁支",   // 出身背景：无父无母/贫穷家庭/家族旁支/家族嫡系/富商家庭/血脉相传/皇亲国戚
  "cultivation": "凡人",       // 修为：凡人→渡劫期
  "personality": "谨慎隐忍",  // 性格选择
  "identity": "外门弟子",      // 身份：杂役弟子/外门弟子/内门弟子/核心弟子/散修/自定义
  "golden_finger_id": "heavenly_book",
  "golden_finger_custom": "",
  "identity_custom": ""
}
```

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

### 角色创建模板数据（`GET /sessions/{session_id}/templates`）

返回 `player_templates.json` 的全部内容，前端用来渲染下拉框。包含：
- `genders` — 性别列表
- `backgrounds` — 7 种出身（含 desc/initial_resource/personality_tendency/typical_sect_path）
- `cultivations` — 10 个修为等级（凡人→渡劫期，含寿元和能力描述）
- `personalities` — 4 种性格（含完整 desc）
- `identities` — 5 种身份 + 自定义（含 clothing/monthly_income/background/spiritual_root/talent_note + desc）
- `golden_fingers` — 9 种金手指（含 icon/name/tagline/desc）

---

## 地图

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| POST | `/sessions/{session_id}/map` | `map_data` | `{ok}` | 保存世界地图数据（含 world_intro） |
| GET | `/sessions/models/sects` | — | `{sects: [名称列表]}` | 获取可用宗门名（按后缀过滤 + NSFW 关键词过滤） |
| GET | `/sessions/models/sects/detail` | — | `{sects: [{name, description, details}]}` | 同上，含详细描述 |
| GET | `/sessions/models/cities` | — | `{cities: [名称列表]}` | 获取可用城市名（以"城"结尾，NSFW 过滤） |

**宗门过滤规则**：以 圣地/宗/门/宫/阁/殿/谷/观/派 结尾，且通过 NSFW 关键词检测。
**城市过滤规则**：以"城"结尾，且通过 NSFW 关键词检测。
**NSFW 过滤关键词**：魔法少女、精灵、矮人、兽人、触手、便利店等非修仙词汇。

### `map_data` 保存格式
```json
{
  "seed": 12345,
  "count": 12,
  "locations": [{"x": 100, "y": 150, "name": "青云宗", "identity": "宗门", "type": "sect"}],
  "cityLocations": [{"x": 100, "y": 150, "name": "天风城"}],
  "world_intro": "📜 世界已生成\n\n【你的角色】..."
}
```

---

## 日志 & 调试

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| GET | `/api/logs` | `lines=80, user_id=可选` | 返回后端/前端日志尾部；指定 user_id 则返回该用户的 frontend.log + backend.log |
| POST | `/api/log` | JSON体 | 前端浏览器日志 POST 到此端点（含 user_id 时同时写入 user_logs/<id>/） |
| POST | `/api/log/backend` | JSON体 | 后端子模块日志 POST 到此端点（按 user_id 分流） |
| POST | `/api/clear-logs` | — | 清空全局 frontend.log + backend.log |

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
| GET | `/api/models` | 可用模型列表（云端 + Ollama 本地）|

### `GET /api/models` 返回结构
```json
{
  "models": [
    {"id": "openai:gpt-4o", "provider": "openai", "name": "gpt-4o",
     "available": true, "source": "cloud"}
  ],
  "default_model": "…"
}
```

支持 6 个 Provider（配置了 API Key 则可用）：
- openai、deepseek、sensenova（商汤）、claude（Anthropic）、gemini（Google）、ollama（本地）
- 排序：先 available，再 local（ollama），再 cloud，再 name

---

## 启动

后端默认监听 **`127.0.0.1:8001`**（`HOST`/`PORT` 配置在 `config.json` + `.env` 可覆盖）。
