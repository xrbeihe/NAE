# ANE Bug 修复记录

> 记录代码审查中发现并修复的 Bug，作为后续维护参考。

---

## Bug 1 — HO 标记逻辑不一致（input_validator.py）

### 问题
`nsfw_confirmed` 判断用 `user_input`（原始输入，未 strip），而 HO 标记剥离用 `text`（`.strip()` 后的输入）。当玩家输入末尾有空格时（如 `"我要双修HO "`），`user_input.endswith("HO ")` 为 False → `nsfw_confirmed=False`，引擎把 HO 写成了但当成普通对话处理。

### 修复
统一使用 `text`（strip 后）做 `nsfw_confirmed` 判断：`text.endswith("HO")`。

**相关文件**：`backend/ane/modules/input_validator.py:312-317`

---

## Bug 2 — DB session 泄漏（main.py）

### 问题
`get_db()` 是 `@asynccontextmanager` 异步上下文管理器，中间件中写成 `async for db in get_db()`，会引发 `TypeError`。该异常被外层 `except Exception: pass` 吞掉，导致：
- DB session 泄漏（底层连接未释放）
- `request.state.user` 始终为 None

### 修复
`async for` → `async with get_db() as db:`。

**相关文件**：`backend/ane/main.py:159-162`

---

## Bug 3 — pending_debut 不在 load_model_data 中处理（game_engine.py）

### 问题
`pending_debut` 检查只遍历 `active_set.core_npcs` 和 `active_set.nearby_npcs`。
`load_model_data` 块会把**不在 Active Set 中**的已建模 NPC 注入 `ctx.core_npcs`，但这些 NPC 的 `pending_debut` 永远不会被检查清除，`is_modeling_turn` 也不会被置 True，导致【建模登场——强制完整外貌描写】Prompt 指令不触发。

### 修复
在 `load_model_data` 块内（遍历 `all_db_npcs` 时）直接执行 `lts.pop("pending_debut")`，与注入 complete NPC Context 同步完成。

**相关文件**：`backend/ane/game_engine.py:393-396`

---

## Bug 4 — 建模 prompt 字段传错参数（game_engine.py）

### 问题
`_run_npc_modeling()` 的建模 prompt 中：
```python
f"人物性别：{player_name}\n\n"
```
把**玩家的名字**写在了"人物性别"字段，LLM 会混淆。

### 修复
获取 NPC 自身的 `gender` 字段：
```python
f"人物性别：{npc_model.gender or '待确定'}\n\n"
```

**相关文件**：`backend/ane/game_engine.py:911`

---

## Bug 5 — config.json 重复键（config.json）

### 问题
`month_to_season` 键出现两次：
```json
"month_to_season": [[1,3,"春"],[4,6,"夏"],[7,9,"秋"],[10,12,"冬"]],
"month_to_season": [[1,3,"春"],[4,6,"夏"],[7,9,"秋"],[10,12,"冬"]],
```
`json.load` 取最后一个，功能不受影响，但维护时改第一个不改第二个会引入时序 bug。

### 修复
删除重复行。

**相关文件**：`backend/ane/config.json:65-66`

---

## Bug 6 — 建模确认时未设置 is_important（api/routes.py）

### 问题
`npc_modeling_confirm` 路由确认建模后，NPC 的 `is_important` 字段保持为 False。导致【重要人物】面板显示"（无）"，且 📦 加载建模的 `is_important == True` 查询无法匹配到该 NPC。

### 修复
在 `db_npc` 创建/获取后立即设置 `db_npc.is_important = True`。

**相关文件**：`backend/ane/api/routes.py:542`

---

## Bug 7 — 忘记 HO 时无提示（前端）
### 问题
用户输入 NSFW 关键词但忘记加 HO 时，LLM 会在"写不写露骨内容"之间纠结，导致输出大量过渡文字后触达 `max_tokens` 截断，用户白等 2 分钟。

### 修复
前端 `sendTurn()` 中正则检测 NSFW 关键词（匹配 ≥2 个），未加 HO 时在输入框上方显示红色提醒，不阻断发送。

本次审查遵循的检查路径：

1. **HO 标记路径**：前端输入 → `input_validator.validate()` → Intent 覆盖 → NSFW material 注入
2. **中间件路径**：FastAPI middleware → JWT decode → DB session → `request.state`
3. **pending_debut 路径**：建模完成 → `pending_debut=True` → turn 管线 debut 检查 → Prompt Builder 注入
4. **建模 prompt 路径**：前端确认 → `_run_npc_modeling()` → prompt 拼装 → LLM
5. **配置加载路径**：`config.json` → `config.py` → 各模块 import
