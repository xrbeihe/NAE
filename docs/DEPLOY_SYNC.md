# Deploy Sync Guide — 代码同步与部署指南

## 部署方式

**GitHub Actions 自动部署**（仅此方式）：`git push` → GitHub Actions 测试 + 自托管 Runner 部署

不再支持手动 SSH 部署。

## 工作流流程

每次推送到 `main` 分支：

1. **test job** — 在 GitHub 云服务器上运行 pytest
2. **deploy job** — 测试通过后，在服务器的自托管 Runner 上执行：
   - ⚠️ **部署保护（前置）已暂时停用**（`9b5cf77`，恢复见 `af599de`）：原来 `actions/checkout` 之前会检测服务器 worktree 是否有未提交改动（网页编辑器直接写磁盘的包文件，如 `world_facts.json` / `system_prompt.txt` / `ui.json`），有改动则打包备份为 artifact `ane-server-edits-backup` 并**中止部署**，防止 checkout 静默覆盖手改内容。**当前已移除**——服务器上未提交的网页改动会被 checkout 直接覆盖，请务必在网页编辑后手动 commit + push。
   - `actions/checkout` — 拉取最新代码到 `_work/` 目录
   - `sudo systemctl restart ane.service` — 重启后端
   - `curl -sf http://localhost:8002/api/health` — 健康检查

## 服务器端编辑内容如何同步（双向）

网页编辑器（designer 的权威设定/文案/选项等）把内容直接写进服务器磁盘的包文件，**不在 git 内**。要让它进版本库并同步到本地：

```bash
cd /home/runner/actions-runner/_work/NAE/NAE
git add -A
git commit -m "描述改动"
git push origin main          # 需配置 HTTPS 凭证（PAT），首次会询问 Username/Password
```

推送后：部署自动触发（此时 worktree 已提交 → 保护检测干净 → 正常部署）；本地执行 `git pull` 即可同步。

> 注意：图片库数据（`data/images/` 文件 + `image_categories`/`user_images` 表）是**运行时数据**，不在 git 管理范围，无需推送也不受部署影响。若保护恢复后被中止（忘了 commit 就 push 部署），先下载 artifact `ane-server-edits-backup` 找回内容，再按上面流程 commit + push。

## 相关文件

| 文件 | 位置 |
|------|------|
| 工作流 | `.github/workflows/ci.yml` |
| 服务配置 | `/etc/systemd/system/ane.service` |
| 部署文档 | `docs/DEPLOY.md` |
