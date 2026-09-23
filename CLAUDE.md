# AI Narrative Engine (ANE)

修仙叙事引擎 — Phase 1 MVP。FastAPI 后端 + 前端多页面 SPA。
约 46 个 Python 源文件 / ~16600 行代码。

## 快速导航

| 你想做什么 | 看哪里 |
|------------|--------|
| 理解 turn 怎么跑 | [docs/DATA_FLOW.md](docs/DATA_FLOW.md) |
| 查数据库表结构 | [docs/DATABASE.md](docs/DATABASE.md) |
| 查 API 端点 | [docs/API.md](docs/API.md) |
| 查模块职责和依赖 | [docs/MODULES_REFERENCE.md](docs/MODULES_REFERENCE.md) |
| 意图分类规则 | [docs/INTENT_CLASSIFICATION.md](docs/INTENT_CLASSIFICATION.md) |
| 重要人物标记 | [docs/important_npc.md](docs/important_npc.md) |
| AI 工作效率规则 | [CLAUDE.md](CLAUDE.md) |
| NPC 建模（90+字段） | [docs/npc_modeling.md](docs/npc_modeling.md) |
| NPC 分类体系 | [docs/NPC_CLASSIFICATION.md](docs/NPC_CLASSIFICATION.md) |
| 完整架构设计文档 | [AI Narrative Engine (ANE).txt](AI%20Narrative%20Engine%20%28ANE%29.txt) |
| ANE Platform 项目书 | [ANE Platform.md](ANE%20Platform.md) |
| 查看 Bug 修复记录 | [docs/BUG_FIXES.md](docs/BUG_FIXES.md) |
| LLM JSON 输出质量控制 | [docs/JSON_OUTPUT_QUALITY.md](docs/JSON_OUTPUT_QUALITY.md) |
| 关系网系统 | [docs/RELATIONSHIP_GRAPH.md](docs/RELATIONSHIP_GRAPH.md) |
| 服务器部署指南 | [docs/DEPLOY.md](docs/DEPLOY.md) |
| 代码同步方式 | [docs/DEPLOY_SYNC.md](docs/DEPLOY_SYNC.md) |
| 状态变更处理 | [docs/STATE_CHANGES_LOOP.md](docs/STATE_CHANGES_LOOP.md) |
| 移动端适配 | [docs/MOBILE.md](docs/MOBILE.md) |

## 目录结构

```
backend/          → 后端源码
  ane/
    main.py          FastAPI 入口
    game_engine.py   核心编排器（turn 管线）
    worldview.py     世界观包 loader/注册表（扫目录 + 降级链 + 注入防护）
    panels.py        主角面板配置化渲染器
    config.py        配置（JSON + env 覆盖）
    config.json      服务器/数据库/LLM 配置
    database/        ORM 模型 + 异步引擎（含世界观列无损迁移）
    modules/         16 个独立模块（含 npc_modeler、pack_generator、card_schema、card_from_novel）
    content/         7 个 JSON 模板库 + 2 个 Python 封装层
    worldviews/      世界观包目录（xianxia_v1/modern_city/fantasy_kingdom/naruto_shippuden）
    tools/           NSFW 收割 + GUI 工具
    api/             FastAPI 路由 + Pydantic schemas + worldview_routes
frontend/         → 前端 SPA
  app.html           主应用（首页 NPC 总库 + 聊天界面，display 切换）
  login.html         独立登录/注册页
  settings.html      用户设置页（头像/密码/日志/设计器入口）
  designer.html      世界观设计器（/designer 路由）
  chat.html          1v1 陪伴对话页（/chat 路由）
  card_editor.html   角色卡编辑器（/card-editor 路由）
  public/
    common.js        共享工具函数（JWT、日志、颜色、NPC 格式化等）
    character.js     角色创建 + 世界观选择逻辑（ES5 共享）
tests/            → 测试（pytest，263 个用例）
data/             → SQLite 数据库文件
docs/             → 文档
```

## 快速命令

```bash
# 首次设置（虚拟环境在项目根 .venv/）
.venv\Scripts\pip install -r requirements.txt

# 服务管理器（CMD 运行，支持启动/停止/清缓存）
ane.bat

# 仅后端（带热重载 ANE_RELOAD=1）
cd backend && ANE_RELOAD=1 .venv\Scripts\python -m ane.main

# 测试（263 个用例全通过）
.venv\Scripts\pytest tests/ -v

# 设计器页
http://localhost:8002/designer

# 自动备份（每 30 秒监控全目录）
watch_backup.bat
```

## 功能变更记录

### 🧾 取消【附近人物】的人物类型约束（改由模型按场景自行决定）
- **背景**：原约束 `3 位场景路人（1 男 2 女）` 的初衷是"别让什么人都被塞进附近人物"，但实测模型
  遵守得很弱（要么硬凑性别、要么干脆乱列），于是**决定取消该约束**，把"谁在场、几位"交给模型按场景判断
- **改法**：两份提示词（SYSTEM_PROMPT + NARRATIVE_KERNEL）的【附近人物】规则收敛成一句：
  「本轮场景里出现的其他人物：**有谁、几位，由你按当前场景自行决定**（不做人数或性别限制，也不要求凑数）。
  每位一行「姓名｜身份｜外貌｜正在做什么」。玩家点名的、与其有重要关系的 NPC 必须列入」
  —— 删掉「3 位」「1 男 2 女」，以及中途试过的「1-3 位」上限；只保留**格式**与**必须列入**两条（都不是类型约束）
- **顺带澄清**：`player_relationships` 规则里含糊的「背景npc路人npc不要输出」改成
  「背景/路人 NPC 不要写进这里——它们只写在 info_panel 的【附近人物】段」，避免模型误读成"整个附近人物段都别写"
- **文档**：docs/NPC_CLASSIFICATION.md 的「产生方式」同步（不再写 3 位/1 男 2 女）
- **测试**：新增 `test_prompt_nearby_section_has_no_person_type_quota`（锁：无人数/性别配额 + 有「自行决定」说明 +
  格式保留 + 关系数组明确指路）→ 全量 **300 通过**

### 🧹 主角面板重复（位置）三处根因全修：提示词示例 / 显示层 / 落库回喂
- **症状**：主角面板里已有「位置：林之国·杉谷村」，【主角动态】又写一遍「…｜位置：林之国·杉谷村口」
- **根因 1（提示词自己挖的坑）**：`prompt_builder` 的【主角动态】规则嘴上说"位置…不要重复列出"，
  但**同一行的格式示例就是「主角名：状态：… ｜位置：…」** → 模型照示例抄。两份副本（SYSTEM_PROMPT +
  NARRATIVE_KERNEL）都改成：示例不带位置 + 显式写「示例里没有『位置：』，你也别加」，并加一条
  「位置变化必须用 state_changes 的 `location_change` 写回，别只在动态段写位置」
- **根因 2（显示层只按整行去重）**：`_mergePlayerDynamic` 原来只在"整行是面板子串"时丢弃，而
  「北荷茶光：查克拉消耗过半 ｜位置：…」整行并非子串 → 原样显示。改为**字段级去重**：逐「｜」段判断
  字段名是否已在权威面板中（位置/身份/性格/能力…），是则丢该段、保留动态内容；整行只剩回声才连段删。
  另修：模型**没写【】标题、裸写「名字：状态…｜位置：…」**时原先压根没被认成动态段（`_isDynamicLine` 补三种写法）
