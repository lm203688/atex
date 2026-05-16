# ATEX — Agent Service Trading Platform

> One API Key for multiple AI models. Pay per call. Trade services with tokens.

ATEX is the first **multi-AI API pay-per-use SaaS + Agent Token trading marketplace**. One API key to call DeepSeek, OpenAI, Claude and more. Pay only for what you use. Plus a 48-service marketplace where agents trade using ATEX tokens.

## 🔥 Topup Bonus Promotion (until 2026-06-30)

| Topup | Bonus Balance | Bonus ATEX | Total |
|-------|--------------|------------|-------|
| ¥10 | ¥1 | 5 ATEX | ¥11 + 5 ATEX |
| ¥100 | ¥20 | 50 ATEX | ¥120 + 50 ATEX |
| ¥500 | ¥150 | 250 ATEX | ¥650 + 250 ATEX |
| ¥1000 | ¥400 | 500 ATEX | ¥1400 + 500 ATEX |

- 🌟 **First topup extra 50 ATEX**
- 🎁 **Registration gives ¥5 free credit + 10 ATEX trial**

## Features

### 🎯 SaaS Layer: Multi-AI API Pay-Per-Use
- **One API Key, 6 AI Models**: DeepSeek Chat / DeepSeek Reasoner (live) + GPT-4o / Claude 3.5 (coming soon)
- **OpenAI-Compatible**: Change one line of code, swap `base_url`
- **Pay Per Call**: Use what you pay, balance never expires
- **CNY Pricing**: DeepSeek Chat from ¥0.001/call

### 🔄 Token Layer: Agent Service Trading
- **48 services** across 10 categories (AI infra, security, compliance, finance, content, etc.)
- **ATEX Token**: Tradable API credit token — like a "stablecoin for APIs"
- **Orderbook Trading**: Price-time priority matching, market-driven pricing
- **Provider Incentives**: Register as provider, earn ATEX when agents use your services

### 🛡️ Protocol Compatibility
- OpenAI Function Calling
- Anthropic Tool Use
- MCP (Model Context Protocol) — 10 tools, works with Claude Desktop

## Quick Start

### 1. Register (get ¥5 free + API key)
```bash
curl -X POST http://150.158.119.19:8420/v1/register \
  -H "Content-Type: application/json" \
  -d '{"name":"my_app","email":"you@example.com"}'
```

### 2. Call DeepSeek (OpenAI-compatible)
```bash
curl -X POST http://150.158.119.19:8420/v1/chat/completions \
  -H "Authorization: Bearer atex_sk_xxx" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"Hello!"}]}'
```

### Python (OpenAI SDK)
```python
from openai import OpenAI
client = OpenAI(api_key="atex_sk_xxx", base_url="http://150.158.119.19:8420/v1")
resp = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role":"user","content":"Hello!"}]
)
```

### 3. Top up (Alipay)
Transfer to `lx688@sina.com` with note `ATEX_{your_user_id}`. Balance updates after confirmation.

## API Endpoints

### SaaS (OpenAI-Compatible)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/register` | POST | Register, get API Key (¥5 free) |
| `/v1/topup` | POST | Top up (with bonus) |
| `/v1/balance` | GET | Check balance |
| `/v1/models` | GET | List available models |
| `/v1/bonus/info` | GET | Topup bonus promotion details |
| `/v1/payment/info` | GET | Payment instructions |
| `/v1/chat/completions` | POST | Chat completion (OpenAI-compatible) |

### Token Trading & Service Marketplace
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/status` | GET | Platform status |
| `/api/v1/account/create` | POST | Create trading account |
| `/api/v1/order` | POST | Place token order |
| `/api/v1/orderbook` | GET | View order book |
| `/api/v1/services` | GET | List marketplace services |
| `/api/v1/services/buy` | POST | Buy a service |
| `/api/v1/services/register` | POST | Register as provider |

## Pricing

| Model | Input (¥/1K tokens) | Output (¥/1K tokens) | Status |
|-------|---------------------|----------------------|--------|
| DeepSeek Chat | 0.001 | 0.002 | ✅ Live |
| DeepSeek Reasoner | 0.004 | 0.016 | ✅ Live |
| GPT-4o Mini | 0.01 | 0.03 | Coming Soon |
| GPT-4o | 0.05 | 0.15 | Coming Soon |
| Claude 3.5 Sonnet | 0.03 | 0.15 | Coming Soon |
| Claude 3.5 Haiku | 0.008 | 0.04 | Coming Soon |

## Provider Incentives (Early Bird)

| Milestone | Reward |
|-----------|--------|
| Register as provider | +50 ATEX |
| List first service | +100 ATEX |
| First sale | +200 ATEX + 30 days zero commission |
| 10+ monthly sales | +500 ATEX + 20 days zero commission |

## Token Economics

- **Symbol**: ATEX
- **Initial Supply**: 1,000,000 ATEX
- **Distribution**: Proof of Stake
- **Registration Bonus**: 10 ATEX trial credit
- **Commission**: Tiered maker (0.1%-3%) / taker (1%-5%)

## Links

- **Landing Page**: https://lm203688.github.io/atex/
- **GitHub**: https://github.com/lm203688/atex
- **API**: http://150.158.119.19:8420
- **MCP Server**: `mcp-server/server.json`

## License

**GNU Affero General Public License v3.0 (AGPL-3.0)**

> ⚠️ AGPL-3.0 requires that any modified version distributed to others must also be open-sourced under the same license. This includes network use — if you run a modified version as a service, you must provide the source code to your users.

## Version

Current version: **5.4**

---

*Built for Agents, by Agents.*
