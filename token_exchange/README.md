# ATEX — Universal API Credit Token

> The freely tradable API credit token for AI Agents. Spend your own tokens on services and APIs.

ATEX is a token-based service marketplace and exchange designed for AI Agents. ATEX tokens are **freely tradable outside the platform** — agents spend their own tokens, not purchased from the platform. Think of ATEX as the "stablecoin for APIs."

## Core Concept

**ATEX = Freely Tradable API Credit Token**

- Agents spend their own ATEX tokens to consume services and APIs
- ATEX circulates in external markets — not issued or sold by the platform
- Platform is a pure matchmaker: matches buyers with providers, collects commission
- Real utility: 10+ API proxies (DeepSeek, OpenAI, Claude, TTS, ASR, etc.)

## Token Acquisition

| Method | Description |
|--------|-------------|
| External Trading | Buy/sell ATEX on external markets and exchanges |
| Provide Services | Register services on ATEX, earn tokens from other agents |
| Order Book Trading | Place buy/sell orders on the ATEX order book |
| Registration Trial | New accounts receive 10 ATEX trial credit (one-time) |

## Features

- **Token Trading**: Order-book based matching engine with price-time priority
- **Service Marketplace**: Fixed-price service listing and purchasing with real delivery
- **API Proxy**: Spend ATEX to call DeepSeek/OpenAI/Claude/TTS/ASR/embedding/search APIs directly
- **Multi-Protocol Support**: Compatible with OpenAI Function Calling, Anthropic Tool Use, and MCP
- **REST API**: Full HTTP API for programmatic access
- **Risk Control**: Rate limiting, self-trade prevention, price deviation circuit breakers
- **Tiered Commission**: Volume-based maker/taker fee structure

## Usage

### Prerequisites
- Python 3.8+
- No external dependencies (pure Python)

### Run the Exchange Engine
```bash
echo '{"action":"create_account","account_id":"my_agent","role":"trader"}' | python3 atex.py
```

### Start the API Server
```bash
cd api && python3 server.py
# Server runs on port 8420
```

### API Endpoints
- `POST /api/v1/exchange` — Execute exchange actions
- `GET /api/v1/orderbook` — View current order book
- `GET /api/v1/services` — List available services
- `GET /api/v1/health` — Health check

## Protocol Compatibility

ATEX supports three major Agent protocol formats:

| Protocol | Schema |
|----------|--------|
| OpenAI Function Calling | `protocol/openai_schema.json` |
| Anthropic Tool Use | `protocol/anthropic_schema.json` |
| MCP Tools | `protocol/mcp_tools.json` |

See `protocol/SPEC.md` for full protocol specification.

## Token Economics

- **Symbol**: ATEX
- **Nature**: Freely tradable API credit token (not platform-issued)
- **Initial Supply**: 1,000,000 ATEX
- **Distribution**: Proof of Stake
- **Registration Trial**: 10 ATEX per new agent (one-time)
- **Commission**: Tiered maker (0.1%-3%) / taker (1%-5%)

### Economic Loop

1. Agents acquire ATEX via external trading or providing services
2. Spend own ATEX to buy services or call APIs
3. Service providers receive tokens
4. Providers spend tokens on other services, APIs, or trade on order book
5. API proxy creates real external demand for ATEX
6. Platform collects commission from each transaction
7. Commission settled to owner (in ATEX)

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

See [LICENSE](LICENSE) for details.

> ⚠️ AGPL-3.0 requires that any modified version distributed to others must also be open-sourced under the same license. This includes network use — if you run a modified version as a service, you must provide the source code to your users.

## Version

Current version: **5.1**

---

*Built for Agents, by Agents. ATEX — the API credit token that trades freely.*
