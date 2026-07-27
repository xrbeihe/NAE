# 手机端前端适配说明

> 基于 `frontend/index.html`，两个媒体查询断点：≤768px（平板/大屏手机）、≤480px（小屏手机）。

---

## 布局架构

```
body (100vh, flex-col)
  #header (flex-shrink: 0)
  #app-main (flex: 1, flex-col)
    #main-layout (flex: 1, overflow: hidden)
      #chat-panel (flex: 1, min-height: 0, flex-col)   ← 自适应核心
        #session-bar (flex-shrink: 0, flex-wrap)         ← 🎨 模型/颜色按钮
        #chat (flex: 1, min-height: 0, overflow: auto)   ← 聊天区，可滚动
        #rec-area (flex-shrink: 0)                       ← 推荐行动 / ❤️ 🚻
        #input-area (flex-shrink: 0)                     ← 输入框 + 勾选 + 发送
```

关键：`#chat-panel` 和 `#chat` 都设 `min-height: 0`，flex 才能在屏幕高度不足时正常收缩聊天区，header/rec/input 始终固定在可视范围内，不会溢出。

---

## ≤768px 响应式

| 元素 | 变化 |
|---|---|
| `#header` | `flex-wrap`，字号缩小 |
| `#main-layout` | `flex-direction: column`（侧栏 → 顶部抽屉） |
| `#chat-panel` | `min-height: 0`（非 50vh） |
| `#rec-grid` | `display: none` |
| `#rec-label` + `#rec-count` | `display: none` |
| `#rec-mobile-btn` | `display: inline-flex`（💡 推荐 N 按钮） |
| `#help-btn` ❤️ | 正常显示，不隐藏 |
| `#model-btn` / `#color-btn` | 字号缩小 |

推荐行动在手机端显示为 `💡 推荐(N)` 按钮（替代桌面端的 3 卡片 + 标签）。

---

## ≤480px 小屏

| 元素 | 变化 |
|---|---|
| header `⏳ 📍` | `display: none` |
| `#session-id-display` | `display: none` |
| 侧栏 `max-height` | 35vh（不遮挡聊天区） |

---

## 颜色设置

🎨 按钮在 `#session-bar` 中，手机端没被隐藏。调色浮窗是 `position: fixed`，触摸正常。颜色存在 `localStorage`，桌面/手机共用。

## 帮助说明

❤️ 按钮在推荐行动右侧，点击弹出 `position: fixed` 浮窗说明 ⭐ 和 📦 的作用。全屏点击关闭。

## 侧栏

手机端侧栏走 `position: fixed` + `translateX(-100%)` 抽屉式，☰ 按钮切换。`#sidebar-overlay` 半透明蒙版层。