- **根因 3（落库/回喂不清洗）**：旧回声存库后每轮原样回喂 → 被模型反复抄。新增
  `panels.strip_dynamic_field_echoes(info_panel, player_panel)`，在 **Step 17 落库前**与**回喂前**各跑一遍；
  只删字段、不动状态；【交互人物】【附近人物】里 NPC 自己的「位置/状态」不受影响（只处理动态段）
- **测试**：`test_prompts.py` +3（`strip_dynamic_field_echoes_unit` 五组形态 / 提示词示例不含「｜位置：」/
  端到端 turn：LLM 故意抄位置 → 返回与落库的 info_panel 均无回声、权威面板仍保留位置）；JS 侧用真实数据
  验 5 种形态全 PASS；全量 **299 通过**

### 🔍 广场自诊断（服务器「开源广场什么都没有」不再静默）
- **背景**：用户报服务器网页端开源广场为空。本地复现了服务器的升级路径（**旧表 + 新代码**）：
  先建一个没有 `is_official` 列的 `worldview_shares` 老库 → `init_db()` 迁移补列（日志确认）→
  `ensure_open_source_shares()` 6 个包全部发布成功。代码路径没问题 → 线上为空说明**那份代码还没跑起来**，
  或失败被 `except` 吞掉看不见
- **后端**：`GET /worldviews/shared` 现在返回 `publish_error` + `declared_open_source`；若声明了开源却一条都没发布，
  会**算出原因**（最常见：库里没有任何用户账号 → 官方条目挂不上外键）而不是安静返回空数组；
  新增诊断端点 `GET /worldviews/open-source` → `{declared, published, missing, just_added, error}`
- **前端**：广场为空时把 `publish_error` 用红字直接显示在页面上（含声明列表 + 提示去看诊断端点），不用翻服务器日志
- **测试**：`test_open_source.py` +3（老库迁移→发布 端到端 / 无用户时给出原因 / 广场与诊断端点字段）→ 全量 296 通过

### 🗃️ 前端资源改为回源校验（修「改了前端页面没变」）
- **现象**：用户报告气泡透明度滑块/勾选框"没反应"，但同一份代码在 headless Chrome 里驱动真实页面完全正常
  （实测 0.72 → 拖到 25% → 0.1 → 取消勾选变 `rgb(34,28,21)` 全不透明 → 勾回 0.1）
- **根因**：`StaticFiles` 默认**不发 `Cache-Control`** → 浏览器按"启发式缓存"直接用旧副本不回源。
  用户恰好在两次编辑之间刷过页（先加了滑块控件、后加的 JS 函数），缓存里那份"有滑块、没函数"，点它自然没反应
- **修法**：`main.py` 加 HTTP 中间件，凡是 `text/html` / `text/css` / `javascript` / `image/svg+xml` 一律
  `Cache-Control: no-cache, must-revalidate`（仍靠 ETag 走 304，不浪费带宽）；图片（jpg/png/webp）不受影响，
  包内背景图仍走 assets 路由的 `max-age=86400`
- **测试**：新增 `tests/test_frontend_cache.py`（HTML/CSS/JS 必须 no-cache；背景图必须仍是长缓存）
- **定位手法（可复用）**：静态服务（API 用桩）+ `Page.addScriptToEvaluateOnNewDocument` 预置假 JWT（`ane_token`）
  + CDP `Runtime.evaluate` 驱动真实页面读 `getComputedStyle`——比手抄 CSS 做预览更可靠（手抄会绕过真实层叠/缓存）

### 🎚️ 气泡透明度滑块（信息栏 + 正文输出栏只透明背景、文字不透明）
- **需求**：信息栏与正文气泡完全不透明，想要一个透明度滑块（文字不要求透明）
- **做法**：`theme.css` 把气泡色拆出 RGB token（`--bubble-ai-rgb` / `--bubble-user-rgb` / `--bubble-system-rgb`）
  + 变量 `--bubble-alpha`（1=不透明）与 `--bubble-alpha-soft`（系统消息用一半）；`app.html` 的
  `.msg.ai` / `.msg.user` / `.msg.system` 与**信息栏盒子内联样式**都改成 `rgba(var(--…-rgb), var(--bubble-alpha))`
  —— 只动背景 alpha，`color` 不变，所以文字始终不透明
- **浮窗**：🖼 背景图区块新增「气泡不透明度」滑块（10–100%，默认 72%）+ 说明；勾掉「聊天气泡半透明」时强制不透明并置灰滑块；
  `.bg-transparent` 现在只管毛玻璃与投影，不再写死透明度
- **按会话隔离**：新键 `chat_bg_alpha:<sid>`（与背景/位置/半透明同一套）；`setChatBgAlpha()` 做了上下限夹取
- **验证**：JS 侧断言扩到 23 项全 PASS（含默认 0.72、25%→0.25、系统消息 0.13、越界夹取、B 世界不继承 A 的数值、
  取消勾选强制 1 且滑块置灰）；headless Chrome 截图确认 72%/25%/100% 三档真实渲染正确；全量 290 通过

### 🖼️ 世界观包默认聊天背景（`ui.json` 的 `chat_background` + 包内 `assets/`）
- **需求**：把一张图设为火影忍者包的默认背景
- **机制**：`ui.json` 新增 `chat_background` = `{image, position_y, dim}`（也支持字符串简写）；
  `image` 只允许 `assets/<单层文件名>`，扩展名限 jpg/jpeg/png/webp/gif；`dim` 为压暗强度（0–0.9，默认 0.35）
- **后端**：`Worldview.chat_background` 解析成 `{url, position_y, dim}`（校验目录/扩展名/文件存在，防路径穿越）；
  新增资源路由 `GET /worldviews/{id}/asset/{filename}`（`FileResponse` + `Cache-Control: max-age=86400`）；
  `GET /sessions/{id}` 与角色创建响应都带回 `worldview` + `chat_background`（schemas.SessionSummary 加两个字段）
- **前端**（app.html）：`applyChatBg()` 优先级 = 玩家自定义（localStorage）> 包默认 > 无；包默认叠一层
  `linear-gradient(rgba(10,12,16,dim),…)` 压暗保证可读；`clearChatBg()` 清掉自定义后**回落到包默认**；
  🎨 浮窗新增「当前背景来源」提示；`sessionChatBg` 在会话切换/角色创建时由响应赋值
- **配置**：`naruto_shippuden` 配 `assets/chat_bg.jpg`（1920×1344，473 KB，源自 `D:\图\thumb-1920-602092.jpg`），
  `position_y=50%`、`dim=0.42`
- **测试**：`test_worldview.py` +4（包声明与文件存在 / 非法声明全被拒（穿越·非 assets·扩展名·不存在）/
  资源路由 200·400·404 / `GET /sessions/{id}` 带回包背景）→ 全量 290 通过
