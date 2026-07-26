
# ANE Platform — Multi-Worldview AI Narrative Engine

> 版本：v0.1（草案）
> 基于：ANE 修仙版 Phase 1 MVP（代码现状 2026-07-26）

---

## 1. 项目愿景

ANE 目前是一个修仙叙事引擎。ANE Platform 的目标是将其改造为一个**多世界观叙事平台**：
作者可以使用平台提供的工具和模板，**创作属于自己的世界观对话剧本**，而不用修改任何引擎代码。

作者只需要提供：
- Worldview 名称和描述
- 该世界观的 System Prompt
- 世界地图/场景模板（JSON）
- 名字池和身份模板（JSON）
- 角色创建模板
- 叙事约束规则

平台负责管线执行、记忆管理、NPC 建模、时间推进、Token 记账。

---

## 2. 与当前修仙版的关系

```
ANE Platform（基础平台）
  ├── core/                  ← 不随世界观改变的核心模块
  │     GameEngine, MemoryManager, NPC_MODELER, OutputParser,
  │     ModelAdapter, EventBus, TimeManager, HO 系统
  │
  ├── modules/               ← 因世界观而异的模块（按 worldview 加载）
  │     PromptBuilder, InputValidator, NarrativeConstraints,
  │     PlayerManager, NPCManager, WorldManager, RetrievalEngine
  │
  └── worldviews/            ← 世界观包（纯 JSON + 文本，不改代码）
        xianxia/             ← 当前修仙世界观（作为首个 reference worldview）
          system_prompt.txt
          world_templates.json
          npc_templates.json
          player_templates.json
          intent_keywords.json
          constraints.json
          name_pool.json
        modern_city/
          ...
        (更多作者自建)
```

**复制一份当前修仙版代码就是 xianxia 世界观包的起点。**

---

## 3. 世界观包（Worldview Pack）定义

一个世界观包 = 一个目录，包含以下全部内容。所有文件为**纯 JSON/文本**，不需要改代码。

### 必需文件

| 文件 | 用途 | 修仙版参考来源 |
|------|------|-------------|
| `manifest.json` | 版本号、名称、描述、作者、依赖 | 新建 |
| `system_prompt.txt` | 该世界观的完整 System Prompt | `prompt_builder.py` 的 `SYSTEM_PROMPT` 常量移出 |
| `world_templates.json` | 地图区域模板 | 当前 `content/world_templates.json`（42 宗门 + 30 城市） |
| `npc_templates.json` | 姓名池 + 身份池 + 性格池 | 当前 `content/npc_templates.json` |
| `player_templates.json` | 角色创建选项 | 当前 `content/player_templates.json` |
| `intent_keywords.json` | 意图分类关键词（修仙有"闭关/突破/修炼"等） | 当前 `input_validator.py` 的 `INTENT_PATTERNS` 常量移出 |
| `constraints.json` | 叙事硬约束 + 软约束 + 条件触发规则 | 当前 `narrative_constraints.py` 移出 |
| `events.json` | Scheduler 随机事件池 | 当前 `time_manager.py` 的内联事件 |

### 可选文件

| 文件 | 用途 | 修仙版参考 |
|------|------|-----------|
| `nsfw_templates.json` | NSFW 描写素材 | 当前 `content/nsfw_templates.json` |
| `ntr_templates.json` | NTR 心理素材 | 当前 `content/ntr_templates.json` |
| `underage_templates.json` | 未成年 NSFW 素材 | 当前 `content/underage_templates.json` |
| `portrait_templates.json` | 外貌描写参考 | 当前 `content/portrait_templates.json` |
| `system_prompt_nsfw.txt` | NSFW 模式下的 system prompt 补充 | 当前 `prompt_builder.py` 的 `NSFW_PROMPT` |
| `preview.json` | 世界观简介/封面/标签 | 平台展现用 |

### manifest.json 结构

```json
{
  "worldview_id": "xianxia_v1",
  "name": "修仙世界",
  "version": "1.0.0",
  "author": "ANE Team",
  "description": "经典东方玄幻修仙世界观",
  "base_map": "cultivation",
  "maturity_rating": "adult",
  "required_core_version": ">=0.1.0",
  "tags": ["xianxia", "cultivation", "chinese-fantasy"]
}
```

---

## 4. 核心改动（相对当前修仙版）

### 4.1 数据库 — 表变更

```sql
-- 新增：世界观注册表
CREATE TABLE worldviews (
    id          TEXT PRIMARY KEY,    -- worldview_id
    name        TEXT,
    version     TEXT,
    author      TEXT,
    description TEXT,
    path        TEXT,                -- worldviews/<id>/ 目录路径
    is_active   BOOLEAN DEFAULT 1,
    created_at  DATETIME
);

-- 新增：用户世界观偏好
ALTER TABLE users ADD COLUMN default_worldview TEXT DEFAULT 'xianxia_v1';

-- WorldSession 新增 worldview 字段
ALTER TABLE sessions ADD COLUMN worldview TEXT DEFAULT 'xianxia_v1';

-- WorldSession 新增 worldview_data 字段（存储该世界观特有数据）
ALTER TABLE sessions ADD COLUMN worldview_data JSON DEFAULT NULL;
```

### 4.2 模块改动

#### PromptBuilder — 世界观化

```python
# 当前：固定读 SYSTEM_PROMPT 常量
# 改成：按 worldview 加载 system_prompt.txt

class PromptBuilder:
    def __init__(self, worldview_id: str):
        self.worldview = load_worldview(worldview_id)
        self.system_prompt = self.worldview.load("system_prompt.txt")
        self.nsfw_prompt = self.worldview.load("system_prompt_nsfw.txt", optional=True)
```

