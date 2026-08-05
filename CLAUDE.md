# AI Narrative Engine (ANE)

修仙叙事引擎 — Phase 1 MVP。FastAPI 后端 + 前端多页面 SPA。
约 38 个 Python 源文件 / ~4500 行代码。

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
    modules/         15 个独立模块（含 npc_modeler、pack_generator）
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
tests/            → 测试（pytest，208 个用例）
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

# 测试（152 个用例全通过）
.venv\Scripts\pytest tests/ -v

# 设计器页
http://localhost:8002/designer

# 自动备份（每 30 秒监控全目录）
watch_backup.bat
```

## 功能变更记录

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

