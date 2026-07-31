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
  public/
    common.js        共享工具函数（JWT、日志、颜色、NPC 格式化等）
    character.js     角色创建 + 世界观选择逻辑（ES5 共享）
tests/            → 测试（pytest，150 个用例）
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

# 测试（150 个用例全通过）
.venv\Scripts\pytest tests/ -v

# 设计器页
http://localhost:8002/designer

# 自动备份（每 30 秒监控全目录）
watch_backup.bat
```

## 功能变更记录

### 🌍 多世界观平台（世界观包系统）
- 世界观包 = 纯 JSON/文本目录（`backend/ane/worldviews/<id>/`），作者无需改代码即可创建新世界观
- 首个参考包 `xianxia_v1`（修仙，从 content/ 与代码常量抽出）+ 验证包 `modern_city`（现代都市，无宗门/无金手指/无 cultivate）
- `worldview.py` loader：扫目录注册 + 逐工件降级链（包 → xianxia → 引擎常量）+ 路径注入防护（`^[a-z0-9_]{1,48}$`）
- System Prompt 双模式：`shell+kernel`（世界观外壳 + 通用叙事内核）/ `full`（包内完整文本，xianxia 用此保持逐字一致，golden 测试锁定）
- `sessions.worldview` 列 + 无损迁移（`ALTER TABLE ... ADD COLUMN ... DEFAULT 'xianxia_v1'`，启动时自动执行）
- 意图关键词 / 叙事约束 / 玩家面板（`panel.json` 渲染器）/ 角色建模 prompt / 事件白名单 / 前端文案（`ui.json`）全部世界观化
- 事件白名单改为 `CORE ∪ 包.extra_event_types`，并修复 `economy_change` 被白名单静默丢弃的 bug
- 前端角色创建弹窗新增世界观下拉，切包即时刷新表单选项 + 显隐宗门/金手指区块 + 动态按钮/标签文案
- 共享 `frontend/public/character.js`（ES5）承载世界观选择逻辑，chat.html/app.html 各自接线
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
- 双页收敛：chat.html 已删除（孤儿页面），`/chat` 重定向到 `/`，前端统一 app.html + character.js

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

### 🏠 首页按钮
- 🏠 按钮移到顶栏最左侧，36px 圆形，与 ⚔ logo/🧑 用户头像同行

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