- **文档**：WORLDVIEW_PACK_SPEC.md 新增「chat_background 字段」+ 目录结构补 `assets/`
- **注意**：设计器「文案」编辑器是在已加载的 ui 对象上改字段，不会丢 `chat_background`；资源改动需重启服务生效
- **后续（同一功能的补充逻辑要求）**：
  - **每个世界观包内置一张背景图** → 6 个包全部配 `assets/chat_bg.jpg` + `ui.json` 声明（火影=原作风格群像插画；
    其余 5 包=程序生成的氛围占位图：修仙雾山/都市夜景/西幻暮色城堡/海贼落日帆船/三国尘雾军旗，1600×1000，130–210 KB，无版权问题；
    换图只需覆盖对应包的 `assets/chat_bg.jpg`，名字不变即可）。每包 `dim` 按明暗分别调过（0.25–0.42）
  - **自定义背景限制在该用户的此次世界（session）内** → localStorage 键改为按会话隔离
    `chat_bg:<sid>` / `chat_bg_pos:<sid>` / `chat_bg_transparent:<sid>`（新增 `_bgStoreKey()` / `_chatBgCustom()` /
    `syncChatBgControls()`）；换世界回到该包的默认背景，清除自定义也回落包默认；旧的全局 `chat_bg` 键不再读取（不迁移，避免跨世界串味）
  - 测试：`test_every_builtin_pack_ships_default_chat_background`（逐包校验声明+文件）+ JS 侧
    12 项会话隔离验证（用页面里真实函数 + 假 localStorage 跑：A 世界自定义不影响 B 世界、切回仍在、清除回落包默认、位置/半透明也按会话存）

### 🐛 修复：pytest 跑完全套后进程不退出（aiosqlite 非守护线程卡住解释器关闭）
- **症状**：`pytest tests/` 打印完 `286 passed` 后永久挂住（CI 里 job 一直跑到超时）
- **根因**：① `aiosqlite 0.22.1` 每个连接起一个**非守护**工作线程（`Thread(target=_connection_worker_thread)` 无 `daemon=True`），Python 退出时 `threading._shutdown()` 会 join 它们；② 测试里有连接没人关——turn 提交后 fire-and-forget 的后台 llm_summary 用**全局 session 工厂**（指向真实 `data/ane.db`）另开 session，测试只 dispose 自己的内存引擎
- **定位手法**（可复用）：`faulthandler.dump_traceback_later(20, exit=True)` + `pytest.main(...)` 抓超时瞬间全线程栈 → 直指 `aiosqlite/core.py:_connection_worker_thread` 与 `threading.py:_shutdown`；再用「给 `aiosqlite.Connection.__init__` 打桩 + pytest 钩子记录当前 `nodeid`」精确定位到泄漏用例
- **修法**：`tests/conftest.py` 加 session 级 autouse fixture，会话结束时对仍活着的 `aiosqlite.Connection` 调 `Connection.stop()`（把"关连接+停线程"投进工作线程队列，不需要事件循环）。**注意不能每用例后扫**——会停掉后台任务正在 await 的连接导致跑中挂
- **验证**：修复前挂 >5 分钟；修复后 `286 passed / 退出码 0 / 6.2s`，探针残留连接线程 `1 → 0`
- **文档**：docs/BUG_FIXES.md「Bug 8」

### 🌐 世界观包「默认开源」（开源 = 使用权限，修改权限仅白名单）
- **语义**：开源只授**使用**权——所有账号都能在角色创建里用这些包开局、也能在开源广场看到它们；
  **改包内容仍然只有白名单管理员**（有 owner 的包则是作者或管理员）。非白名单账号写接口一律 403
- **包清单**：6 个内置包（xianxia_v1 / modern_city / fantasy_kingdom / naruto_shippuden / one_piece / sanguo_yanyi）
  manifest 全部加 `"open_source": true`；`sanguo_yanyi` 去掉 `owner_user_id` → 六包权限完全统一（内置系统包）
- **自动发布**：新增 `backend/ane/open_source.py::ensure_open_source_shares()`——把 `open_source: true` 的包幂等补进
  `worldview_shares`（`is_official=True`，广场作者显示「官方内置」+「官方」标记）；调用点＝服务启动（`main.py` lifespan）
  + `GET /worldviews/shared`（打开广场自愈，无需重启也能看到）。条目挂靠真实用户（外键）：优先白名单管理员，其次最早注册用户；库里无用户时跳过等下次补
- **不可下架**：内置开源包 `DELETE /worldviews/share` 返回 400（要下架就删 manifest 的 `open_source`）；广场卡片对官方包隐藏「撤销开源」按钮
- **DB**：`worldview_shares.is_official` 新列（`init_db` 无损迁移 `ALTER TABLE ... ADD COLUMN`）
- **生成器**：`pack_generator` 产出 manifest 显式写 `"open_source": false`（作者可改 true 或走设计器「开源」按钮）
- **测试**：新增 `tests/test_open_source.py`（9 用例：清单声明 / 自动发布幂等 / 无用户时跳过 / 广场官方条目 / 任意账号可用内置包开局 / 非白名单改不动 / 管理员可改且官方包不可下架）
- **文档**：WORLDVIEW_PACK_SPEC.md 新增「open_source 字段（默认开源）」+ 权限语义；docs/CLAUDE.md「世界观包权限」章节同步

### 📋 信息栏合并（取消附近人物 / 推荐行动的独立渲染）
- **需求**：「取消附近人物的独立渲染，取消推荐行动的独立渲染。所有信息类内容都进入信息栏」
- **提示词（两份都改：SYSTEM_PROMPT + NARRATIVE_KERNEL）**：输出 JSON 骨架从 `narrative/state_changes/player_relationships/nearby_characters/recommendations` 收敛为 `narrative/state_changes/player_relationships/info_panel`；删除 `recommendations 规则`/`nearby_characters 规则` 两个独立块，改为 **`info_panel 规则`**——「信息栏 = 所有信息类内容的唯一去处」，固定四段 `【主角动态】/【交互人物】/【附近人物】/【推荐行动】`（各段用【…】小标题、段间空行、无内容整段省略），并保留「禁止复述主角面板已有内容」
- **后端**：`output_parser` / `TurnResult` 的 `nearby_characters` / `recommendations` 字段**保留为兼容字段**（老输出仍可解析、API 不破坏），但 prompt 不再要求模型产出
- **前端（app.html）**：`addInfoPanel(text)` 改为纯文本单参数（原 `recommendations` 参数与分节渲染删除）；删除 `addNearbyCards()` 与历史恢复里的 `【附近人物】` 分支（该前缀现在只作为 conversation 里的块边界，防止旧 JSON 尾巴渲染成正文）；新增 `_recsToText(recs)` 把世界观包 `ui.json` 的初始推荐行动按同一 `【推荐行动】` 格式并入信息栏文本；会话创建/切换路径由两次 `addInfoPanel` 收敛为**一次**（原来会连出两个「信息栏」框），`#rec-area` 现在只放工具按钮（❤️ 🚻 📚）
- **测试**：`test_modules.py::TestPromptBuilder::test_build_does_not_inject_suggestions` 断言口径更新（`【推荐行动】` 现在是 info_panel 的必备分节名，故只断言"推荐**内容**不泄漏"+ 不成列表注入）；`test_prompts.py` 25 通过、`test_modules.py` 92 通过
- **文档**：DATA_FLOW.md 的「Nearby Characters 架构详解」改写为「信息栏（info_panel）架构详解」（含兼容字段表）；NPC_CLASSIFICATION / MODULES_REFERENCE / API / MOBILE / RELATIONSHIP_GRAPH 同步
- **🐛 后续修复（同一改动上线后实测发现）**：模型把信息栏段名改写成「【无名忍者】【当前交互人物】」且**漏掉【推荐行动】段** → ① 提示词两份都补「段标题固定照抄这四个，不要改名」；② `_withRecs()` 每轮把 `td.recommendations` 并入信息栏文本（模型已自带【推荐行动】段则不重复追加）——否则推荐行动在回合流程里会彻底不显示（原来靠独立推荐栏）；③ `_mergePlayerDynamic()` 把**主角面板**与 info_panel 的【主角动态】段**合并成一个块**（面板静态行 + 动态状态行，重复行如面板已有的「位置：…」自动去重，其余段落后接），修复"同一个主角信息显示成两块"

