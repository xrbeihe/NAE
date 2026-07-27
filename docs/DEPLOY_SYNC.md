# Deploy Sync Guide — 代码同步与部署指南

## 部署方式

**GitHub Actions 自动部署**（仅此方式）：`git push` → GitHub Actions 测试 + 自托管 Runner 部署

不再支持手动 SSH 部署。

## 工作流流程

每次推送到 `main` 分支：

1. **test job** — 在 GitHub 云服务器上运行 pytest
2. **deploy job** — 测试通过后，在服务器的自托管 Runner 上执行：
   - `actions/checkout` — 拉取最新代码到 `_work/` 目录
   - `sudo systemctl restart ane.service` — 重启后端
   - `curl -sf http://localhost:8002/api/health` — 健康检查

## 相关文件

| 文件 | 位置 |
|------|------|
| 工作流 | `.github/workflows/ci.yml` |
| 服务配置 | `/etc/systemd/system/ane.service` |
| 部署文档 | `docs/DEPLOY.md` |
