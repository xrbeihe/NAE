# CI/CD — 持续集成与自动部署
# 禁止擅自推送或者git操作，用户可能会放权允许agent行为，但是agent禁止擅自推送或者git操作，执行前需停止行为询问用户
> 基于 GitHub Actions + 自托管 Runner，每次 push 到 `main` 分支时自动运行。

---

## 流程概览

```
你 git push
    ↓
GitHub Actions 触发
    ↓
┌─ Job 1: test ───────────────────────────────────────────┐
│ 启动 Ubuntu 临时服务器                                    │
│ 安装 Python 3.12 + 项目依赖                              │
│ 运行 pytest（全部 90+ 测试）                             │
│                                                           │
│ ❌ 测试失败 → 红色 ❌，流程停止，不发部署                   │
│ ✅ 测试通过 → 绿色 ✅，自动进入 Job 2                      │
└───────────────────────────────────────────────────────────┘
    ↓ (test 通过 + push 到 main)
┌─ Job 2: deploy ──────────────────────────────────────────┐
│ 自托管 Runner 在服务器本地执行：                           │
│ actions/checkout → 拉取最新代码到 _work/                   │
│ sudo systemctl restart ane.service → 重启后端             │
│ curl http://127.0.0.1:8002/api/health → 健康检查验证      │
│                                                           │
│ 验证失败 → 红色 ❌（部署有问题）                           │
│ 返回 ok → 绿色 ✅（部署成功）                              │
└───────────────────────────────────────────────────────────┘
```

---

## 配置文件

位置：`.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

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
    needs: test
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v4
      - name: Restart ANE service
        run: |
          sudo systemctl restart ane.service
          sleep 3
          systemctl is-active ane.service
      - name: Verify
        run: |
          curl -sf http://localhost:8002/api/health
```

---

## 如何查看 CI 结果

| 方式 | 操作 |
|------|------|
| **GitHub 仓库页** | 打开 `https://github.com/xrbeihe/NAE` → 顶部绿色 ✅ / 红色 ❌ |
| **Actions 标签** | 仓库顶部 → **Actions** → 看到所有运行记录 |
| **具体日志** | 点进某次运行 → 点 failed/success 的 job → 展开步骤看控制台输出 |

---

## 排错

| 症状 | 可能原因 |
|------|---------|
| test ❌ | 有测试失败 → 点进 Actions 看具体哪个测试报错 |
| deploy ❌（test ✅） | 自托管 Runner 未运行或掉线 |
| deploy ❌（ane.service 重启失败） | 端口被占用 → 服务器执行 `lsof -ti:8002 \| xargs kill -9` 后重试 |
| deploy ❌（健康检查失败） | 后端启动异常 → 服务器查看 `journalctl -u ane.service -n 50 --no-pager` |
| Runner 不在线 | 检查服务器上 runner 服务状态 |

---

## 服务器运维参考

详见 [DEPLOY.md](DEPLOY.md) 和 [DEPLOY_SYNC.md](DEPLOY_SYNC.md)。