### 🔁 修复扩展栏目重复注入（面板 × prompt × info_panel 回声）
- **症状**：同一个 prompt 里 `_extensions` 栏目渲染两遍——`panels.py` 的「扩展：」（主角面板内）+ `prompt_builder.py` 的 `extension:` 行；LLM 看到两遍后又把它抄成 info_panel 的 `【栏目名】` 分节，而 info_panel 每轮原样回喂 → 这份重复被固化、逐轮累积（提示词膨胀 + 正文跟着复述同一批设定）
- **修法**：① 只保留主角面板一条权威路径（删掉 `prompt_builder` 的 `extension:` 渲染，并把引用它的提示词说明改为"见主角面板「扩展：」项"）；② 新增 `panels.strip_extension_echo_sections()`，在**存库前**与**回喂前**剔除"标题 == 扩展栏目名"的分节（删到空行为止，精确匹配，不碰主角动态状态行/交互人物行）；③ info_panel 规则补一条「禁止复述主角面板已有内容」
- **测试**：`test_prompts.py::test_strip_extension_echo_sections_unit`（单元）+ `test_extension_echo_stripped_from_info_panel`（端到端：栏目仍在权威面板、info_panel 回声被剔除、落库版本已清洗且同轮其他内容保留）
- **回退**：不想要"删分节"行为，去掉 `game_engine.py` 里那两处调用即可（`panels.py` 的函数保留不影响其他逻辑）

### 🌌 主页背景「墨卷」+ 随机天气（雨/雪）
- **背景层**（`theme.css` 的 `.ink-backdrop`，可复用层）：天光 + 双层缓慢漂移墨雾 + 双层远山剪影 + 全局纸纹；`position: fixed` 钉在视口（滚动时不动），接入新页面时容器需 `position:relative;z-index:0`、内容 `z-index:1`、页面外固定 UI 需 `z-index:2`；关闭 `<body data-no-ink-bg>`，强弱 `--ink-bg-opacity`
- **天气层**（同一 backdrop 内的 `.weather`，三层景深）：雨 = 短划线贴图 + 整层倾斜（**不能用无限竖线**——竖线沿自身方向平移看不出运动）；雪 = 雪场贴图 + 左右轻摆。每层仅 1 个 DOM 元素，上千粒子靠可平铺 SVG 贴图；只动 `transform`（合成器线程），密度/速度由 `--tile` / `--wx-duration` 单值控制，粒子不加 `will-change`
- **方向与曲线**：位移一律 `+Y`（向下）；两组动画都用 **`linear`**——雪的水平摆动把 12 段正弦采样**烘焙进关键帧**，因为 `ease-in-out` 是**逐段**生效的，会在每个关键帧把垂直速度归零（实测最慢段只有均速 54% → 肉眼是"一顿一顿"）。雨速约 141 / 258 / 458 px/s（远/中/近），雪约 23 / 27 / 36 px/s（恒速）
- **玩家不可调整**：无任何 UI 开关；每次页面载入 `initWeather()` 随机启用雨或雪（强度 1/2/3 加权随机：0.4/0.45/0.15），选择不持久化；系统「减少动态效果」开启时整层隐藏（WCAG 2.3.3）

### 🛡️ 部署保护 + 服务器内容同步 + 世界观历史完善
- **部署保护（ci.yml）**：deploy job 在 `actions/checkout` **之前**检测服务器 worktree 未提交改动（网页编辑器直接写磁盘的包文件，如 world_facts.json）——有改动则 tar 打包 `worldviews/` + `git diff` 上传为 artifact `ane-server-edits-backup`，并**中止部署**，防止 checkout 静默覆盖手改内容。worktree 干净时正常部署。**⚠️ 已暂时停用**（`9b5cf77`，恢复见 `af599de`）——停用期间服务器未提交的网页改动会被部署直接覆盖，请网页编辑后手动 commit + push
- **服务器 ↔ GitHub 双向同步**：网页编辑保存 → 服务器 `git add -A && git commit && git push`（HTTPS + PAT）→ 本地 `git pull`。图片库数据（`data/images/` + `image_categories` 表）是运行时数据，不在 git 内，无需推送也不受部署影响
- **📜 世界观历史（lore）**：naruto `world_facts.lore` 写入完整木叶编年史——表格版（木叶前 24 年—木叶 68 年，时间/大事记/人物出生）+ 浓缩年表（按木叶年纪年）+ 关键事件散文 + 人物成长线（春野樱/井野/纲手/手鞠/雏田/白/夕日红，含年龄戳如雏田 12/14/19/32）；聊天页 📜 弹窗展示
- **🛠 提示词库双击取消选择**：新增 `promptLibToggle`——双击提示词条目切换启用/取消，允许"不启用任何提示词"；`promptLibEnable` 勾选时只同步 radio 与高亮、不再重建 DOM（否则双击事件丢失）；后端 `PUT /prompts/{id}` 原生支持 `enabled:false`
- **🧹 经济系统彻底移除**：清理 5 包 `manifest.savings_unit`、`panel` 存款字段、`system_prompt` 的 `economy_change` 说明、`ui.json` 经济尾巴推荐文案、designer 货币名称字段（后端已不消费）；文档同步清理
- **🎭 naruto 移除职业/能力**：删除 `cultivations`（下忍/中忍/上忍/暗部/医疗忍者/村民）数据与 `form.json`「忍者等级」字段——与「身份」选项重复；面板忍者等级改由 `player.cultivation` 直接显示
- 服务器端编辑：naruto `player_templates.json` 出身背景简化（label 去括号、清空 initial_resource/性格倾向、砂忍流亡→流亡忍者）
- **🔒 世界观包权限（系统包隔离 + 白名单）**：内置公共包（manifest 无 `owner_user_id`，即 xianxia/modern_city/fantasy_kingdom/naruto_shippuden/one_piece）仅白名单管理员可编辑；用户上传安装的包仅作者或管理员可改；包写操作一律要求登录（匿名 401 / 无权 403）；`GET /worldviews?scope=mine` 让 designer 按用户过滤（普通账号只见自己上传的 + 开源共享库的包，内置包隐藏）；`/settings` 页显示用户编号；白名单 = `config.json worldview_admin_ids` / env `ANE_WORLDVIEW_ADMIN_IDS`（服务器 `207d25fa7acf` / 本地 `3bc7553ba877`）；**本地 agent 改文件系统绕过 API 权限层（无防御），修改内置包前须经用户确认**——详见 docs/CLAUDE.md「世界观包权限」

