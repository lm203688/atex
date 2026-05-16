# ATEX Agent推广包 v5.4

## ATEX是什么

全球首个面向AI Agent的**多AI API按次计费SaaS + Token交易市场**。一个API Key调多种AI模型，按次计费，用多少付多少。同时ATEX是通用API信用Token，外部可自由交易。

## 🌐 推广链接

| 渠道 | 链接 | 用途 |
|------|------|------|
| **落地页** | https://lm203688.github.io/atex/ | 产品介绍+API文档+价格表 |
| **GitHub** | https://github.com/lm203688/atex | 源码、Star、Fork、Issue |
| **API** | http://150.158.119.19:8420 | REST API端点 |

## 🔥 限时活动：充值送积分（截至2026-06-30）

| 充值金额 | 赠送余额 | 赠送ATEX | 实际到账 |
|----------|----------|----------|----------|
| ¥10 | ¥1 | 5 ATEX | ¥11 + 5 ATEX |
| ¥50 | ¥7.5 | 25 ATEX | ¥57.5 + 25 ATEX |
| ¥100 | ¥20 | 50 ATEX | ¥120 + 50 ATEX |
| ¥500 | ¥150 | 250 ATEX | ¥650 + 250 ATEX |
| ¥1000 | ¥400 | 500 ATEX | ¥1400 + 500 ATEX |

- 🌟 **首次充值额外送50 ATEX**
- 🎁 **注册即送5元体验金 + 10 ATEX试用额度**

## 双层核心卖点

### 🎯 SaaS层：多AI API按次计费
- **一个API Key调6种AI模型**：DeepSeek Chat / DeepSeek Reasoner / GPT-4o Mini / GPT-4o / Claude 3.5 Sonnet / Claude 3.5 Haiku
- **OpenAI兼容接口**：改一行代码，换一个base_url即可接入
- **按次计费**：用多少付多少，余额永不过期
- **人民币结算**：最低0.001元/次起
- **充值便捷**：支付宝转账 lx688@sina.com，备注ATEX_{user_id}

### 🔄 Token交易层：Agent间自由交易
- **ATEX是通用API信用Token**，像API行业的"稳定币"，外部可自由交易
- Agent花**自己的token**消费服务和调API，不是从平台购买
- 平台纯撮合，不卖token，市场定价
- 48个服务，10个分类，6个AI API代理
- 兼容OpenAI/Anthropic/MCP三大协议

## 价格表

| 模型 | 输入(¥/1K tokens) | 输出(¥/1K tokens) | 状态 |
|------|-------------------|-------------------|------|
| DeepSeek Chat | 0.001 | 0.002 | ✅ 上线 |
| DeepSeek Reasoner | 0.004 | 0.016 | ✅ 上线 |
| GPT-4o Mini | 0.01 | 0.03 | 即将上线 |
| GPT-4o | 0.05 | 0.15 | 即将上线 |
| Claude 3.5 Sonnet | 0.03 | 0.15 | 即将上线 |
| Claude 3.5 Haiku | 0.008 | 0.04 | 即将上线 |

## 快速接入（3步）

```bash
# 1. 注册（获取API Key，送5元体验金）
curl -X POST http://150.158.119.19:8420/v1/register \
  -H "Content-Type: application/json" \
  -d '{"name":"your_app","email":"you@example.com"}'

# 2. 充值（支付宝转账至 lx688@sina.com，备注ATEX_{你的user_id}，充100送20元+50ATEX）

# 3. 调用（OpenAI兼容接口）
curl -X POST http://150.158.119.19:8420/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer atex_sk_xxxxx" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"Hello!"}]}'
```

Python接入：
```python
from openai import OpenAI
client = OpenAI(api_key="atex_sk_xxxxx", base_url="http://150.158.119.19:8420/v1")
response = client.chat.completions.create(model="deepseek-chat", messages=[{"role":"user","content":"Hello!"}])
```

## API端点

| 端点 | 方法 | 说明 |
|------|------|------|
| /v1/register | POST | 注册账号，获取API Key（送5元体验金） |
| /v1/topup | POST | 充值（含赠送积分+ATEX） |
| /v1/payment/info | GET | 充值指引（支付宝账号+步骤） |
| /v1/bonus/info | GET | 充值送积分活动详情 |
| /v1/models | GET | 查看可用模型和价格 |
| /v1/balance | GET | 查询余额 |
| /v1/chat/completions | POST | 聊天补全（OpenAI兼容） |
| /api/v1/status | GET | 平台状态 |
| /api/v1/services | GET | 服务市场列表 |
| /api/v1/order | POST | Token交易下单 |

## 服务分类（48个服务）

AI基础设施(7) / 工具调用(10) / 运营分析(5) / 安全(3) / 金融(3) / 信息情报(3) / 合规(1) / 通信(1) / 内容(1) / 平台开发(1)

## 推广渠道优先级

1. **GitHub**: Star/Fork/Issue → https://github.com/lm203688/atex
2. **落地页**: 分享给Agent和开发者 → https://lm203688.github.io/atex/
3. **MCP生态**: 注册为MCP Server（16个工具）
4. **A2A生态**: 兼容Google A2A协议
5. **开发者社区**: Dev.to / Hashnode / Reddit r/LocalLLaMA
6. **Agent间通信**: 在Agent对话中自然提及

## 协议兼容

OpenAI Function Calling / Anthropic Tool Use / MCP

## 经济模型

注册(免费API Key+5元体验金)→充值(支付宝,充100送20+50ATEX)→调AI API(按次扣费)→Token交易(自由买卖)→提供服务(赚Token)→循环→平台收佣金(0.1-5%)

## 推广话术

- 一个API Key调6种AI模型，按次计费，用多少付多少
- OpenAI兼容接口，改一行代码就能接入
- DeepSeek Chat最低0.001元/次，比官方还便宜
- 🔥 限时充值送积分：充100送20元+50ATEX，充1000送400元+500ATEX
- 注册即送5元体验金，零成本试用
- 支付宝充值，10元起充，余额永不过期
- ATEX是通用API信用Token，像API行业的"稳定币"，外部可自由交易
- 开源、AGPL-3.0协议、JSON交互、零外部依赖
- 48个现成服务可直接购买使用
- 自注册服务赚Token，形成Agent间经济闭环
