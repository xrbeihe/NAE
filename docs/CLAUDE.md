# AI Narrative Engine (ANE) — AI Coding Guide

## Token 效率规则（每次改代码时遵守）

1. **先搜索，后读取**
   - 用 Grep 定位函数/类名，用 Glob 找文件
   - 只在确定需要修改时才 Read 目标文件
   - 禁止一次性 Read 所有 .py 文件

2. **只在需要时读规范**
   - 架构规范在 `AI Narrative Engine (ANE).txt`（约690行），用 Grep 检索相关段落
   - 不需要每次读全文
   - 常见关键词：`Phase 1` `Section 5.x` `Prompt Builder` `Event Bus`

3. **不要派 Explore/Plan 子代理做简单探索**
   - ANE 只有 ~38 个文件、~4500 行，Grep + Glob 足够
   - 子代理不共享上下文，会重复读文件

4. **查数据库看 dump，不要跑 Python 脚本查库**
   - 服务器启动后自动生成 `data/unpacked/ane_dump.txt`，包含所有表的数据
   - 想查看当前 session 数据（玩家属性、NPC 档案、对话历史、Prompt 记录等），
     直接 Read 这个 dump 文件，**不要尝试 import 模块或用 Python SQL 查库**
   - dump 每字段截断 200 字符，足以日常查证；需要完整数据直接读 SQLite

5. **修改范围要精准**
   - 一次只改一个模块
   - 改完 → 跑测试 → 确认通过 → 再改下一个

6. **禁止的行为**
   - 禁止 `cat` 或用 Bash 读文件（用 Read 工具）
   - 禁止并行启动 2+ 子代理读同一批文件
   - 禁止 Edit 后再 Read 验证（工具本身会保证写入正确）

## 项目结构速览

```
├── backend/               # 后端源码
│   └── ane/
│       ├── config.py              # 配置（从 config.json + .env 加载）
│       ├── main.py                # FastAPI 入口
│       ├── auth.py                # JWT 认证 + 密码哈希 + FastAPI 鉴权依赖
│       ├── game_engine.py         # 核心编排器（turn 处理管线）
│       ├── migrate_users.py       # 数据库迁移脚本
│       ├── database/
│       │   ├── engine.py          # 异步 SQLAlchemy 引擎
│       │   └── models.py          # ORM 模型（11 张表：users + 10 业务表，含开源共享/评分）
│       ├── modules/               # 15 个独立模块（全部单例）
│       │   ├── input_validator.py # 安全检查 + 意图分类 + 中文数字解析
│       │   ├── time_manager.py    # 时间推进 + Phase 1 内联 Scheduler
│       │   ├── narrative_constraints.py
│       │   ├── retrieval_engine.py # Active Set 构建（NPC + 位置层级）
│       │   ├── memory_manager.py   # 三层记忆（Conversation/Shortmemory/Longmemory）
│       │   ├── prompt_builder.py   # 唯一允许生成 Prompt 的模块
│       │   ├── model_adapter.py    # 6 个 LLM 适配器（deepseek, gemini, openai, claude, sensenova, ollama）
│       │   ├── output_parser.py    # JSON 提取 + state_change 校验
│       │   ├── event_bus.py        # 内存 Pub/Sub
│       │   ├── player_manager.py   # Player CRUD + 角色创建
│       │   ├── npc_manager.py      # NPC CRUD（无初始生成，按需创建）
│       │   ├── npc_modeler.py      # 结构化人物档案建模（90+字段，替代 HTEM）
│       │   ├── world_manager.py    # 世界区域 CRUD + 初始生成
│       │   └── pack_generator.py   # 一键生成世界观包 zip
│       ├── content/               # JSON 模板数据
│       │   ├── world_templates.json  # 宗门/城市模板
│       │   ├── player_templates.json # 角色创建选项
│       │   ├── npc_templates.json    # NPC 属性模板
│       │   └── json_loader.py
│       ├── api/                   # FastAPI 路由
│       │   ├── routes.py          # Session/Turn/NPC/记忆 API（认证保护）
│       │   ├── auth_routes.py     # 注册/登录/改密 API
│       │   ├── worldview_routes.py# 世界观工具链 + 开源共享/评分 API
│       │   └── schemas.py
│       └── tools/                 # 工具脚本
│           ├── nsfw_harvest.py     # NSFW 素材收割
│           └── portrait_harvest.py # 角色肖像收割
├── frontend/              # 前端 SPA（FastAPI 直接挂载）
│   ├── app.html           # 主应用（首页 NPC 总库 + 开源广场 + 聊天界面）
│   ├── login.html         # 登录/注册
│   ├── settings.html      # 用户设置（头像/密码/清空日志/设计器入口）
│   ├── designer.html      # 世界观设计器（/designer 路由）
│   └── public/
│       ├── common.js      # 共享工具函数（JWT、日志、颜色、NPC 格式化）
│       └── character.js   # 角色创建 + 世界观选择逻辑
├── tests/                 # 测试（pytest，152 个用例）
│   ├── conftest.py        # engine + db fixtures
│   ├── test_modules.py    # 单元测试
│   └── test_turn.py       # 集成测试
└── docs/
```

## 架构 11 原则（不可违反）

1. AI 不负责维护世界状态 —— 程序控制状态变化，AI 只负责讲述变化的过程。
2. AI 不直接修改数据库。
3. 数据库是唯一数据来源。
4. 程序负责规则、状态、计算。
5. AI 负责文学、描写、对话。
6. Prompt 永远保持最小化。
7. 所有长期信息来自数据库而非 Prompt。
8. 世界按需加载。
9. 世界时间由玩家行动驱动。
10. 每个模块职责单一，禁止直接耦合。
11. NPC 分为三类：重要（player ⭐）、offstage（llm_main 写入 DB）、background（一次性路人不入库）

> 注意：原则 1 不排斥开局时的静态模板数据（宗门列表、城市描述、NPC 名字池等）。
> 这些是程序搭舞台的材料，属于原则 4 的范畴，不是 AI 需要维护的"动态状态"。

## 修改检查清单

- [ ] 新代码是否引入了模块间直接耦合？
- [ ] 数据库修改是否经过 Event Bus？
- [ ] Prompt 是否由 Prompt Builder 生成？
- [ ] 是否添加了对应测试？
- [ ] `python -m pytest tests/ -v` 是否全部通过？

## 认证机制

- 所有 `/sessions/*` 端点需要 JWT 认证（`Authorization: Bearer <token>`）
- 用户密码以 `pbkdf2_sha256` 哈希存储，**不存明文**
- 每个用户只能操作自己的 session（API 按 `user_id` 过滤）
- JWT 密钥在 `config.json` 的 `secret_key` 字段，可用 `ANE_SECRET_KEY` 环境变量覆盖
- 详见 `backend/ane/auth.py`