### 🎯 位置体系重构 + 卡片渲染修复 + 1v1 历史修复（v1.3+）
- **初始无位置**：`_pick_start_location` 改为返回空，玩家初始无固定位置；第一轮 prompt 渲染「具体位置：未设定」，LLM 按角色身份/世界观/时间线自主决定位置（输出 location_change 确立）。解决砂忍角色被生成在木叶等身份-位置错配
- **剧情航线注入**：world_facts.json 支持 `story_route` 字段（极简箭头链），每轮【本世界权威设定】注入；LLM 从航线定位当前位置并推下一站。one_piece 已配 21 地完整航线（东海篇+伟大航路+新世界）
- **时间线锚定**（one_piece）：14 条时间线加 `start_location`，`_pick_start_location` 优先读它（初始无位置改动后保留字段但 create 不再使用）
- **location_change 同步层级**：turn 管线 step15 更新 `player.location` 时同步 `location_hierarchy`（与 `/move` 接口对齐），避免移动后 prompt 位置显示旧层级
- **卡片按包渲染**：角色创建成功卡片改为读各包 `ui.json` 的 `character_card`（title/lines/conditional），删除硬编码字段清单（修为/灵根/衣物等）；conditional 金手指标签跟随包配置（火影=血继限界/海贼=恶魔果实）
- **自定义身份不再覆写出身**：自定义身份时 `identity_desc` 留空（不再硬编码「自定义身份」）、不再覆写 `background_summary`（出身保持独立选择）；composite 渲染裁剪空 token 悬挂分隔符
- **age 类型修复**：表单 number 字段转 int 存储（age 字符串导致第二轮 `str/int` 比较崩）
- **1v1 主动搭话历史修复**：`get_history` 对主动搭话轮只输出 assistant 消息（不再显示假玩家消息「（角色主动开口）」）；`_get_conversation` 注入时标注为「【角色主动开口】」
- 测试：全量 262 通过

### 🧠 记忆系统优化 + 短输出重试 + 姓名归一化
- **短记忆精简**：llm_summary 输出只保留「当前地点 / 行动/目标 / 推荐行动」，删除「交互npc / 持有物品变化 / 世界事件」三部分；存储时压缩字段间空行
- **短记忆显示 5 轮**：📘 弹窗显示全部 5 轮短记忆 + 完整内容（原只显示 3 轮且每轮截 4 行）；修复 `get_summaries_since` 的 `asc+limit` 错配（原取最早 3 条）
- **短输出自动重试**：narrative 汉字数低于字数下限时自动重试一次（"写充分到 N-M 字"），失败保留首次输出
- **关系人名归一化**：turn 注入「已登记人物名单」让 LLM 用全名；后端按**后缀匹配**兜底（「路飞」→「蒙奇·D·路飞」，但「林星」不并入「林星如」防前缀误伤）
- **创建成功消息修正**：删除重复「身份」渲染、删除「衣物：未设定」、恶魔果实 name/tag 相同时去重（「无 — 无」→「无」）
- **长记忆总结重构**：纪元总结从"按时间顺序流水账"改为**因果+影响两维**（进展/遗留），≤250字，有什么写什么不硬凑；📘 弹窗长记忆去掉多余的 turn_number 小标签（内容头部已含区间）
- **偶发不分段修复**：prompt 要求正文分段（段落间空行）+ output_parser 兜底——narrative >200字无换行时按句末标点**每 2-3 句一组自动分段**
- **narrative 泄漏清洗**：`_clean_narrative_leakage` 剥离正文里混入的 JSON 对象/数组、`【附近人物】`标记、`"key":value` 残留（仅删含 JSON 特征的花括号，正常文字花括号如「{好}」保留；清洗后为空则回退原始）
- **🌡 温度滑动条**：🛠 弹窗新增温度调节（0.2-1.2，默认0.8），`TurnRequest.temperature` 全链路透传
- 测试：+5（温度透传 / 泄漏清洗×2 / 短输出重试 / 名称归一化 / 前缀不混淆），全量 263 通过

### 📱 UI 与健壮性修复（v1.3）
- **NPC 总库响应式布局**：`renderNpcTable` 按视口宽度切换——≤768px 用卡片布局（每 NPC 一张卡，名称+操作一行，身份/修为/标签横向排布），>768px 保留表格。解决手机端中文竖排、表格超宽只显示一列的问题
- **顶栏整理**：删除 🏠 圆形按钮（与 ⚔ 标题 `goHome()` 重复）；💬 1v1 对话按钮移到主页「🌍 世界管理」右侧
- **🎨 浮窗**：手机端（≤768px）改为屏幕居中定位（原锚定按钮导致超出视口）；支持点浮窗外部关闭
- **背景图功能**：🎨 浮窗新增🖼 背景图区块——上传本地图片（localStorage 存储）、位置滑块上下移动（`background-position-y`）、聊天气泡半透明开关（`backdrop-filter` 毛玻璃）。`chat_bg`/`chat_bg_pos`/`chat_bg_transparent` 存 localStorage
- **刷新竞态修复**：init 先判定最终落点（`willEnterChat`），进聊天区则不渲染主页总库（消除闪烁）；`npcLibLoad` 失败重试一次（防总库偶发空表）；`#home-npc-table` 加 `min-height:120px` 防坍缩
- **SQLite 并发写锁**：`create_async_engine` 加 `connect_args={"timeout":15}` + `pool_pre_ping`，所有业务连接 busy_timeout=15s（此前仅 init_db 连接有，并发写时报 `database is locked`）
- **短记忆格式**：llm_summary 两处 prompt（`memory_manager` 同步 / `game_engine` 后台）移除「氛围/环境」行，存储与叙事注入均不再含氛围数据
- **LLM 输出健壮性**：`output_parser` 新增 `_as_dict_list`/`_as_str_list` 清洗——`nearby_characters`/`offstage_npcs`/`player_relationships`/`recommendations` 遇 LLM 畸形输出（字符串片段）自动修复/丢弃，不再触发 Pydantic 校验崩溃

### 💬 1v1 陪伴对话 + 角色卡编辑器（v1.3）
- **陪伴对话**：独立于世界管线的 1v1 虚拟角色对话（`companion_engine.py` + `api/chat_routes.py`，前缀 `/chat`）。会话用 `worldview="companion_v1"` 标记，不生成世界区域、不进入 turn 管线
- 角色源：UserNPC 总库 + UserCard 角色卡合并（`GET /chat/characters`），开启会话后按卡渲染 prompt 文本
- 关系记忆：LLM 输出 `relationship_note` → 存 `Memory(memory_type="companion")`（`[第N轮]` 前缀），「TA 记得什么」面板读取
- 主动搭话（nudge）：双阈值（距最后对话 + 距上次主动搭话均超阈值）+ `_last_nudge_ts` 冷却，默认 30 分钟，`clinginess` 粘人度可覆盖
- 前端 `chat.html`（`/chat` 路由，主页「🌍 世界管理」右侧 💬 按钮进入）
- **角色卡**：`card_editor.html`（`/card-editor`）+ `api/card_routes.py`（前缀 `/cards`）+ `modules/card_schema.py`（恋爱向字段树）。结构化表单制作，**不依赖 LLM 建模链**；支持从总库预填（`POST /cards/import`）
- `UserCard` 表（`user_cards`），`card_data` 含 identity/appearance/personality/speech_style/initial_relationship/relationship_behavior/clinginess/opening
- **开场白场景化**：`opening` 不再机械复述——渲染时降级为「开场基调」，LLM 按关系类型（主仆/恋人/陌生人等）+ 正在发生的场景生成（环境/姿态/神情/真实反应），greeting 仅作风格参考；第一轮对话注入场景块，禁止空泛招呼
- **小说→角色卡**：`POST /cards/from-novel`（上传 txt，`depth` 档位：快速/标准/深度/全文或章节数）→ LLM 提取候选角色 → `from-novel/character` 抽样片段填卡。`card_from_novel.py`：候选提取已移除硬编码书名，抽样按**名字出现密度**降序取最相关片段（常见短名更精准）
- **system prompt 内嵌**：`COMPANION_SYSTEM_PROMPT` 内嵌为代码常量（不再依赖 companion_v1 包文件）
- 测试：`test_companion.py`（会话/聊天/关系记忆/nudge 阈值）+ `test_cards.py`（含小说管线 + depth 档位）

