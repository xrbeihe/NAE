# CI/CD — 持续集成与自动部署

> 基于 GitHub Actions，每次 push 到 `main` 分支时自动运行。

---

## 流程概览

```
你 git push
    ↓
GitHub Actions 触发
    ↓
┌─ Job 1: test ──────────────────────────────────────┐
│ 启动 Ubuntu 临时服务器                               │
│ 安装 Python 3.12 + 项目依赖                         │
│ 运行 pytest（全部 90+ 测试）                        │
│                                                      │
│ ❌ 测试失败 → 红色 ❌，流程停止，不发部署              │
│ ✅ 测试通过 → 绿色 ✅，自动进入 Job 2                 │
└──────────────────────────────────────────────────────┘
    ↓ (test 通过)
┌─ Job 2: deploy ─────────────────────────────────────┐
│ SSH 登录服务器 (admin@47.82.109.156)                 │
│ cd /home/admin/NAE && git pull                      │
│ cp frontend/index.html → backend/ane/static/         │
│ 杀掉旧进程 → 后台重启 nohup python -m ane.main       │
│ sleep 2 → curl http://127.0.0.1:8002/api/health 验证  │
│                                                      │
│ 验证失败 → 红色 ❌（部署有问题）                      │
│ 返回 ok → 绿色 ✅（部署成功）                         │
└──────────────────────────────────────────────────────┘
```

---

## 配置文件

位置：`.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: [main]     # push 到 main 时触发
  pull_request:
    branches: [main]     # PR 到 main 时触发（不部署）

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: pip install pytest-asyncio aiosqlite
      - working-directory: backend
        run: |
          mkdir -p ../data
          python -m pytest ../tests/ -v --tb=short

  deploy:
    needs: test                              # 等待 test 通过
    if: github.ref == 'refs/heads/main'      # 仅 main 分支
    runs-on: ubuntu-latest
    steps:
      - uses: appleboy/ssh-action@v1.0.3
        with:
          host: 47.82.109.156
          username: admin
          password: ${{ secrets.SERVER_PASSWORD }}   # GitHub Secrets 配置
          script: |
            cd /home/admin/NAE
            git pull
            cp frontend/index.html backend/ane/static/index.html
            fuser -k 8002/tcp 2>/dev/null
            sleep 1
            cd backend && source ../.venv/bin/activate
            export LANG=zh_CN.UTF-8 LC_ALL=zh_CN.UTF-8
            nohup python -m ane.main > backend.log 2>&1 &
            sleep 2
            curl -s http://127.0.0.1:8002/api/health
```

---

## 如何查看 CI 结果

| 方式 | 操作 |
|------|------|
| **GitHub 仓库页** | 打开 `https://github.com/xrbeihe/NAE` → 顶部绿色 ✅ / 红色 ❌ |
| **Actions 标签** | 仓库顶部 → **Actions** → 看到所有运行记录 |
| **具体日志** | 点进某次运行 → 点 failed/success 的 job → 展开步骤看控制台输出 |

---

## 部署前提

服务器密码存放在 GitHub Secrets 中（不在代码里）：

1. 打开 `https://github.com/xrbeihe/NAE/settings/secrets/actions`
2. 点 **New repository secret**
3. Name: `SERVER_PASSWORD`
4. Secret: 服务器 admin 的 SSH 密码

如果密码变了，更新 Secret 即可，不需要改代码。

---

## 排错

| 症状 | 可能原因 |
|------|---------|
| test ❌ | 有测试失败 → 点进 Actions 看具体哪个测试报错 |
| deploy ❌（test ✅） | SSH 连接失败、密码不对、服务器磁盘满 |
| deploy ❌ 但 test ✅ | 服务器上 Python 包缺失、端口被占用、git pull 冲突 |
| 页面没更新 | GitHub Secrets 里密码过期或没配置 |