#### InputValidator — 世界观化

```python
# 当前：固定的 INTENT_PATTERNS 列表
# 改成：加载 worldview 的 intent_keywords.json

def validate(user_input, worldview_id=None):
    keywords = load_intent_keywords(worldview_id or "xianxia_v1")
    # ... 同一套匹配逻辑，不同关键词
```

#### WorldManager — 通用化

```python
# 当前：generate_initial_world 写死了 SECTS/SETTLEMENTS
# 改成：按 worldview 加载 world_templates.json

async def generate_initial_world(db, session_id, worldview_id):
    templates = load_world_templates(worldview_id)
    for entry in templates["locations"]:
        # ... 创建 region，不再假定有"宗门""城市"概念
```

### 4.3 不变的模块（≈ 当前代码的 60%）

| 模块 | 不改的理由 |
|------|-----------|
| `game_engine.py` | 17 步管线—通用编排逻辑。注意：当前修仙版中 game_engine.py 仍有修仙特定关键词（如`cultivate` intent 被重试等），platform 版需做一轮通用化清理 |
| `memory_manager.py` | 三层记忆（compact/era/facts）完全通用 |
| `output_parser.py` | JSON 提取 + state_change 校验。已包含 json_repair 修复 |
| `model_adapter.py` | 6 个 Provider + Token 追踪 |
| `event_bus.py` | 纯路由 |
| `time_manager.py` | tick 推进 + 格式化 + Scheduler（格式化字符串配置化即可） |
| `json_loader.py` | 惰性加载 |
| `auth.py` | 认证通用 |
| `npc_modeler.py` | 90+ 字段建模通用（字段名可配置但保持兼容） |
| `HO 系统` | input_validator 的 HO 检测 + prompt_builder 的 NSFW 指令 + 前端提醒 — 完全通用，与世界观无关 |

### 4.4 前端改动

前端只需要轻量改动：

```html
<!-- 新增世界观选择器 -->
<select id="worldview-select">
  <option value="xianxia_v1">修仙世界</option>
  <option value="modern_city">现代都市</option>
</select>
```

API 侧：

```python
# 创建 session 时指定 worldview
POST /sessions { "worldview": "xianxia_v1" }

# 列出可用世界观
GET /worldviews → [{id, name, description, tags}]
```

---

## 5. 管线流程对比

### 当前修仙版管线

```
创建 Session → 生成世界区域（sects+settlements — 42 宗门 + 30 城市）
            → NPC 不做预生成
            → 用户进入角色创建（player_templates.json）
            → 地图选择宗门+城市
```

### ANE Platform 管线

```
创建 Session → 选择 worldview
            → 按 worldview 加载 world_templates.json → 生成区域
            → 按 worldview 加载 player_templates.json → 角色创建
            → 进入 turn 循环
              → InputValidator 按 worldview 加载意图关键词
              → PromptBuilder 按 worldview 加载 System Prompt
              → NarrativeConstraints 按 worldview 加载约束
              → NPC_MODELING 按 worldview 加载模型字段定义（可选）
              → LLM 调用（不变）
```

---

## 6. 世界观创作工作流（作者视角）

### 创建一个新的世界观

```
1. 复制 worldviews/xianxia/ 为 worldviews/my_world/
2. 修改 manifest.json（名称、作者、标签）
3. 修改 system_prompt.txt（世界观描述、叙事风格）
4. 修改 system_prompt_nsfw.txt（如果需要）
5. 修改 world_templates.json（地图/场景）
6. 修改 npc_templates.json（名字池）
7. 修改 player_templates.json（角色创建）
8. 修改 intent_keywords.json（意图词）
9. 修改 constraints.json（叙事约束）
   ── 全改完，不需要写一行 Python
```

### 发布与分发

世界观包 = 一个目录，可压缩为 zip/目录 分发：

```
my_world.zip
  manifest.json
  system_prompt.txt
  system_prompt_nsfw.txt（可选）
  world_templates.json
  npc_templates.json
  player_templates.json
  intent_keywords.json
  constraints.json
  nsfw_templates.json（可选）
```

平台提供 `POST /worldviews/upload` 接口接收 zip → 解压到 `worldviews/<id>/` → 注册到数据库。

---

## 7. 工作量估算

| 阶段 | 内容 | 文件数 | 代码量 | 可并行 |
|------|------|--------|--------|--------|
| P0 | 修仙版代码现状整理（确保复制后可直接运行） | — | — | — |
| P1 | worldview 目录结构 + loader 模块 | 3 个新文件 | ~150 行 | — |
| P2 | PromptBuilder 世界观化 | 1 个文件 | ~50 行 | — |
| P3 | InputValidator 世界观化 | 1 个文件 | ~30 行 | — |
| P4 | WorldManager + NPCManager 通用化 | 2 个文件 | ~80 行 | ✅ P2-P6 |
| P5 | 前端世界观选择器 | 1 个文件 | ~40 行 | ✅ |
| P6 | API 端点（worldviews CRUD） | 2 个文件 | ~100 行 | ✅ |
| P7 | 将当前修仙内容抽出为 xianxia_v1 包 | 新建 8 个文件 | 纯 JSON | ✅ |
| **合计** | | **~10 个新/改文件** | **~450 行** | |

---

## 8. 不做的边界

| 不做 | 原因 |
|------|------|
| 作者自由编辑地图（可视化编辑器） | 超出 MVP 范围，作者直接改 JSON |
| 多世界观的 NPC/记忆互访 | 不同世界观物理隔离 |
| 世界观市场/社区商店 | 超出 MVP 范围 |
| 实时世界观热切换 | 一个 Session 绑定一个世界观 |
| 作者自定义 LLM 模型 | 使用平台统一模型配置 |