### 🧩 新建建模NPC 世界观选择（v1.3）
- 建模弹窗新增**世界观下拉**：切换即时加载该世界观的提示库（姓名池/身份池/原型/quick-pick），提交用选中世界观建模——可在任意会话为任意世界观建模 NPC
- 修复下拉只显示 xianxia_v1：打开弹窗时若世界观列表未加载，先 `loadWorldviews` 再填充
- 移除 `companion_v1` 伪世界观包（1v1 会话内部标记，非真实作品；system prompt 已内嵌至 companion_engine，功能不受影响）

### 🔗 建模链路跨世界观适配（v1.3）
- `UserNPC` 新增 `worldview` 列：记录建模时的归属世界观（无损迁移）。编辑弹窗按此渲染字段树、AI 增量更新按此取 schema，总库跨世界资产不再被错误地用 xianxia 模板读写
- 修复增量更新/直接替换把 `model_version` 硬编码降级为 1.0 的 bug（保留当前版本）
- 前端编辑弹窗改为从 NPC 自身 model 构建字段树，跨世界观独有字段（lifestyle/wardrobe/ninja_ability 等）不再丢失
- 测试：`test_modeling_chain.py`（17 用例）+ `test_modeling_routes.py`（7 用例 HTTP 集成）

### ⚡ Prompt 内核去重 + NSFW 安全规则常态化
- `NARRATIVE_KERNEL_PROMPT` 去重：合并"禁止反问"3 处重复表达与"推进感"重复强调，精简 73 字符
- 「禁止用拼音/字母/谐音替代敏感词」从 NSFW 块提升为**全局常态规则**（进 kernel，所有 shell+kernel 包每轮生效，不依赖 HO）
- NSFW 块精简：删除与 kernel 重复的拼音规避说明，保留质量强化（极致细节/Type1/Type2）

### ⚙️ xianxia 迁移到 shell+kernel（四包架构统一）
- xianxia_v1 的 `assembly` 从 `full` 改为 `shell+kernel`，`system_prompt.txt` 从完整 legacy 主 prompt 瘦身为纯世界观壳（1981 字符，只含修仙独有内容：世界观/宗门规则/state_changes 类型/修仙推荐）
- 好处：四包架构统一、xianxia 自动获得 kernel 全部更新（拼音规避常态化/禁反问精炼）、每轮完整系统 prompt 略减（5509 vs 5523）
- 收益评估（修正）：本体层面 shell+kernel 比 legacy 小 14 字符；真正价值是架构统一 + 安全规则生效，非 token 节省
- golden 测试更新：不再断言"xianxia 逐字等于 legacy"，改为验证 shell 独有内容 + kernel 通用内容并存
- 运行时确认：game_engine turn 用 `assemble_system(worldview)` 覆盖 `PromptContext.system` 默认值，迁移对实际 turn 生效

### 🛡️ 世界观包校验器增强（validate_pack 结构性规则）
- 姓名池规则：各池内无重复、男/女名池不重叠、名池不含姓氏（疑似完整姓名）、姓氏超 4 字警示
- panel 字段来源对齐：panel 引用 attrs 字段需能在 player_templates（identities/golden_fingers）找到，识别 golden_finger option_map 映射（golden_finger_*）
- 时间线完整性：world_facts.timelines 每节点需 id（唯一）/label/description/must_follow/forbidden/characters
- 校验器发现并修复：fantasy_kingdom 姓氏重复（艾略特）、名池含姓氏（凯尔）；xianxia/火影 panel 字段对齐确认
- 4 个包 validate 全部通过，仅剩合理警告（西方复姓超4字/自定义 attrs 字段）

### ⏳ 起始时间线选择（IP 世界观）
- `world_facts.json` 支持 `timelines[]` 变体：`{id, label, description, must_follow[], forbidden[], characters[]}`，会话创建时可选择起始时间线
- `sessions.timeline_id` 列（无损迁移）+ `POST /sessions` 接受 `timeline` 参数
- `process_turn` 按 `timeline_id` 解析变体：变体的 must_follow/forbidden/characters 覆盖 base，prompt【本世界权威设定】块额外注入「当前时间线」行
- 前端角色创建弹窗：有 timelines 的世界观显示「⏳ 起始时间线」下拉 + 描述提示，随世界观切换刷新
- 火影包已配 19 个时间线：6 个鸣人出生前（战国/木叶创立/一战/二战三忍/三战/九尾之乱）+ 13 个出生后细分节点（学校/毕业分班/波之国/中忍考试/佐助夺还/疾风传前/归来/救我爱罗/晓镇压/佐鼬/佩恩入侵/五影会谈/四战前），每个标注鸣人年龄、村子与忍界状态

### 📖 火影世界观包审计修复
- 设定硬伤：佐鼬决战地点改宇智波据点（非终结之谷）、蝎死于千代+樱之战（非我爱罗）、螺旋丸习得时间改中忍考试期间、带土当场揭晓灭族真相（删 forbidden 矛盾项）、"沙隐村"→"砂隐村"
- 内部矛盾：顶层约束默认时间线明确为"第七班成立"时期、合并"佩恩（长门）/漩涡长门"重复条目、战国时代扉间称谓改"未来的二代火影"
- 姓名池：移除 9 个非姓氏角色名、3 个女性角色名从男池移出、3 个完整姓名只留名、红去重
- **家族姓氏补全**：姓氏池重写为 29 个原作规范家族姓氏（千手/宇智波/日向/漩涡 + 木叶秘术家族 + 砂隐/雾隐叛忍家族 + 大筒木/竹取），男名/女名池扩充为火影风格通用名（男 56 / 女 54），避免与知名角色撞名
- 忍者等级：移除非官方"三忍级/传说级"，补官方"特别上忍/精英上忍"
- 金手指：千鸟标注为忍术（非血继限界）、补冰遁/木遁/熔遁/沸遁/尘遁/尸骨脉/轮回眼 7 个血继限界
- 杂项：孤儿出身去掉"人柱力之子"绑定、system_prompt 影分身措辞、age_rules 影级年龄改灵活（水门24岁成四代等特例）

