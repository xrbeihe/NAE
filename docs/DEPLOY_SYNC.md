# Deploy Sync Guide — 代码同步与部署指南

当前部署方式：
- **CI/CD 自动部署**（推荐）：git push → GitHub Actions 自动测试 + 部署
- **手动部署**：本地构建 → git push → 服务器运行以下命令

## 服务器手动更新命令（SSH 或直接终端执行）

```bash
cd /home/admin/NAE
git pull
cp frontend/index.html backend/ane/static/index.html
cp frontend/public/worldmap.js backend/ane/static/worldmap.js
fuser -k 8002/tcp 2>/dev/null
sleep 1
cd backend && source ../.venv/bin/activate
export LANG=zh_CN.UTF-8 LC_ALL=zh_CN.UTF-8
nohup python -m ane.main > backend.log 2>&1 &
```

## 查看运行状态

```bash
tail -60 ~/NAE/backend/backend.log
curl -L http://aneplatform.top/api/health
```
