# 真测 Realcast（积分制 · 合规 · 非现金）

类 Polymarket 的**积分版真实预测游戏（真测 Realcast）**：用户用免费积分参与预测，靠"声誉加权社区共识 + 严格评分"产生真实概率，
积分商城单向兑换好礼；平台靠广告与匿名化群体情绪数据（B2B）变现。**不涉及现金赌博、不涉及加密货币。**

## 核心设计（合规优先）
- **四条红线系统级落地**：积分只送不卖 / 不可用户间流通 / 不可回兑现金 / 彻底去加密货币。
- **平台奖励池替代"赢家通吃输家筹码"**：输家积分作 sink，赢家从平台出资的奖励池获得加成，规避赌博三要素。
- **选题白名单 + 主权红线**：台港澳属中国，不得当外国政治；政治选举/敏感公共事件禁入。
- **Oracle 结算 + 争议通道**：结算以权威源为准，可复核。
- **人工兜底不可删**：广告审核、敏感选题、群体投诉、Oracle 冲突仍升级人工（轻量审批闸门）。

## 准确性引擎（Metaculus 范式，解决免费积分噪声）
- 社区概率按**声誉加权**聚合（高手权重更高），非简单投票占比。
- 结算奖励 = 本金加成 + **Brier 严格评分加成**（奖励校准、惩罚过度自信）。
- 用户仪表盘展示**命中率 / 平均 Brier / 校准曲线**。

## 架构
```
app/
  db.py                SQLite（WAL+timeout），所有表与迁移
  core/
    points.py          积分账本（只送不卖/不可流通/不可回兑）+ 签到 + 邀请裂变 + token
    lmsr.py            LMSR 份额定价
    scoring.py         严格评分规则（Brier/log）+ 声誉加权
    markets.py         市场域 + 声誉加权概率 + 我的预测 + 准确率聚合
    settlement.py      平台奖励池结算（Brier 加成）
    oracle.py          Oracle 结算 + 自动结算 + 争议
    oracle_sources.py  可插拔权威源（manifest/HTTP 适配器，结算不再只能手动）
    achievements.py    成就/勋章（基于真实战绩，留存抓手）
    mall.py            积分商城（单向兑换）
    data_export.py     匿名化群体情绪指数 / CSV 导出（PIPL 合规，无 PII）
    whitelist.py       白名单 + 主权红线
  automation/
    scout.py           选题流水线（真实 RSS + 标注 demo 兜底）
    publish.py         发布（auto/review 分级）
    moderation.py      UGC 四道闸审核
  agents/
    support.py         客服 Agent（意图识别/FAQ/建单/升级）
    ads.py             广告 Agent（接单/定价/定广告位/对账单）
    devboard.py        开发看板闭环
  main.py              FastAPI：鉴权 + 安全响应头 + 限流 + 健康检查 + CORS + 体限 + OpenAPI
                        + WebSocket 实时概率 + 全部接口
  static/index.html    产品级 SPA（登录/市场/排行/商城/我的预测/勋章/发起/运营/客服/广告/看板/合规）
  seed.py              种子数据
  tests/smoke.py       冒烟测试（--fresh 可重置数据库后完整自验，30 项全绿）
```

## 运行
```bash
cd app
python -m venv ../../.venv && . ../../.venv/bin/activate   # 或任意 Python 3.11+
pip install fastapi uvicorn
python seed.py
python -m uvicorn main:app --port 8000
# 浏览器打开 http://localhost:8000
```
> admin 端点需 `x-admin-token` 头，演示默认 `dev-admin-token`（生产用环境变量 `ADMIN_TOKEN` 注入）。
> 环境变量：`CORS_ORIGINS`（逗号分隔允许来源）、`MAX_BODY_BYTES`（请求体上限，默认 1MB）、
> `ORACLE_HTTP_ENDPOINT` / `ORACLE_HTTP_APIKEY`（接入外部权威结算源）。

## 接口速览（节选）
- `GET /api/health` — 健康检查 + 各表计数 + Oracle 源启用状态。
- `POST /api/oracle/resolve-due` — 对到期市场依次咨询已启用权威源并自动结算（admin）。
- `POST /api/admin/markets` — 运营直接创建市场（admin）。

## 测试
```bash
python tests/smoke.py --fresh   # 重置数据库→重新种子→起本地服务→跑全链路，25 项全绿
```

## 已做实 vs 路线图
**做实**：声誉加权价格发现、真实 RSS 选题、Oracle 结算+争议、**可插拔权威源自动结算（manifest/HTTP）**、
匿名化数据导出、我的预测/校准、**成就/勋章（留存）**、**WebSocket 实时概率推送**、
**邀请裂变反刷（深度上限+自环防御）**、鉴权/反刷/并发/WAL、移动端导航、XSS/CSP、CORS/健康检查/体限/OpenAPI、合规页、冒烟测试（30 项）。
另见 `../合规就绪自评与上线清单.md`（阶段0 内部合规自检稿）。

**路线图（非本轮，需外部）**：真实生产部署（云/HTTPS）、**阶段0 律所合规意见书（上线前必做）**、
各平台全量 API 接入（RSS/权威源）、战队 PK/专家分体系。
