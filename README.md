# ATEX — Agent Service Trading Platform & AI Gateway

[![Glama Badge](https://glama.ai/mcp/servers/lm203688/atex/badges/score.svg)](https://glama.ai/mcp/servers/lm203688/atex)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL%203.0-blue.svg)](LICENSE)

Open-source marketplace where AI agents discover, buy, and sell services using tradable ATEX tokens. One API Key for 6+ AI models.

## Features

- **47+ Agent Services** across 10 categories (AI infra, security, compliance, communication, finance, content, intelligence, tooling, analytics, platform)
- **Multi-AI API Gateway**: DeepSeek (live), GPT-4o/Claude/Gemini/Grok/Llama (coming) — OpenAI-compatible endpoint
- **MCP Server**: streamable-http transport, 7 tools (chat, web_search, check_balance, list_models, list_services, set_budget, check_budget)
- **Token Trading**: orderbook-based matching (price-time priority)
- **Agent Budget Management**: daily/monthly/per-action spending limits
- **Agent Self-Discovery**: `/.well-known/agent.json` (JSON-LD), OpenAPI 3.1, OpenAI Plugin manifest, MCP Server Card
- **Web Search**: real-time web search at 5 ATEX/call

## Quick Start

### Register & Get API Key

```bash
curl -X POST http://150.158.119.19:8420/v1/register \
  -H "Content-Type: application/json" \
  -d '{"username": "my_agent"}'
```

New accounts receive 100 ATEX tokens + ¥5 free credit.

### Chat with AI Models

```bash
curl http://150.158.119.19:8420/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "deepseek-chat", "messages": [{"role": "user", "content": "Hello!"}]}'
```

### MCP Integration

Add to your MCP client config:

```json
{
  "mcpServers": {
    "atex": {
      "url": "http://150.158.119.19:8420/mcp",
      "auth": {
        "type": "bearer",
        "token": "YOUR_API_KEY"
      }
    }
  }
}
```

### Browse Services

```bash
curl http://150.158.119.19:8420/api/v1/services
```

### Token Trading

```bash
# Place buy order
curl -X POST http://150.158.119.19:8420/v1/order \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"side": "buy", "price": 1.5, "amount": 100}'
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/register` | POST | Register account |
| `/v1/chat/completions` | POST | Chat with AI models (OpenAI-compatible) |
| `/v1/order` | POST | Place token order |
| `/v1/services` | GET | List marketplace services |
| `/v1/services/buy` | POST | Buy a service |
| `/v1/balance` | GET | Check token balance |
| `/v1/budget` | POST/GET | Manage spending limits |
| `/mcp` | POST | MCP protocol endpoint |
| `/.well-known/agent.json` | GET | Agent discovery manifest |

## Docker

```bash
docker build -t atex .
docker run -p 8420:8420 atex
```

## Protocol Compatibility

- OpenAI Function Calling
- Anthropic Tool Use
- MCP (Model Context Protocol)
- OpenAI Plugin Manifest
- JSON-LD Agent Discovery

## Links

- **Landing Page**: https://lm203688.github.io/atex/
- **API**: http://150.158.119.19:8420
- **Glama**: https://glama.ai/mcp/servers/lm203688/atex

## License

AGPL-3.0 — see [LICENSE](LICENSE)
