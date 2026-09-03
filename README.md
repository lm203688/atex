# 真测 Realcast

合规版积分预测社区（类 Manifold × Good Judgment Open × Kalshi 信息呈现）。
FastAPI + SQLite，积分**只送不卖 / 不可流通 / 不可回兑 / 彻底去加密**，合规优先。

## 合规红线（贯穿全系统，不可破）
1. 积分只送不卖（禁人民币买积分）
2. 积分不可用户间流通
3. 积分不可回兑现金（商城单向换实物/虚拟权益）
4. 彻底去加密货币
5. 结算用「平台奖励池」替代「赢家通吃输家筹码」，规避赌博三要素
6. 台港澳属中国，不得当外国政治；国外政治仅大选季引流且需人工复核
7. 数据出售必须匿名化（PIPL）
8. 人工兜底（广告审核 / 敏感选题 / 群体投诉 / Oracle 冲突）

## 部署
见 `deploy/`：`Dockerfile` / `docker-compose.yml` / `nginx.conf` / `.env.example` /
`realcast.service`(systemd) / `backup.sh`(热备) / `healthcheck.sh` / `部署运维手册.md`。

```bash
cd deploy && cp .env.example .env   # 填 ADMIN_TOKEN / CORS_ORIGINS
docker compose up -d --build
```

### Railway（海外部署，绕过 ICP 备案）

Railway 提供持久化卷，SQLite 可直接用，无需改 Postgres。配置文件已就绪：
`railway.json` + `deploy/Dockerfile.railway`（构建上下文为**仓库根目录**，与
`deploy/Dockerfile` 不同，别混用）。

控制台操作步骤：
1. New Project → Deploy from GitHub Repo，选本仓库
2. Settings → Volumes → **Add Volume**，挂载路径填 `/data`
   （不挂卷 = 容器每次重建数据归零；库目录不存在时应用会**拒绝启动**并打印排查提示）
3. Variables 里设置：

   | 变量 | 值 | 说明 |
   |---|---|---|
   | `ADMIN_TOKEN` | 长随机串（≥32 位） | 运营后台/审核/结算必填 |
   | `CORS_ORIGINS` | 你的 Railway 域名 | 不要留 `*` |
   | `DB_PATH` | `/data/platform.db` | 镜像已默认，与卷挂载点对应 |
   | `APP_TZ` | `8`（国内）/ `0`（UTC） | 决定「今天是哪一天」的边界 |
   | `UVICORN_WORKERS` | `1` | **不可 >1**，原因见下 |
   | `SEED_ON_BOOT` | `0` | 演示环境可设 `1` 自动播种（幂等） |
   | `REDIS_URL` | 可选 | 配了才能安全扩容到多 worker |

4. 部署后访问 `https://<你的域名>/api/health`，确认 `status=ok`

**为什么 `UVICORN_WORKERS` 必须是 1**：未配 `REDIS_URL` 时实时广播走进程内
`MemoryBackplane`（`app/core/backplane.py`），一个 worker 就是一个独立广播域。
worker > 1 时，A 进程的市场状态变更推不到连在 B 进程上的客户端，不同用户看到的
概率会不一致。要扩容必须先配 `REDIS_URL` 切到 `RedisBackplane`，并建议同时迁 Postgres。

**为什么 `overlapSeconds = 0`**：Railway 默认在新旧容器间做短暂重叠实现零停机，
但重叠窗口内两个进程会同时写同一个 SQLite 文件，轻则锁竞争、重则损坏数据。
设为 0 = 先停旧再起新，代价是每次部署几秒中断。

## 质量门禁
```bash
cd app && python tests/smoke.py --fresh   # 期望 99/99 全绿
cd app && python tests/uat_customer.py    # 期望 90/90 全绿
```

> 提示：本机若设了 `HTTP_PROXY`，测试脚本访问 `127.0.0.1` 会被代理拦截（表现为 502），
> 运行时请加 `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy` 清除。

## 安全边界（CSP nonce）
`script-src`（v0.7.1 起）与 `style-src`（v0.7.5 起）均已去掉 `'unsafe-inline'`，
改为每请求生成的一次性 nonce。nonce 只能由后端在渲染 HTML 时注入，因此：
- SPA 首页**必须**由 FastAPI 渲染下发（`_render_index`），不能由 nginx 等静态托管；
- CSP 头**只能**由应用层下发，nginx 不得重复下发（见 `deploy/nginx.conf` 文件头说明）。

## 上线硬性前提（未满足不得公开运营）
- 服务器 ICP 备案（腾讯云）
- 律所合规意见书
