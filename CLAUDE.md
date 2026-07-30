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
    config.py        配置（JSON + env 覆盖）
    config.json      服务器/数据库/LLM 配置
    database/        ORM 模型 + 异步引擎
    modules/         14 个独立模块（含 npc_modeler、memory_manager）
    content/         7 个 JSON 模板库 + 2 个 Python 封装层
    tools/           NSFW 收割 + GUI 工具
    api/             FastAPI 路由 + Pydantic schemas
frontend/         → 前端 SPA
  app.html           主应用（首页 NPC 总库 + 聊天界面，display 切换）
  login.html         独立登录/注册页
  settings.html      独立用户设置页
  public/
    common.js        共享工具函数（JWT、日志、颜色、NPC 格式化等）
tests/            → 测试（pytest，87 个用例）
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

# 测试（87 个用例全通过）
.venv\Scripts\pytest tests/ -v

# 自动备份（每 30 秒监控全目录）
watch_backup.bat
```

## 功能变更记录

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

### ⭐ 局内建模
- 输入区 ⭐"重要人物"改名为"局内建模"
- 功能不变：勾选后发送，提取人名做 AI 建模

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

