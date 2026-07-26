# 关系网系统 (Relationship Graph)

## 概述

关系网系统是一个后台异步运行的模块，从每轮叙事正文中提取人物关系，以结构化边（edge）的形式存储在 `NPC_Relationship` 表中。玩家可通过前端 🚻 按钮查看实时关系图谱。

整体流程：

```
llm_main 叙事正文
  + offstage_npcs（幕后NPC标记）
  + NPC 建模档案关系
      → _run_bg_relationship（后台异步）
          → LLM(llm_relationshipprocess) 增量更新
              → NPC_Relationship 表
                  → 前端 🚻 端点查询
```

## 数据库模型

`NPC_Relationship` 表（`backend/ane/database/models.py`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | String (PK) | UUID hex[:12] |
| `session_id` | String (FK) | 所属世界会话 |
| `source_id` | String (FK→NPC) | 发起方 NPC ID（可为空，表示未在 NPC 表中创建记录） |
| `source_name` | String | 发起方姓名（始终不为空） |
| `target_id` | String (FK→NPC) | 接收方 NPC ID（可为空） |
| `target_name` | String | 接收方姓名（始终不为空） |
| `rel_type` | String | 关系类型（支持 `/` 分隔的多重关系） |
| `description` | Text | 关系详细描述 |
| `affinity` | Integer | 亲密度 -100~+100 |
| `updated_at` | DateTime | 最后更新时间 |

## 数据来源

`_run_bg_relationship`（`backend/ane/game_engine.py`）每轮 commit 后异步触发，增量模式运行：

### 输入（增量）

1. **已有关系网** — `NPC_Relationship` 表当前所有边
2. **本轮叙事** — 最近 3 轮 `Memory(memory_type="conversation")` 中的 `【AI】` 正文
3. **本轮 offstage_npcs** — `llm_main` 输出的 `offstage_npcs` 数组
4. **本轮新建模 NPC** — 刚完成 ⭐ 建模的 NPC 的 `long_term_state["model"]` 关系数据

### LLM 调用

标签：`llm_relationshipprocess`

```json
{
  "add": [
    {"source": "姓名A", "target": "姓名B", "type": "关系类型", "description": "描述", "affinity": 整数}
  ],
  "update": [
    {"source": "姓名A", "target": "姓名B", "type": "新类型", "description": "新描述", "affinity": 新亲密度}
  ]
}
```

### 写入规则

- **add**：新增关系边到 `NPC_Relationship` 表
- **update**：匹配 `(source_name, target_name)` 后原地更新已有边的 `rel_type`、`description`、`affinity` 和 `updated_at`
- 关系类型用 `/` 分隔表示多重关系（如 `"母亲/性爱对象"`）
- `affinity` 钳制在 -100~+100

## 触发时机与频率

- 每轮 `process_turn` commit 后调用 `_asyncio.ensure_future(self._run_bg_relationship(...))`
- 完全在后台运行，不阻塞玩家等待
- 服务重启后丢失未完成的 LLM 调用，但 DB 中已有的关系网数据不受影响

## 前端端点

### API

```
GET /api/sessions/{session_id}/relationship-graph
```

返回：

```json
{
  "session_id": "...",
  "edges": [
    {"source": "主角", "target": "柳青衣", "type": "合作", "description": "...", "affinity": 30},
    ...
  ]
}
```

### UI

推荐行动栏右侧 🚻 按钮，点击弹出关系网面板：

- 按 source 姓名分组
- 亲和度颜色编码：绿色 >= 50，黄色 >= 0，红色 < 0
- 关系类型用 `[]` 标注
- 显示亲和度数值

## 相关配置

- `SYSTEM_PROMPT_SUFFIX`：在 `_run_bg_relationship` 的 prompt 中作为基础 system 指令注入
- `model_adapter.generate()`：使用默认模型，标签 `llm_relationshipprocess`

## 与其他模块的关系

| 模块 | 交互方式 |
|------|----------|
| PromptBuilder | 控制 `offstage_npcs` 在 llm_main 输出格式中的定义和规则 |
| OutputParser | 解析 `offstage_npcs` 字段到 `ParsedOutput` |
| NPCManager | 读 `NPC` 表构建 `name→id` 映射 |
| process_turn | 触发后台关系处理（Step 17 后） |
| API routes | `/relationship-graph` 端点供前端查询 |
| NPC Modeler | 读取 `long_term_state["model"]` 中的 `relationships` 和 `attitude_to_player` |
