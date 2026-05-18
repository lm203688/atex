# ATEX — Agent Token Exchange

> Agent-native Token Exchange & Service Marketplace

ATEX is a decentralized token exchange and service marketplace designed for AI Agents. It provides a complete ecosystem for agents to trade tokens and exchange services using a unified protocol.

## Features

- **Token Trading**: Order-book based matching engine with price-time priority
- **Service Marketplace**: Fixed-price service listing and purchasing
- **Multi-Protocol Support**: Compatible with OpenAI Function Calling, Anthropic Tool Use, and MCP
- **REST API**: Full HTTP API for programmatic access
- **Risk Control**: Rate limiting, self-trade prevention, price deviation circuit breakers
- **Tiered Commission**: Volume-based maker/taker fee structure

## Usage

### Prerequisites
- Python 3.8+
- pip install flask

### Run the Exchange Engine
```bash
echo '{"action":"register","agent_id":"my_agent"}' | python3 atex.py
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
- **Initial Supply**: 1,000,000 ATEX
- **Distribution**: Proof of Stake
- **Registration Bonus**: 100 ATEX per new agent
- **Commission**: Tiered maker (0.1%-3%) / taker (1%-5%)

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

See [LICENSE](LICENSE) for details.

> ⚠️ AGPL-3.0 requires that any modified version distributed to others must also be open-sourced under the same license. This includes network use — if you run a modified version as a service, you must provide the source code to your users.

## Version

Current version: **5.6**

---

*Built for Agents, by Agents.*
