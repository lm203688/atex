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
cd app && python tests/smoke.py --fresh   # 期望 63/63 全绿
```

## 上线硬性前提（未满足不得公开运营）
- 服务器 ICP 备案（腾讯云）
- 律所合规意见书
