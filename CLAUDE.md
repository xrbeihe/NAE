# AI Narrative Engine (ANE)

修仙叙事引擎 — Phase 1 MVP。FastAPI 后端 + 前端 SPA。
约 35 个 Python 源文件 / ~4000 行代码。

## 快速导航

| 你想做什么 | 看哪里 |
|------------|--------|
| 理解 turn 怎么跑 | [docs/DATA_FLOW.md](docs/DATA_FLOW.md) |
| 查数据库表结构 | [docs/DATABASE.md](docs/DATABASE.md) |
| 查 API 端点 | [docs/API.md](docs/API.md) |
| 查模块职责和依赖 | [docs/MODULES_REFERENCE.md](docs/MODULES_REFERENCE.md) |
| 意图分类规则 | [docs/INTENT_CLASSIFICATION.md](docs/INTENT_CLASSIFICATION.md) |
| 重要人物标记 | [docs/important_npc.md](docs/important_npc.md) |
| AI 工作效率规则 | [docs/CLAUDE.md](docs/CLAUDE.md) |
| Htem Prompt 格式规范 | [docs/Htem规范.md](docs/Htem规范.md) |
| 完整架构设计文档 | [AI Narrative Engine (ANE).txt](AI%20Narrative%20Engine%20%28ANE%29.txt) |
| ANE Platform 项目书 | [ANE Platform.md](ANE%20Platform.md) |
| 查看 Bug 修复记录 | [docs/BUG_FIXES.md](docs/BUG_FIXES.md) |
| LLM JSON 输出质量控制 | [docs/JSON_OUTPUT_QUALITY.md](docs/JSON_OUTPUT_QUALITY.md) |
| 世界地图渲染原理 | [docs/WORLDMAP.md](docs/WORLDMAP.md) |
| 地图玩家标记交互 | [docs/WORLDMAP_PLAYER_MARKER.md](docs/WORLDMAP_PLAYER_MARKER.md) |
| 关系网系统 | [docs/RELATIONSHIP_GRAPH.md](docs/RELATIONSHIP_GRAPH.md) |
| 服务器部署指南 | [docs/DEPLOY.md](docs/DEPLOY.md) |
| 代码同步方式 | [docs/DEPLOY_SYNC.md](docs/DEPLOY_SYNC.md) |

## 目录结构

```
backend/          → 后端源码
  ane/
    main.py          FastAPI 入口
    game_engine.py   核心编排器（turn 管线）
    config.py        配置（JSON + env 覆盖）
    config.json      服务器/数据库/LLM 配置
    database/        ORM 模型 + 异步引擎
    modules/         14 个独立模块（含 npc_modeler）
    content/         7 个 JSON 模板库 + 2 个 Python 封装层
    tools/           NSFW 收割 + GUI 工具
    api/             FastAPI 路由 + Pydantic schemas
    static/          静态文件目录（前端构建产物）
frontend/         → 前端源码
  index.html         SPA 主页面
  public/worldmap.js 世界地图 Canvas 渲染器
tests/            → 测试（pytest）
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

# 构建前端（复制静态文件到 static/）
cp frontend/index.html backend/ane/static/index.html
cp frontend/public/worldmap.js backend/ane/static/worldmap.js

# 测试
.venv\Scripts\pytest tests/ -v
```

## 重要功能变更记录

### 🕐 世界时间格式
- 格式：`第{年}年·{月}月·{季节}季·{时段}`
- 季节：春/夏/秋/冬（1-3月→春）
- 时段：清晨/上午/正午/下午/傍晚/夜晚/凌晨（7段，每段约3.4 tick）
- 24 ticks/天，每个 action 推进 1-12 ticks

### 🗺️ 地图与位置关联
- 前端随机散布宗门和城市（坐标配对），保存地图时 `chosen_sect` 传给后端
- 后端 `save_map` 根据坐标匹配找到选中的宗门对应的城市，设为玩家出生点
- NPC 分布：核心NPC→选中宗门，其余按 proximity 列表扩散
- 检索 NPC 时按玩家当前位置过滤核心NPC

### 🧑/👩 建模人物按钮
- 按钮改为纯展示（不再触发建模 API）
- turn 响应中 `modeled_npcs` 字段动态渲染已建模NPC的点击卡片
- 建模触发仅保留 ⭐ 重要人物复选框

### 📝 叙事原则
- 交互推进三分层：短交互（闭环给结果）/ 有分量小场景（阶段性结果+延展）/ 大型事件（自由推进）
- 禁止"你准备怎么做""等你的回答"等反问句式
- NSFW 分为 Type 1（刺激插曲，一轮闭环）+ Type 2（情节性性爱，可跨轮次）

### 🔄 热重载
- `ANE_RELOAD=1` 环境变量启用 uvicorn reload
- `start_backend.bat` / `ane.sh` 启动前自动杀旧进程

