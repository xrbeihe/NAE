# ANE 部署指南

> **部署方式**：GitHub Actions + 自托管 Runner（self-hosted runner）
> **服务管理**：systemd（ane.service）
> **代码源**：https://github.com/xrbeihe/NAE

---

## 一、服务器信息

| 项目 | 当前值 |
|------|--------|
| 服务器商 | 阿里云 |
| 实例类型 | 轻量应用服务器（国际型） |
| 地域 | 中国香港 |
| 公网 IP | 47.82.109.156 |
| 端口 | 8002 |
| 配置 | 2 核 / 1GiB / 30GiB SSD |
| 带宽 | 200Mbps 峰值（BGP_NCO） |
| 流量 | 不限 |
| 费用 | ¥28/月 |
| 系统 | Ubuntu 22.04 |
| 状态 | ✅ 运行中（systemd 管理） |

---

## 二、首次部署流程

### 2.1 系统依赖

```bash
apt update && apt install -y git python3 python3-venv python3-pip
```

### 2.2 配置自托管 Runner

在 GitHub 仓库 → Settings → Actions → Runners → Add runner，选择 Linux，按提示执行。

Runner 安装目录建议：`/home/runner/actions-runner`

### 2.3 拉取代码 + 虚拟环境

Runner 首次执行 `actions/checkout` 后，代码会自动拉到 `/home/runner/actions-runner/_work/NAE/NAE/`。

在该目录下创建虚拟环境并安装依赖：

```bash
cd /home/runner/actions-runner/_work/NAE/NAE
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2.4 配置 API Key

```bash
echo 'DEEPSEEK_API_KEY=sk-xxx' > /home/runner/actions-runner/_work/NAE/NAE/.env
```

### 2.5 创建 systemd 服务文件

路径：`/etc/systemd/system/ane.service`

```ini
[Unit]
Description=ANE Backend
After=network.target

[Service]
Type=simple
User=runner
WorkingDirectory=/home/runner/actions-runner/_work/NAE/NAE/backend
EnvironmentFile=/home/runner/actions-runner/_work/NAE/NAE/.env
ExecStart=/home/runner/actions-runner/_work/NAE/NAE/.venv/bin/python -m ane.main
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=LANG=zh_CN.UTF-8
Environment=LC_ALL=zh_CN.UTF-8

[Install]
WantedBy=multi-user.target
```

```bash
# 启动服务
sudo systemctl daemon-reload
sudo systemctl enable ane.service
sudo systemctl start ane.service
```

### 2.6 验证

```bash
systemctl status ane.service
curl http://localhost:8002/api/health
```

---

## 三、部署流程（日常）

**只需 `git push`。** 提交到 main 分支后，GitHub Actions 自动执行：

1. **Test** — 在 GitHub 服务器上跑 pytest
2. **Deploy** — 测试通过后，自托管 Runner 在服务器本地执行：
   - `actions/checkout` — 拉取最新代码
   - `sudo systemctl restart ane.service` — 重启服务
   - 健康检查确认

---

## 四、运维命令

| 操作 | 命令 |
|------|------|
| 查看服务状态 | `systemctl status ane.service` |
| 查看实时日志 | `journalctl -u ane.service -f` |
| 查看最近日志 | `journalctl -u ane.service -n 100 --no-pager` |
| 重启服务 | `sudo systemctl restart ane.service` |
| 停止服务 | `sudo systemctl stop ane.service` |
| 启动服务 | `sudo systemctl start ane.service` |
| 查看端口 | `ss -tlnp \| grep 8002` |
| 端口占用处理 | `lsof -ti:8002 \| xargs kill -9` |

---

## 五、配置说明

### 5.1 config.json

位置：`backend/ane/config.json`

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8002
  },
  "llm": {
    "default_model": "deepseek:deepseek-v4-flash",
    "temperature": 0.8,
    "max_tokens": 8192
  }
}
```

### 5.2 环境变量

可通过 `.env` 文件覆盖：

```bash
export ANE_PORT=8002
export ANE_HOST=0.0.0.0
export ANE_SECRET_KEY=your-random-secret
```

---

## 六、工作流文件

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
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install pytest-asyncio aiosqlite
      - name: Run tests
        working-directory: backend
        run: |
          mkdir -p ../data
          python -m pytest ../tests/ -v --tb=short 2>&1 | tail -30

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
