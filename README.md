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

## 质量门禁
```bash
cd app && python tests/smoke.py --fresh   # 期望 97/97 全绿
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
