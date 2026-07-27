# ANE 更新同步方案

## 问题

本地代码修改后，需要同步到香港服务器。GitHub HTTPS 443 端口在本地网络下不稳定（间歇性被墙），导致 `git push` 频繁失败。同时 `git pull` 后需要手动 `cp` 前端文件并重启服务。

---

## 方案一：SSH 推送（推荐，当前已配置成功）

### 配置 SSH

```bash
# 在本地 Git Bash / PowerShell 执行
# 如果你还没有 SSH key
ssh-keygen -t ed25519 -C "your_email@example.com"

# 复制公钥
cat ~/.ssh/id_ed25519.pub
# 把输出粘贴到 GitHub → Settings → SSH and GPG keys → New SSH key

# 将远程地址改为 SSH 协议（已配好）
git remote set-url origin git@github.com:xrbeihe/NAE.git
```

### 完整更新流程

```bash
# ========== 本地 ==========
cd /d D:\ANE

# 1. 改代码...

# 2. 提交并推送
git add .
git commit -m "描述改动"
git push

# ========== 服务器（VNC 终端）==========
# 3. 一行命令同步并重启
cd /home/admin/NAE && git pull && cp frontend/index.html backend/ane/static/index.html && fuser -k 8002/tcp 2>/dev/null && sleep 1 && cd backend && source ../.venv/bin/activate && export LANG=zh_CN.UTF-8 LC_ALL=zh_CN.UTF-8 && nohup python -m ane.main > backend.log 2>&1 &
```

---

## 方案二：服务器端直接修改（紧急）

不需要经过 GitHub，直接在服务器上改代码并重启：

```bash
ssh admin@47.82.109.156
# 或通过 VNC 登录

# 修改文件
vim /home/admin/NAE/backend/ane/static/index.html
# 或编辑其他文件...

# 重启服务
fuser -k 8002/tcp 2>/dev/null
sleep 1
cd /home/admin/NAE/backend
source ../.venv/bin/activate
export LANG=zh_CN.UTF-8 LC_ALL=zh_CN.UTF-8
nohup python -m ane.main > backend.log 2>&1 &
```

---

## 方案三：部署脚本（自动化）

创建一个部署脚本，一键完成：

### 本地：`deploy.bat`

```batch
@echo off
cd /d D:\ANE
git add .
git commit -m "%1" || echo "nothing to commit"
git push
echo "✅ 代码已推送到 GitHub"
echo "请登录 VNC 执行: cd /home/admin/NAE && git pull && cp frontend/index.html backend/ane/static/index.html && fuser -k 8002/tcp 2>/dev/null && sleep 1 && cd backend && source ../.venv/bin/activate && export LANG=zh_CN.UTF-8 LC_ALL=zh_CN.UTF-8 && nohup python -m ane.main > backend.log 2>&1 &"
```

### 服务器：`/home/admin/NAE/deploy.sh`

```bash
#!/bin/bash
cd /home/admin/NAE
git pull
cp frontend/index.html backend/ane/static/index.html
fuser -k 8002/tcp 2>/dev/null
sleep 1
cd backend
source ../.venv/bin/activate
export LANG=zh_CN.UTF-8 LC_ALL=zh_CN.UTF-8
nohup python -m ane.main > backend.log 2>&1 &
echo "✅ 部署完成"
```

赋予执行权限并运行：

```bash
chmod +x /home/admin/NAE/deploy.sh
/home/admin/NAE/deploy.sh
```

---

## 注意事项

| 问题 | 解决方案 |
|------|----------|
| GitHub 443 端口被封 | 改用 SSH 协议推送（`git@github.com:xrbeihe/NAE.git`） |
| 前端修改不生效 | `git pull` 后必须执行 `cp frontend/index.html backend/ane/static/index.html` |
| Node.js v12 无法跑 `vite build` | 临时用 `cp` 手动复制，后续建议升级 Node.js：`curl -fsSL https://deb.nodesource.com/setup_20.x \| sudo -E bash - && sudo apt install -y nodejs` |
| 编码问题（UnicodeEncodeError） | 启动前设置 `export LANG=zh_CN.UTF-8 LC_ALL=zh_CN.UTF-8` |
| 端口冲突 | 启动前 `fuser -k 8002/tcp` 杀掉旧进程 |
| 服务意外停止 | `ps aux \| grep ane.main` 查看进程；`tail -f backend.log` 查看日志 |
