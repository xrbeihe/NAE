# 世界地图玩家标记与交互

## 玩家位置标记

地图上用一个 emoji 小人（`🧍‍♂️` / `🧍‍♀️`，根据玩家性别自动选择）表示当前所在地。

### 渲染层级（由下到上）

1. 地形（Simplex noise 生成）
2. 城市标签（碧水城等，固定位置）
3. 宗门标签（青云宗等，可拖拽查看下方城市）
4. **玩家标记**（最顶层，永远不被遮挡）

### 交互方式

| 操作 | 效果 |
|------|------|
| **拖拽小人** | 小人跟随鼠标移动，放手后自动找最近的宗门/城市 |
| **拖拽到城市/宗门** | 不弹确认，直接 `POST /move`，计算时间流逝，聊天区显示移动消息 |
| **拖拽到空地** | 附近无有效目标时，小人弹回原位 |
| **点击小人** | 聊天区显示 `📍 你当前在「XX」` |
| **点击宗门/城市标签** | 弹出确认框 → `POST /move` |
| **拖拽宗门标签** | 移动宗门图层，查看被挡住的下方城市标签 |

### 性别的数据流

```
后端 GET /sessions/{id}
  → SessionSummary.player_gender（从 player.attributes.gender 读取）
  → 前端 updateStatus() 存入 _playerMapGender 全局变量
  → WorldMapRenderer({ playerGender: _playerMapGender })
  → _renderPlayerMarker 选择 emoji：女→🧍‍♀️，其他→🧍‍♂️
```

### 防连点保护

- `worldmap.js` `_handleClick`：同一点位 800ms 内位置偏差 < 10px 时忽略
- `index.html` `_globalMoveLock`：`handleMapMove` 入口全局锁，一次只处理一个移动请求
- 拖拽移动跳过确认对话框（`skipConfirm: true`）

## 移动时间计算

拖拽/点击宗门城市 → `POST /sessions/{id}/move`

```
后端 POST /sessions/{id}/move
  → calc_travel_delta(from_x, from_y, to_x, to_y)
    → 像素距离 / MAP_WIDTH_LOGICAL(800) * TRAVEL_DAYS_ACROSS_MAP(30) 天
    → ticks = max(1, round(天数 * 24))
  → session.time_epoch += ticks → 更新 world_time
  → player.location = destination
  → 写入事实："玩家从X启程前往Y，耗时Z刻"
```

聊天消息格式：
> 🚶 你从碧水城启程前往青云宗，耗时 759 刻（约 31 天 15 小时）。当前时间：第1年·2月·2日·春·清晨

## 地图保存时自动定位

`_finalizeSaveMap()` 中保存地图后立即：
- `_playerMapLocation = chosenCity`
- `mapRenderer.playerLocation = _playerMapLocation`
- `mapRenderer.render()` 刷新标记

不再需要额外打开/刷新动作。

### 相关文件

| 文件 | 说明 |
|------|------|
| `frontend/public/worldmap.js` | Canvas 渲染器，所有交互逻辑 |
| `frontend/index.html` | `handleMapMove`、`_fetchPlayerLocation`、`_finalizeSaveMap` |
| `backend/ane/api/routes.py` | `POST /{session_id}/move` 端点 |
| `backend/ane/api/schemas.py` | `MoveRequest`、`SessionSummary.player_gender` |
| `backend/ane/modules/time_manager.py` | `calc_travel_delta`、`format_world_time` |