### 🌐 开源世界观共享平台
- **推送**：designer 便捷开发板块每个世界观卡片新增「📤 开源」按钮 → 弹窗填简介 + 点选标签（修仙/都市/西幻/科幻/冒险/日常/轻松/硬核/IP改编/无超自然）→ 推送到后端共享库
- **下架**：designer 便捷开发板块已开源的世界观卡片显示「(已开源)」标记 + 「🚫 下架」按钮，作者可一键下架（后端 `DELETE /worldviews/share`，仅限本人；路由须注册在 `DELETE /{worldview_id}` 之前避免被动态参数捕获）
- **广场**：主页面（app.html）新增「🌐 开源世界观广场」区块（NPC总库下方），展示所有用户开源的世界观卡片（标题/作者/简介/标签/星级评分/已安装标记）
- **评分**：每个开源世界观支持 1-5 星评分（`worldview_ratings` 表，同用户重复评覆盖），列表显示平均分 + 人数
- **使用**：点「▶ 使用」一键安装（安装到本机世界观池）+ 打开该世界观的角色创建，直接开新世界
- **撤销**：主页面广场作者可撤销自己的开源（「撤销开源」按钮），仅限本人
- **数据**：新增 `worldview_shares`（共享库）+ `worldview_ratings`（评分）两表，`init_db` 自动建表
- **API**：`POST /worldviews/share`（推送）、`DELETE /worldviews/share?worldview_id=`（撤销）、`GET /worldviews/shared`（列表含评分）、`POST /worldviews/shared/{id}/rate`（评分）、`POST /worldviews/shared/{id}/install`（安装）
- 修复：异步下访问 `s.user` 关系触发 SQLAlchemy MissingGreenlet → 改为批量查询 author 名

### 🎨 designer 选项编辑器扩展（NPC 自定义）
- 「📋 选项」新增 9 个 NPC tab：姓氏池/男名池/女名池/NPC性格池/修为级别池/体型池/衣着池/体质天赋池/NPC原型
- 新增 `kind: 'str_list'` + `list_key` 机制：选项编辑器统一支持字符串数组（姓名池等）与对象数组（NPC原型等）两种数据形态的增删改保存
- 修复既有 bug：对象数组保存时表头 tr 导致索引错位（此前 edits 只影响上一行，新行丢失）
- NPC 原型的 identity/personality/cultivation/behavior_note 均可可视化编辑，写回 `npc_templates.json` 的 `core_archetypes`

### 🏗️ 四个世界观包内容完善（开箱即用）
- **modern_city**：补 `form.json`（角色创建表单，含金手指 card_grid）；world_templates 7→25 个都市地点；npc_templates 5→12 个原型；player_templates 补 6 个都市金手指 + 8 种出身；intent_keywords 补 work/shopping/social/rest
- **fantasy_kingdom**：world_templates 补 6 大势力（骑士团/法师议会/圣光教会等）+ 4 城镇 + 17 地点；npc_templates 姓名池 14→28/24/24 + 12 原型；intent_keywords 补 quest/magic_study/explore/social
- **naruto_shippuden**：补 `modeler/schema.json`（忍者建模字段，查克拉/血继限界/忍术）；npc_templates 姓名池 30/30/19 + 10 忍者原型 + 忍界 realm；world_facts 9→41 角色（十二小强/晓/三忍/各影）；world_templates 补 10 大组织 + 23 地点；intent_keywords 补 training/mission/spar/medical
- **xianxia_v1**：intent_keywords 补 craft/harvest/quest/alchemy_lore；events idle_events 2→8；constraints 补 5 条 triggers
- **意图分类优先级修正**：input_validator 将包级意图插入 CORE 的 trade 之前（nsfw/ntr/time_skip/use_item 之后），使世界观特有意图（work/shopping/training 等）不被宽泛 CORE 的 travel/trade/dialogue 抢先；修复 `外卖` 含"卖"字被 CORE trade 误判
- **数据字段对齐**：四个包 backgrounds 补 `background_summary`、identities 补 `identity_desc`（与 derive/prompt_builder/ui 卡片期望的字段名一致）

### 🧹 清理氛围死代码
- `SceneContext` 移除从未赋值的 `atmosphere/weather/present_characters/perceptible_objects` 字段 + prompt_builder 两处对应渲染分支（此前 `if s.atmosphere:` 永远不触发）
- 移除 `sects/detail` 接口的 atmosphere/law_description/spiritual_rules 收集（无前端消费方，返回空 details）
- designer「📋 选项」移除「地点氛围」tab（place_attrs，含 era_description/law_description 列）
- 保留：位置层级/场景描述/时间/不在场相关人物（`absent_related`）——这些有真实数据源且每轮生效

### 🧩 NPC 建模档案世界观化（modeler/schema.json）
- 新增包级工件 `modeler/schema.json`：作者声明自己世界的 NPC 建模字段树，包级全替换修仙 90+ 字段
- `game_engine.py` 建模 prompt / `_llm_cover` 增量更新 / 渲染器统一按包 schema 组装，包未提供时降级 xianxia 默认模板
- `npc_modeler.render_model_for_prompt` 改为**通用递归渲染**：遍历 model dict 本身，未知字段也能渲染进 [重要人物] 块（中文标签从字段名映射，schema 可覆盖）
- 前端编辑弹窗按 schema 动态渲染字段树（替换第二份硬编码模板），`GET templates` 返回 `modeler_schema`
- `modeler/schema.json` 可经 designer `data` API 读写（`write_artifact` 白名单子路径）；pack_generator 自动产出通用 schema
- 各包已配：xianxia（原 90+ 字段）、fantasy_kingdom（骑士/魔法/血统）、modern_city（职场/生活/社交）
- **匹配修复**：`_model_rels_to_entries` 通用化（遍历 relationships 所有键，中文标签走 npc_modeler keymap）；重要人物面板/导入/列表的 `basic.identity/cultivation` 兜底（title/rank/occupation/level）；`formatNpcModel` 详情浮层通用化（未知分节递归渲染）；关系网图合并 `NPC.relations`（建模/导入声明的关系即时上边，不依赖 LLM 后台）

### 🧩 NPC 提示库世界观化
- 「新建建模NPC」弹窗的 📚 提示库（角色模板/姓名池/修为/性格/体型/衣着）从前端硬编码改为按世界观读取
- 数据源 = 世界观包的 `npc_templates.json`，经 `GET /sessions/{id}/templates?worldview=` 返回给前端；字段缺失时回退到修仙缺省数组
- 空数组 = 作者明确"无此项"，前端隐藏对应区块（如 modern_city 无灵根 → 🧬 区块消失）；`quick_pick_sections` 可自定义区块标签与数据源键
- `POST /npcs/library` 新增 `worldview` 查询参数，总库建模按当前世界观走 `modeler/role.txt` 包模板（此前固定 xianxia legacy）
- 为 `xianxia_v1` 补 `spiritual_roots/constitutions/body_types/attires` 键；新增 `modern_city`、`fantasy_kingdom` 的 `npc_templates.json`（作者示范，可经 designer「📋 选项」直接编辑）

