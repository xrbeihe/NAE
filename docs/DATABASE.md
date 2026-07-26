# 数据库表结构

> 文档位置：`backend/ane/database/models.py`

## 表总览

8 张业务表 + `npc_relationships`（关系网）：

| 表名 | 模型名 | 说明 |
|------|--------|------|
| `users` | User | 注册用户 |
| `sessions` | WorldSession | 游戏世界（一个用户可创建多个） |
| `players` | Player | 玩家角色（每个世界一个） |
| `npcs` | NPC | 世界中的 NPC |
| `npc_relationships` | NPC_Relationship | **关系网边表（新增）** |
| `world_regions` | WorldRegion | 地点/区域层级树 |
| `event_logs` | EventLog | 状态变更事件日志 |
| `facts` | Fact | 永久事实（永远不裁剪） |
| `memories` | Memory | 对话/摘要/推荐 三层记忆 |

---

## NPC_Relationship 表

```sql
id            TEXT PRIMARY KEY     -- UUID hex[:12]
session_id    TEXT NOT NULL         -- FK → sessions.id
source_id     TEXT                  -- FK → npcs.id (nullable, 非NPC实体可为空)
source_name   TEXT NOT NULL         -- 发起方姓名（始终有值）
target_id     TEXT                  -- FK → npcs.id (nullable)
target_name   TEXT NOT NULL         -- 接收方姓名（始终有值）
rel_type      TEXT NOT NULL         -- 关系类型，支持多重（如 "母亲/师父"）
description   TEXT DEFAULT ''       -- 关系详细描述
affinity      INTEGER DEFAULT 0     -- 亲密度 -100~+100
updated_at    DATETIME              -- 最后更新时间
```

### 关系类型

支持 `/` 分隔的多重标签，如 `"母亲/师父"`。常用类型：

- 师徒 / 师兄弟 / 师姐妹
- 夫妻 / 配偶 / 恋人 / 道侣
- 姐妹 / 兄弟 / 母女 / 父子
- 仇敌 / 敌人 / 宿敌 / 竞争者
- 朋友 / 知己
- 合作 / 依附 / 交易
- 上级 / 下属
- 抢夺 / 夺宝

### 设计说明

- 一条边 = 一对 `(source, target)` 的有向关系。双向关系需要两条边。
- 关系不自动删除——只通过 LLM 增量更新中的覆盖来改变。
- `source_id`/`target_id` 可为空：因为可以存在非 NPC 实体（如未创建 NPC 记录的角色、组
 
