# ANE 部署指南

> 服务器信息
> - 地域：中国香港（阿里云轻量应用服务器）
> - 规格：2 vCPU / 1GiB 内存 / 30GiB 系统盘 / 200Mbps 峰值 BGP（非中国优化）
> - 公网 IP：47.82.109.156
> - 端口：8002
> - 系统：Ubuntu 22.04 LTS
> - 费用：¥28/月（不限流量）
> - 部署方式：nohup 后台运行（关 SSH 后仍持续服务）

---

## 一、部署流程

### 1.1 服务器初始化

```bash
# 购买阿里云轻量应用服务器（香港地域）
# 重置 root 密码 → 重启实例

# 允许 SSH 密码登录
sudo sed -i 's/PasswordAuthentication no/PasswordAuthentication yes/' /etc/ssh/sshd_config
sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
sudo systemctl restart sshd
sudo passwd root
```

### 1.2 系统依赖

```bash
apt update && apt install -y git python3 python3-venv python3-pip
```

### 1.3 拉取代码

```bash
cd /root
git clone https://github.com/xrbeihe/NAE.git
cd NAE
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 1.4 配置 API Key

```bash
echo 'DEEPSEEK_API_KEY=sk-xxx' > /root/NAE/.env
```

config.py 会从项目根目录的 `.env` 文件加载环境变量（`load_dotenv(ROOT_DIR / ".env")`）。

当前支持的 Provider 及其环境变量名：

| Provider | 环境变量 | config.json 中的 key |
|----------|---------|---------------------|
| OpenAI | `OPENAI_API_KEY` | provider 配置 |
| DeepSeek | `DEEPSEEK_API_KEY` | provider 配置 |
| SenseNova（商汤） | `SENSENOVA_API_KEY` | provider 配置 |
| Anthropic Claude | `ANTHROPIC_API_KEY` | provider 配置 |
| Google Gemini | `GEMINI_API_KEY` | provider 配置 |
| Ollama（本地） | 无需 Key | provider 配置 |

### 1.5 启动服务

```bash
cd /root/NAE/backend
source ../.venv/bin/activate

# 后台运行（关 SSH 后继续服务）
nohup python -m ane.main > /root/NAE/backend.log 2>&1 &
```

也可用 screen：

```bash
screen -S ane
cd /root/NAE/backend && source ../.venv/bin/activate && python -m ane.main
# Ctrl+A → D 断开 screen
# 恢复: screen -r ane
```

### 1.6 安全组（防火墙）

阿里云实例需要开放端口 8002：

```
控制台 → 实例 → 安全组 → 配置规则 → 入方向 → 添加规则
  协议类型: TCP
  端口范围: 8002
  授权对象: 0.0.0.0/0
```

### 1.7 验证

浏览器访问 `http://服务器IP:8002` → 注册账号 → 创建世界 → 发消息测试。

---

## 二、运维命令

| 操作 | 命令 |
|------|------|
| 启动服务 | `cd /root/NAE/backend && source ../.venv/bin/activate && nohup python -m ane.main > /root/NAE/backend.log 2>&1 &` |
| 查看日志 | `tail -f /root/NAE/backend.log` |
| 查看服务 | `ps aux \| grep ane.main` |
| 停止服务 | `fuser -k 8002/tcp` |
| 重启服务 | `fuser -k 8002/tcp && sleep 2 && cd /root/NAE/backend && source ../.venv/bin/activate && nohup python -m ane.main > /root/NAE/backend.log 2>&1 &` |
| 拉取更新 | `cd /root/NAE && git pull && systemctl restart ane` |
| 查看端口 | `ss -tlnp \| grep 8002` |

---

## 三、配置说明

### 3.1 config.json

位置：`backend/ane/config.json`

```json
{
  "server": {
    "host": "0.0.0.0",    // 监听所有网卡
    "port": 8002           // 服务端口
  },
  "llm": {
    "default_model": "deepseek:deepseek-v4-flash",
    "temperature": 0.8,
    "max_tokens": 8192
  }
}
```

也可通过环境变量覆盖：

```bash
export ANE_PORT=8002
export ANE_HOST=0.0.0.0
```

### 3.2 默认模型

当前默认模型为 `deepseek:deepseek-v4-flash`。前端模型下拉框也可手动切换为 `ollama:qwen2.5` 等本地模型（需在服务器上配置 Ollama）。

### 3.3 JWT Secret

```bash
export ANE_SECRET_KEY=your-random-secret
```

---

## 四、服务器信息

| 项目 | 当前值 |
|------|--------|
| 服务器商 | 阿里云 |
| 实例类型 | 轻量应用服务器（国际型） |
| 地域 | 中国香港 |
| 公网 IP | 47.82.109.156 |
| 端口 | 8002 |
| 配置 | 2 核 / 1GiB / 30GiB SSD |
| 带宽 | 200Mbps 峰值（BGP_NCO，非中国优化） |
| 流量 | 不限 |
| 费用 | ¥28/月 |
| 系统 | Ubuntu 22.04 |
| 状态 | ✅ 运行中 |

---

## 五、网络说明

### 5.1 线路类型

阿里云香港轻量默认使用 **BGP_NCO（国际型）** 线路，非中国优化线路。晚高峰时段从中国大陆访问可能有丢包。如需更好的中国大陆访问体验，建议：

- 使用 CN2 GIA 线路的 VPS（如恒创科技、HostDare、YYYhost 等）
- 或使用国内服务器+备案

### 5.2 安全

- 所有 `/sessions/*` 路由通过 JWT 认证保护
- 用户数据按 `user_id` 隔离
- API Key 通过 `.env` 文件配置，已加入 `.gitignore`，不会提交到代码仓库
- 建议生产环境更换 `secret_key`

### 5.3 后续计划

- [ ] 使用 Docker 容器化部署
- [ ] 添加 Nginx 反向代理 + SSL（HTTPS）
- [ ] 配置 CI/CD 自动部署
- [ ] 数据库自动备份