### 🌍 多世界观平台（世界观包系统）
- 世界观包 = 纯 JSON/文本目录（`backend/ane/worldviews/<id>/`），作者无需改代码即可创建新世界观
- 首个参考包 `xianxia_v1`（修仙，从 content/ 与代码常量抽出）+ 验证包 `modern_city`（现代都市，无宗门/无金手指/无 cultivate）
- `worldview.py` loader：扫目录注册 + 逐工件降级链（包 → xianxia → 引擎常量）+ 路径注入防护（`^[a-z0-9_]{1,48}$`）
- System Prompt 双模式：`shell+kernel`（世界观外壳 + 通用叙事内核，四包均用此）/ `full`（包内完整文本，保留兼容；无包时兜底用引擎内建 legacy）
- `sessions.worldview` 列 + 无损迁移（`ALTER TABLE ... ADD COLUMN ... DEFAULT 'xianxia_v1'`，启动时自动执行）
- 意图关键词 / 叙事约束 / 玩家面板（`panel.json` 渲染器）/ 角色建模 prompt / 事件白名单 / 前端文案（`ui.json`）全部世界观化
- 事件白名单改为 `CORE ∪ 包.extra_event_types`，并修复 `economy_change` 被白名单静默丢弃的 bug
- 前端角色创建弹窗新增世界观下拉，切包即时刷新表单选项 + 显隐宗门/金手指区块 + 动态按钮/标签文案
- 共享 `frontend/public/character.js`（ES5）承载世界观选择逻辑，app.html 等页面接线
- 世界观包规范见 [docs/WORLDVIEW_PACK_SPEC.md](docs/WORLDVIEW_PACK_SPEC.md)

### 🔧 世界观平台 · P2（作者工具链）
- `POST /worldviews/upload`（zip 上传+校验+安装，默认包受保护）、`GET /worldviews/{id}/validate`（错误/警告报告）、`POST /worldviews/{id}/reload`（清 loader 缓存）、`DELETE /worldviews/{id}`
- manifest 支持 `time_per_intent`（按世界观覆盖时间推进量）+ `events.json`（包级 NPC 事件池，替换 time_manager 内联修仙事件）
- state-change handler 新增通用兜底：世界观特有事件类型（target=player + field）自动写入 `player.attributes[field]`，无需引擎改动即可扩展状态

### 🏰 世界观平台 · P3/P4（平台补完）
- 第三个世界观包 `fantasy_kingdom`（中世纪西幻，剑与魔法/银币/王国，无宗门），三个世界观覆盖东方玄幻/现代都市/西方奇幻，全部通过验证
- manifest 支持 `calendar`（按世界观覆盖 seasons/times_of_day/month_to_season）
- 会话级包版本钉住：`sessions.worldview_version` 记录创建时版本，包升级时检测但不自动迁移旧会话
- 前端世界观管理面板：settings.html「🌍 世界观管理」区块（列表/校验/重载/上传/删除），后端工具链 API 全覆盖

### ✨ 世界观平台 · P5（作者生成器）
- `POST /worldviews/generate`：填短表单（ID/名称/设定/风格基调/能力体系/货币/称呼/职业/地点/按钮文案）→ 自动生成完整 11 文件世界观包 zip
- 4 种风格模板（奇幻/现代/科幻/修仙）提供世界观外壳骨架，通用叙事内核由引擎 `shell+kernel` 自动拼接（作者无需写通用 prompt）
- 前端 settings.html「✨ 一键生成新世界观」表单：生成并下载 zip → 上传安装 → 校验，全链路浏览器内完成
- 生成器产物能直接通过 validate + 创建会话（测试锁定）

### 🎨 世界观平台 · P6-P9（设计器与可视化编辑）
- `form.json` 声明式角色创建表单（title/fields/kind/options_from/hint_template/allow_custom/visible_if/store/option_map/derive），前端动态渲染 + 后端 `apply_character_from_form` 通用写入
- 独立 `designer.html`（`/designer` 路由）：便捷开发板块从设置页迁出，含世界观列表 + 一键生成 + 上传安装
- 三个可视化编辑器（每个世界观卡片按钮）：✏️ 表单（form.json 字段增删改排序）、💬 文案（按钮/标题/称呼 + 5 组初始推荐）、📋 选项（职业/出身/性格/身份/特殊能力/地点表格增删改）
- 通用数据读写 API：GET/PUT `/worldviews/{id}/data/{file}`（白名单 JSON 工件）+ `/worldviews/{id}/form` + `/worldviews/{id}/ui`
- 生成器按风格产出 `npc_templates.json`（姓名池）；`npc_manager._random_name` 补实现（按世界观读池）

### 🖼️ 世界头像 + 裁剪工具
- 聊天区 session bar 左侧圆形头像，世界内独立存储（localStorage）
- 点击头像弹大图预览，可上传、裁剪（鼠标拖拽/缩放圆环）、保存
- 裁剪支持鼠标滚轮缩放 + 触屏双指缩放

### 📝 NPC 编辑弹窗
- NPC 总库"查看/编辑"按钮改为完整属性表单弹窗
- 基于模板展示所有字段（空字段也可编辑），支持自定义标签
- 保存时整 model_data 替换 + 标签同步到后端

### 🏠 返回主页
- 顶栏 ⚔ 标题可点击返回主页（`goHome()`）；独立 🏠 圆形按钮已移除（与标题功能重复）

### 🔄 刷新回聊天区
- `init()` 从 `localStorage` 读 `ane_last_session`，自动恢复上次会话

### ⭐ 局内建模（已关闭）
- 输入区 ⭐"重要人物"改名为"局内建模"（曾用功能）
- **已关闭**：前端 ⭐ 局内建模入口已移除，`/npc-modeling` 后端接口保留但无 UI 触发
- 保留：📦 加载建模（turn 管线自动注入已建模人物）+ NPC 总库建模（`/npcs/library`）

### 🗑 清空日志（移入用户设置）
- 聊天区顶栏"🗑 清空日志"按钮移除，移入 `/settings` 用户设置页「日志管理」区块
- 只清空当前登录用户自己的前后端日志（按 user_id 分目录）

### ⚙️ 用户设置页简化
- settings.html 移除「🎨 文字颜色」和字体下拉设置
- 聊天区顶栏 🎨 颜色/字体设置仍保留

### 🎛️ 模型选择列表精简
- `/api/models` 只返回 deepseek 与 gemini 两个模型（其余 provider 的适配器仍注册，但不出现在选择列表）

### 📘 记忆查看
- 📘 按钮在底部栏右端（❤️🚻📚 旁），点击弹窗展示短记忆 + 长记忆

### 🗺️ 地图（已移除）
- 世界地图功能已全部删除
- 宗门选择改为角色创建时的下拉框

### 🏗️ 多页面 SPA 架构
- 前端从单页 `index.html` 拆分为 `login.html` + `app.html`（含首页/聊天）+ `settings.html`
- 首页和聊天视图通过 `display` 切换

### 🧑/👩 角色创建
- 创建流程：点 `+` → 弹角色创建弹窗 → 填信息 → POST
- 角色表单底部有**宗门选择**下拉框
- 确认创建后自动随机分配城市作为初始位置

### 🕐 世界时间格式
- 格式：`第{年}年·{月}月·{季节}季·{时段}`
- 季节：春/夏/秋/冬（1-3月→春）
- 时段：清晨/上午/正午/下午/傍晚/夜晚/凌晨（7段）
- 24 ticks/天，每个 action 推进 1-12 ticks

### 📝 叙事原则
- 交互推进三分层：短交互 / 有分量小场景 / 大型事件
- 禁止"你准备怎么做"等反问句式
- NSFW 分为 Type 1（一轮闭环）+ Type 2（可跨轮次）

### 🔄 热重载
- `ANE_RELOAD=1` 环境变量启用 uvicorn reload
- `ane.bat` 启动前自动杀旧进程

