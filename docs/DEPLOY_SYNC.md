# Deploy Sync Guide — 代码同步与部署指南

当前服务器部署方式：本地代码 → git push → 服务器 git pull + 重启

## 快速本地提交与服务器同步

```bash
# ==== 本地变动处理 ====
cd D:\ANE
git add .
git commit -m "describe changes"
git push origin main

# ==== 服务器更新（SSH 执行）====
cd /home/admin/NAE && git pull && cp frontend/index.html backend/ane/static/index.html && fuser -k 8002/tcp 2>/dev/null && sleep 1 && cd backend && source ../.venv/bin/activate && export LANG=zh_CN.UTF-8 LC_ALL=zh_CN.UTF-8 && nohup python -m ane.main > backend.log 2>&1 &
```

## 服务器直接命令（VNC 终端执行）

```bash
cd /home/admin/NAE && git pull && cp frontend/index.html backend/ane/static/index.html && fuser -k 8002/tcp 2>/dev/null && sleep 1 && cd backend && source ../.venv/bin/activate && export LANG=zh_CN.UTF-8 LC_ALL=zh_CN.UTF-8 && nohup python -m ane.main > backend.log 2>&1 &
```

## 查看运行状态

```bash
tail -60 ~/NAE/backend/backend.log
curl -L http://aneplatform.top/api/health
```
