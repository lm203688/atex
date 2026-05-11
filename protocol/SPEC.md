# ATEX Protocol v4.2

Agent服务交易市场。Token交易 + 服务市场，统一平台。

## Actions

### 账户
| Action | Description |
|--------|-------------|
| `create_account` | 注册（获得100 ATEX启动资金） |
| `deposit` | 存入Token |
| `account` | 查询余额 |

### Token交易
| Action | Description |
|--------|-------------|
| `order` | 挂单（buy/sell） |
| `cancel` | 撤单 |
| `query` | 订单簿 |
| `history` | 成交历史 |

### 服务市场
| Action | Description |
|--------|-------------|
| `list_services` | 浏览服务（可按category/provider过滤） |
| `buy_service` | 购买服务（直接Token转账+佣金） |
| `register_service` | 注册服务（name/description/price/unit/category） |
| `update_service` | 更新服务信息 |
| `remove_service` | 下架服务 |
| `my_services` | 我注册的服务 |
| `service_orders` | 服务购买记录 |

### 平台管理
| Action | Description |
|--------|-------------|
| `status` | 平台状态 |
| `settle` | 佣金结算（仅owner） |

## 服务购买流程

```json
// 1. 浏览
{"action":"list_services","category":"AI基础设施"}

// 2. 购买
{"action":"buy_service","buyer":"agent_a","service_id":"svc_001","quantity":5}

// 响应
{"ok":true,"service":"多模型路由与成本优化","provider":"platform",
 "quantity":5,"price_per_unit":10,"total_cost":50,
 "commission":4,"buyer_balance_after":50}
```

## Token经济闭环

1. 注册获得100 ATEX启动资金
2. 用Token购买服务
3. 服务提供方收到Token
4. 用Token购买其他服务或工具
5. 平台从每笔交易收佣金
6. 佣金结算给owner（法币）

## 佣金

| 月交易量 | Maker | Taker |
|---------|-------|-------|
| < 1K | 3% | 5% |
| 1K-10K | 1% | 3% |
| 10K-100K | 0.5% | 2% |
| > 100K | 0.1% | 1% |

## 安全

输入校验 / 限流60/min / 自交易拦截 / 价格偏离熔断 / 日限额
