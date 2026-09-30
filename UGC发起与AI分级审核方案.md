# UGC 发起与 AI 分级审核方案（PRD 增订）

> 配套《项目需求方案PRD.md》第 13 章。本方案把「用户发起事件 + AI 分级审核」正式确立为内容供给的**第二引擎**（第一引擎为平台自动选题 `scout`）。机制已落地进 MVP 代码（`app/automation/moderation.py`、`/api/ugc/submit`）。

---

## 1. 背景与目标

平台自动选题只能覆盖热门话题，长尾与细分事件需要社区贡献。引入 UGC 发起有两重价值：

- **参与感**：用户从「参与者」升级为「创建者」，是社群粘性的核心来源（参考 Metaculus / Polymarket 的 UGC 盘口文化）。
- **内容供给**：把选题生产成本转嫁给社区，平台只做审核与结算。

但 UGC 会**放大合规风险**（用户会主动试探红线），因此必须做成**分级审核**，AI 只负责初筛与放行低风险，中高风险保留人工兜底。

## 2. 总体机制

```
用户发起 ──► 四道审核闸 ──► AI 分级路由 ──► 结果反馈发起者
                │                  │
           (复用 whitelist)   auto → 直接建市场(review→人工兜底 / reject→驳回给理由)
```

用户发起的事件与平台自动选题走**同一个 `publish_queue`**，仅 `source` 标记不同（`user` vs `auto`），复用现有白名单、发布、人工兜底、客服升级全链路。

## 3. 四道审核闸（AI 初筛）

| 闸 | 检查项 | 失败处理 |
|----|--------|----------|
| ① 白名单分类 | 类别是否在安全/条件白名单内 | 未知类别 → review |
| ② 主权红线·敏感词 | 台港澳当外国、政治敏感、禁词 | 命中 → **reject**（硬违规） |
| ③ 诱导赌博话术 | 押注/盘口/赔率/对赌/稳赚/跟单等 | 命中 → **reject**（硬违规） |
| ④ 可结算性 + 质量初筛 | 缺 Oracle 数据源、引流/广告/侵权嫌疑 | 命中 → review（一票否决自动上线） |

- **闸②/③ 为硬拦截**：直接 reject，不给自动上线机会。
- **闸④ 可结算性为「一票否决自动上线」**：没有可信 Oracle 的事件，AI 无法保证客观结算，必须进人工复核或由人工补数据源后才能上线。这是 UGC 最大的坑（结算不公 = 信任崩 + 投诉 + 监管关注）。
- **闸④ 质量初筛**：检测标题/描述中的引流话术（加微信、扫码、私聊、外链、代购、返利等），视为 review。

## 4. 分级路由判定矩阵

| 条件 | 路由 |
|------|------|
| 闸②或闸③命中 | **reject**（给原因） |
| 缺 Oracle 数据源 | **review** |
| 未命中白名单类别 / 条件品类 | **review** |
| 质量/引流嫌疑 | **review** |
| 全过（安全类别 + 有 Oracle + 无话术/垃圾） | **auto**（直接发布为市场） |

`route` 写入 `publish_queue.route`，与平台自动选题共用 `publish_auto()` / `admin_approve()` 发布逻辑。

## 5. 发起者激励（去 self-dealing）

- 事件通过并发布（`auto` 或 `review` 放行）后，平台向发起者**赠送积分奖励**（`CREATOR_REWARD=20`，走 `points.grant` 平台正向发放，符合「只送不卖」红线）。
- **隔离自肥**：发起者**不能参与自己发起的事件下注**（`markets.participate` 内校验 `creator == user_id` 即拒绝）。奖励与发起者自身押注完全脱钩。
- 奖励额度受单用户日发放硬上限（2000）约束。

## 6. 合规兜底与责任

- **人工兜底节点保留**：review 路由进审核队列，由 admin 放行（复用现有 `/api/admin/approve`）。
- **审核留痕**：`publish_queue` 记录标题、草稿、路由、状态、原因，可审计。
- **投诉通道**：用户投诉某事件 → 客服 Agent 升级 → 触发下架/复核（与现有 dev 看板闭环打通）。
- **主体责任**：遵守《网络安全法》UGC 平台内容审核与记录留存要求；上线前阶段 0 律所合规意见书必做。

## 7. 与现有自动化闭环整合

```
scout (平台自动选题) ─┐
                      ├─► publish_queue ─► publish_auto / admin_approve ─► markets
UGC submit (用户发起) ┘        │source=user
                               └─► 复用 whitelist + moderation 四道闸
```

新增入口仅在前端多一个「发起事件」表单 + 后端 `/api/ugc/submit`，其余全复用。

## 8. API 设计

`POST /api/ugc/submit`
```json
{
  "creator": 1,
  "title": "哪支队会赢得本周联赛冠军？",
  "description": "基于官方赛果结算",
  "category": "体育",
  "options": ["A队", "B队", "其他"],
  "oracle_source": "联赛官网",
  "settlement_criteria": "以官方公布的冠军归属为准",
  "closes_at": "2026-08-13"
}
```
返回：
```json
{ "queue_id": 12, "route": "auto", "reasons": [] }
// route ∈ auto / review / reject
```

## 9. 开发任务拆分（已完成）

1. `app/automation/moderation.py`：四道闸 + 分级路由。
2. `app/db.py`：markets 表加 `creator` / `settlement_criteria` 列（含 ALTER 迁移）。
3. `app/core/markets.py`：`create_market` 加 `creator`/`settlement_criteria`；`participate` 禁发起者下注自身事件。
4. `app/automation/publish.py`：`publish_auto`/`admin_approve` 传递 creator 并奖励发起者。
5. `app/main.py`：新增 `/api/ugc/submit`。
6. `app/static/index.html`：新增「发起事件」标签页与表单。

## 10. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 野鸡事件无权威结算 | 可结算性一票否决（闸④） |
| 用户试探政治/台港澳红线 | 主权红线硬 reject（闸②） |
| 变相赌博话术包装 | 话术检测硬 reject（闸③） |
| 发起者自肥刷分 | 禁自身下注 + 奖励脱钩 |
| 垃圾/引流广告伪装 | 质量初筛进 review |
| 结算争议投诉 | 客服 Agent 升级 → 下架复核 |

---

*本方案与 PRD 共同构成开发与上线依据。日常运营零人工，合规兜底（review 放行、敏感投诉、Oracle 冲突）保留人工升级。*
