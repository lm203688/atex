# ATEX 公网部署指南

## ⚠️ 重要发现

当前运行环境是清言 AgentMore 的托管 K8s 容器，**无法运行持久化后台服务**（进程会被自动回收）。因此需要将 ATEX API 部署到独立的云服务器上。

---

## 方案对比

| 方案 | 月费用 | 优点 | 缺点 |
|------|--------|------|------|
| **A. 阿里云 ECS** | ¥30-50 | 完全控制，一劳永逸 | 需要购买配置 |
| **B. 阿里云函数计算 FC** | ¥0-10（低流量） | 按量付费，免运维 | 需改代码适配，冷启动延迟 |
| **C. 其他免费VPS** | ¥0 | 免费 | 不稳定，速度慢 |

**推荐方案 A（阿里云 ECS）**——最简单直接，ATEX 需要持久运行。

---

## 方案 A：阿里云 ECS 部署（推荐）

### 第一步：购买 ECS 实例

1. 登录 [阿里云控制台](https://ecs.console.aliyun.com/)
2. 点击 **创建实例**
3. 选择配置：
   - **地域**：华东1（杭州）或 华北2（北京），选离你最近的
   - **实例规格**：`ecs.t6-c1m1.large`（2核2G）— 足够运行 ATEX
   - **镜像**：Ubuntu 22.04 64位
   - **存储**：系统盘 40G ESSD（默认即可）
   - **带宽**：按量付费，1-5Mbps
   - **付费模式**：按量付费（先试用）或 包年包月（更便宜）
4. **安全组**（关键！）：创建时选择自动创建安全组，稍后我们手动添加规则
5. 点击 **确认订单** → **创建实例**

> 💰 预估费用：按量付费约 ¥0.12/小时 = ¥3/天 ≈ ¥90/月；包年包月约 ¥30-50/月

### 第二步：配置安全组（开放端口）

1. 进入 ECS 控制台 → 左侧菜单 **安全组**
2. 找到刚创建的安全组，点击 **配置规则**
3. 点击 **手动添加** → **入方向**，添加以下规则：

| 协议 | 端口范围 | 授权对象 | 描述 |
|------|----------|----------|------|
| TCP | 22 | 0.0.0.0/0 | SSH远程登录 |
| TCP | 8420 | 0.0.0.0/0 | ATEX API服务 |

4. 点击 **保存**

> ⚠️ 如果你已有安全组，直接在现有安全组中添加 8420 端口规则即可。

### 第三步：连接 ECS

1. 在 ECS 实例列表，找到你的实例，记下 **公网IP**（如 47.xxx.xxx.xxx）
2. 点击 **远程连接** → 选择 **VNC远程连接**（或用本地 SSH 客户端）
3. 用创建实例时设置的密码登录

### 第四步：部署 ATEX

登录 ECS 后，依次执行：

```bash
# 1. 更新系统
sudo apt update && sudo apt upgrade -y

# 2. 安装 Python3 和 Git
sudo apt install -y python3 python3-pip git

# 3. 克隆 ATEX 仓库
cd /opt
sudo git clone https://github.com/lm203688/atex.git
cd atex/token_exchange

# 4. 测试运行
python3 api/server.py 8420 &
curl http://127.0.0.1:8420/api/v1/status
# 应该返回 ATEX 状态 JSON

# 5. 停止测试进程
kill %1
```

### 第五步：配置为系统服务（开机自启）

```bash
# 创建专用用户
sudo useradd -r -s /bin/false atex

# 设置权限
sudo chown -R atex:atex /opt/atex

# 创建 systemd 服务文件
sudo tee /etc/systemd/system/atex.service > /dev/null << 'EOF'
[Unit]
Description=ATEX Agent Service Exchange API
After=network.target

[Service]
Type=simple
User=atex
Group=atex
WorkingDirectory=/opt/atex/token_exchange
ExecStart=/usr/bin/python3 /opt/atex/token_exchange/api/server.py 8420
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 启用并启动服务
sudo systemctl daemon-reload
sudo systemctl enable atex
sudo systemctl start atex

# 检查状态
sudo systemctl status atex
```

### 第六步：验证公网访问

```bash
# 在 ECS 上测试
curl http://127.0.0.1:8420/api/v1/status

# 在你自己的电脑上测试（替换为你的 ECS 公网IP）
curl http://47.xxx.xxx.xxx:8420/api/v1/status
```

如果返回 ATEX 状态 JSON，**部署成功！**

---

## 部署后配置

### 更新 ATEX 服务市场数据

ECS 上的仓库是公开版本（示例数据），需要同步真实数据：

```bash
# 从当前环境导出数据（在 AgentMore 环境执行）
# 然后通过 SCP 上传到 ECS
scp services.json root@47.xxx.xxx.xxx:/opt/atex/token_exchange/services/
scp accounts.json root@47.xxx.xxx.xxx:/opt/atex/token_exchange/
scp orderbook.json root@47.xxx.xxx.xxx:/opt/atex/token_exchange/
```

> ⚠️ 注意：accounts.json 包含余额数据，传输后需重启服务

### 配置域名（可选）

如果有域名，可以：
1. 在域名 DNS 添加 A 记录指向 ECS 公网IP
2. 用 Caddy 自动配置 HTTPS：

```bash
sudo apt install -y caddy
# 编辑 /etc/caddy/Caddyfile
# atex.yourdomain.com {
#     reverse_proxy localhost:8420
# }
sudo systemctl restart caddy
```

---

## 常见问题

**Q: 安全组在哪里？**
A: ECS 控制台 → 左侧菜单 → 网络与安全 → 安全组

**Q: 忘记 ECS 登录密码？**
A: ECS 控制台 → 实例列表 → 更多 → 密码/密钥 → 重置实例密码

**Q: 8420 端口访问不了？**
A: 99% 是安全组没开。检查：①安全组入方向有 TCP 8420 规则 ②ECS 实例绑定了该安全组

**Q: 想省钱？**
A: 选包年包月 + 抢占式实例，最低约 ¥10/月。或考虑函数计算方案。

---

## 快速检查清单

- [ ] 购买 ECS 实例（Ubuntu 22.04，2核2G）
- [ ] 安全组开放 TCP 22 + 8420
- [ ] SSH 登录 ECS
- [ ] git clone ATEX 仓库
- [ ] 配置 systemd 服务
- [ ] 公网访问测试通过
- [ ] 同步真实数据
- [ ] （可选）配置域名 + HTTPS
