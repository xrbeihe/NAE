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

## Bug 8 — clearLogs 漏 async 关键字（前端 index.html）

### 问题
`clearLogs()` 函数定义时没有 `async` 关键字，函数体内却使用了 `await`：

```js
function clearLogs() {
  const res = await apiFetch('/api/clear-logs', ...);  // ❌ await 在非 async 函数中
  const data = await res.json();
}
```

JS 引擎编译整个 `<script>` 块时发现此语法错误即**放弃全部编译**，导致该 script 块内所有函数定义（`handleAuthSubmit`、`toggleAuthMode` 等约 80 个全局函数）均未注册。页面加载后立即报：

```
Uncaught SyntaxError: await is only valid in async functions and the top level bodies of modules
5(索引):569 Uncaught ReferenceError: handleAuthSubmit is not defined
```

### 修复
`function clearLogs()` → `async function clearLogs()`。

**相关文件**：`frontend/index.html:1456`

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

---

## Bug 8 — pytest 跑完全套后进程不退出（aiosqlite 非守护线程）

### 症状
`pytest tests/` 打印完 `286 passed, ... in 6.2s` 之后**永久挂住**，不返回、不退出（CI 里表现为 job 一直跑到超时；本地表现为命令挂着不动）。

### 定位方法
用 `faulthandler` 抓超时瞬间的全线程栈：

```bash
.venv\Scripts\python.exe -c "import faulthandler,sys; faulthandler.dump_traceback_later(20, exit=True); import pytest; sys.exit(pytest.main(['tests/','-q']))"
```

输出直接指认病因——主线程卡在解释器关闭阶段等一个线程：

```
Thread 0x00005274 (most recent call first):
  File "...\aiosqlite\core.py", line 59 in _connection_worker_thread
Thread 0x0000359c (most recent call first):
  File "...\threading.py", line 1542 in _shutdown        ← 主线程在 join 非守护线程
```

### 根因
两件事叠加：

1. **aiosqlite 每个连接起一个非守护线程**（`aiosqlite 0.22.1`：`Thread(target=_connection_worker_thread, args=(...))`，**没有 `daemon=True`**）。Python 退出时 `threading._shutdown()` 会 join 所有非守护线程 —— 只要有一个连接没被关闭，就永远等下去。
2. **测试里有个连接没人关**：turn 提交后 fire-and-forget 的后台任务 `_run_background_summary`（`game_engine.py:1056+`）用**全局 session 工厂** `ane.database.engine.async_session_factory` 另开 session，而测试只 dispose 自己的内存引擎，全局引擎（指向真实 `data/ane.db`）从不关闭 → 该连接的 worker 线程活到进程退出。

（用打桩法定位到具体用例：给 `aiosqlite.Connection.__init__` 打桩 + pytest 钩子记录当前 `nodeid`，泄漏点＝`tests/test_prompts.py::test_info_panel_persistence_across_turns`。）

### 修复
`tests/conftest.py` 增加 session 级 autouse fixture：会话结束时遍历 `gc.get_objects()` 里仍活着的 `aiosqlite.Connection`，调 `Connection.stop()` 让工作线程收到哨兵后退出（`stop()` 把"关连接 + 停线程"投进工作线程队列，不需要事件循环，因此不会踩 cross-loop 的坑）。

**注意**：不能在每个用例结束后扫（会停掉后台任务正在 await 的连接，future 永不 resolve → 变成跑中挂）；只在**会话结束**时扫才安全。

### 相关文件
`tests/conftest.py`

### 验证
修复前：`286 passed` 后挂 >5 分钟不退出；修复后：`286 passed，退出码 0，用时 6.2s`；探针复验残留连接线程数 `1 → 0`。
