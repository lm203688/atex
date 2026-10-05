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
cd app && python tests/smoke.py --fresh   # 期望 154/154 全绿
cd app && python tests/uat_customer.py    # 期望 90/90 全绿
```

> 提示：本机若设了 `HTTP_PROXY`，测试脚本访问 `127.0.0.1` 会被代理拦截（表现为 502），
> 运行时请加 `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy` 清除。

## 安全边界（CSP nonce）
`script-src`（v0.7.1 起）与 `style-src`（v0.7.5 起）均已去掉 `'unsafe-inline'`，
改为每请求生成的一次性 nonce。nonce 只能由后端在渲染 HTML 时注入，因此：
- SPA 首页**必须**由 FastAPI 渲染下发（`_render_index`），不能由 nginx 等静态托管；
- CSP 头**只能**由应用层下发，nginx 不得重复下发（见 `deploy/nginx.conf` 文件头说明）。

## 市场类型
- **分类市场（categorical，默认）**：离散选项。LMSR 真实份额定价（份额≠投注额）+ 声誉加权共识概率，按份额结算。
- **数值市场（numeric，v0.7.8 新增，借鉴 Metaculus 连续型问题）**：预测区间内的一个数值，并可声明不确定性 `sigma`。
  - 共识 = 声誉加权的均值 / 中位数 / 离散度（与分类市场「声誉加权」同构，抑制免费积分下的噪声）。
  - 结算用 **CRPS**（连续分级概率评分）——**严格适当**评分规则，期望最优仅在如实报出真实分布时取得，
    因此同时奖励「准」与「不确定性诚实」，瞎报极窄区间会被重罚。正态分布下有闭式解，零开销。
  - 真实值**不做区间裁剪**：现实结果可能落在申报区间外，强行裁剪会扭曲评分，越界只导致技能分被裁为 0。
  - 合规：与分类市场同出一源（平台奖励池），赔付下限 1× 本金——预测得再差也不倒扣，不涉及用户间资金流转。

## 技能评级（v0.7.9，不确定性感知）
排行榜分两类：**积分榜**（`sort=points`，默认，反映活跃度）与**技能榜**（`sort=skill`）。

技能榜不按单点分排序，而按 **Glicko-2 的保守下界** `skill_rating - 1.96 × skill_rd`：
- `skill_rd`（评分偏差）直接量化「我们对这个人有多不确定」。下注越多 RD 越小；
  长期不下注 RD 会回升（见 `rating.rd_decay`）。
- 为什么用下界：裸命中率 `correct/resolved` 在样本极小时噪声极大——新用户下 2 注
  全中就是 100% 直冲榜首，样本一多又暴跌。用区间下沿后，低样本用户自然排后面，
  真实水平随样本积累才浮上来。命中率另给 Wilson 置信下界（`accuracy_conservative`）。
- 市场内**两两成对**比较（Bradley-Terry 系思路），得分基于严格评分的**相对份额**
  `s_ij = score_j / (score_i + score_j)`，满足 `s_ij + s_ji = 1`：一场比较零和，
  不会凭空造分，且比「谁押中谁赢 1 分」保留了幅度与校准信息。
- 样本不足（已结算 < 8 场）或久未参与的用户标 `provisional: true`，
  `?ranked_only=1` 可只看正式榜位。

**为什么不用 Brier 直接评 `prob_at_bet`**：`prob_at_bet` 是下注时的**市场价格**（群体
共识），不是用户自报的信念。拿 Brier 评它，评的是「群体共识准不准」，会出现
「押中 50% 热门(0.25) 输给 未中 40% 冷门(0.16)」的反向结论。改用「跑赢共识」的
标准化技能分 `(y - p) / sqrt(p(1-p))` 后方向才正：逆势押中 +2.0 > 从众押中 +0.5 >
0 > 冷门未中 −0.5 > 热门翻车 −2.0。

## 外部概率参考（v0.7.9，只读）
`EXTERNAL_REF_ENDPOINT` 配置后，可拉取外部公开市场的概率作为**参考水位**，用于
冷启动播种、「群体预测 vs 外部共识」偏差分析、以及校验自有 Oracle 的合理性。

合规隔离（硬约束，改动前必读）：
- 只做 HTTP GET 拉取，**不做下单、不接交易/结算接口、不涉加密或法币资金流**。
- 结果只写入 `markets.external_ref_prob / external_ref_json`，**绝不参与结算**——
  结算只读 `oracle_log` 与自有权威源。smoke 测试有代码级断言守护这条边界
  （结算/Oracle 路径出现 `external_ref` 即失败）。
- 未配置环境变量时模块整体停用，返回 `enabled=false`，**绝不伪造参考值**。

## 前端（v0.7.10，数值市场与技能榜打通）
此前 v0.7.8/0.7.9 的数值市场 API 与技能评级/排行榜后端已就绪，v0.7.10 完成 SPA 前端打通，
做到端到端可用：
- **数值市场参与**：市场卡片与详情页自动识别 `market_type=numeric`，渲染「数值预测 + 可选 σ 不确定性」输入框，
  走 `/api/markets/{id}/participate_numeric`；分类市场维持原选项选择。
- **数值共识展示**：详情页显示声誉加权共识中位数 / 均值 / 离散度与区间范围。
- **外部参考徽标**：配置了外部喂价时，详情页显示「🌐 外部市场共识参考 X% · 仅参考，不具结算效力」。
- **技能榜切换**：排行榜新增「按技能（保守下界·不确定性感知）」排序，展示技能分 95% 区间、置信度与「待定」标记，
  让低样本用户的噪声排名一目了然。

## v0.7.11 — P4 路线图落地（聚合预测 · 校准 · 声誉联动 · 受控 AI · MCP）
在 v0.7.10 产品级 SPA 基础上，按《项目完整性扫描与改进提升计划》P4 路线图补齐：

- **P4-A1 校准曲线（可靠性图）**：新增 `GET /api/users/{id}/calibration`，按 0.1/0.3/0.5/0.7/0.9 概率桶统计「下注概率 vs 实际命中率」，给出过度自信/保守标签与 Brier；前端「我的预测」以 SVG 可靠性图呈现。
- **P4-A2 聚合预测层**：新增 `GET /api/markets/{id}/forecast`，统一分类（LMSR 声誉加权）与数值（CRPS 加权共识）两类共识，并附 `deviation_vs_external` 群体 vs 外部参考偏差分析（外部概率**仅参考、绝不结算**，与 `external_ref` 合规隔离一致）。
- **P4-A3 声誉 × 准确度联动**：技能分折算进声誉（`tiers.skill_bonus_reputation`），让「准」的用户更快解锁 UGC 发起权等特权；`GET /api/users/{id}/tier` 新增 `skill_rating / skill_confidence / skill_certified`，前端展示「🎯 技能认证」徽标。
- **P4-B1 受控 AI 预测（合规护栏）**：新增 `app/automation/ai_predictor.py` 与 `POST /api/automation/ai_predict`。默认 `AI_PREDICT_ENABLED≠1` 时为安全 no-op；开启后 AI 走与普通用户完全相同的 `participate` 路径、受同样规则约束，全部标注 `is_ai=1`，前端明示「🤖 AI 预测」。无外部参考信号时不预测，避免注入噪声。
- **P4-B2 Oracle 人工兜底路由**：`POST /api/oracle/resolve-due` 改用 `resolve_due_with_human_fallback()`——多源结果分歧或 `ORACLE_AUTO_SETTLE` 未开启（默认）时一律升级 dev 人工裁定工单，**绝不自动结算**，守住人工兜底红线；单源一致且显式开启 `ORACLE_AUTO_SETTLE=1` 才自动结算并留痕。原源直连结算保留为 `POST /api/oracle/resolve-from-sources`。
- **P4-B3 MCP 兼容服务（JSON-RPC 2.0）**：新增 `app/mcp/server.py` 与 `POST /mcp`，纯标准库实现无外部依赖。只读工具（list_markets/get_market/get_forecast/get_leaderboard）免鉴权；写工具 `submit_prediction` 必须带 `Authorization: Bearer <token>` 且服务端核验 token 与 user_id 匹配后才走普通 participate 路径。

> 质量门禁：`python tests/smoke.py --fresh` 现为 **161/161 全绿**（含 P4 + P5 全量断言）。

### 真测游戏化（P5）· v0.7.12 → v0.7.14 · 用 Hook 模型补齐「每天回来」的理由
> 竞品/市场扫描结论：纯 play-money 预测在亚洲监管面最小、变现不能靠交易抽水，应走「广告 + 品牌众测 + VIP订阅(非买分) + 实物/抽奖(公示概率) + 匿名数据B2B + 引擎白标」复合栈。真测已站对赛道，缺的是**游戏循环**。本轮按 Nir Eyal Hook 模型（触发→行动→可变奖励→投入）落地四循环：

- **P5-A 每日习惯循环**：`core/daily.py` + 端点 `/api/daily/checkin`、`/api/daily/picks`、`/api/daily/picks/claim`、`/api/daily/status`。每日签到（连续递增、断签重置，损失厌恶）、每日一猜（3 场精选、完成领 15 积分）、预测连胜展示。前端「游戏中心」一屏。
- **P5-G 幸运转盘（可变奖励）**：`POST /api/lucky/spin` 每日免费 1 次，固定概率表（conic 转盘 UI）掉积分/抽奖券/皮肤碎片/连胜加成/近失安慰——Hunt 类爽点，概率后台不可调。
- **P5-B 赛季通行证（免费轨）**：`core/seasons.py` + `GET /api/season`、`POST /api/season/claim`。当前赛季 10 级免费轨，XP 来自真实活动（预测/评论/签到/每日一猜/转盘），奖励为积分/抽奖券/勋章，全部公示；付费轨禁用（因不可人民币买分）。
- **P5-D 段位赛 / P5-C 皮肤抽卡（默认关）**：段位展示由声誉驱动；`core/cosmetics.py` 写好外观抽卡（概率公示 + 保底 10 抽 + 单日上限 5 + 仅外观零货币价值），但 `LOOTBOX_ENABLED` 默认 `0` 关闭、前端不开入口，**等律所意见后一键开启**。
- **P5-H 新手引导**：首次进入「游戏中心」弹 3 步引导（选兴趣→下注→看校准）；注册即送 500 积分 + 每日签到连胜（禀赋进展效应）。
- **P5-γ 社交：战队 + 好友 PK（积分内闭环，零合规风险）**：`core/social.py` + 端点 `/api/squads`(建)、`/api/squads/{id}/join|leave`、`/api/squads/mine`、`/api/squads`(列表)、`/api/squads/leaderboard`、`/api/pk/challenge`、`/api/pk/mine`、`/api/pk/leaderboard`。战队为**纯荣誉聚合榜**（成员预测活跃度+校准+声誉），不发放/不转移积分；好友 PK 双方对同一市场比准，结算时**由平台奖励池**发放胜者小额积分+「PK之王」勋章，输家不付出、双方积分不互转（非流通、非回兑）。市场结算时自动连带结算该市场上的所有 PK。全程 18+，不涉及人民币/加密/赌博三要素。
- **P5-δ 变现层（合规复合栈）**：
  - **L1 激励视频广告**：`core/adwatch.py` + `GET /api/ads/offer`、`GET /api/ads/status`、`POST /api/ads/watch`。用户自愿观看（前端占位 30s）→ 领平台积分（默认 20/次、每日上限 3）。链路：广告主付费→平台发积分→用户免费得，积分仍只送不卖。
  - **L2 品牌众测赞助**：`core/sponsors.py` + `GET /api/sponsors`、`POST /api/markets/{id}/sponsor`(admin)、`GET /api/sponsors/{id}/report`(admin)。品牌赞助市场（徽标展示），品牌方取该市场**匿名**群体情绪报告（无 PII）；奖品由品牌提供，参照虎扑/腾讯体育「品牌主题联赛」。种子已演示：双十一市场由「星寰电商」赞助。
  - **L3 VIP 订阅（不卖分）**：`core/vip.py` + `GET /api/vip/tiers`、`POST /api/vip/subscribe`。三档（免费/支持者 ¥9/Pro ¥29），权益为去广告/专属分析/抢先体验，**绝不发放下注积分、绝不 pay-to-win**。真实支付需接 xunhupay+ICP，`VIP_PAYMENTS_ENABLED` 默认 `0` 关闭，仅暴露档位说明。
- 合规：所有循环仅服务 18+；积分只送不卖/不可流通/不可回兑；抽卡按最严标准（公示概率+保底+上限）先行写好，不开 UI 入口；广告/VIP/赞助变现均不触碰「人民币买积分」「积分回兑现金」红线。

> 质量门禁：`python tests/smoke.py --fresh` 现为 **161/161 全绿**（含 P4 + P5 全量断言）。

## 上线硬性前提（未满足不得公开运营）
- 服务器 ICP 备案（腾讯云）
- 律所合规意见书
