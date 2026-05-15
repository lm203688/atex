#!/usr/bin/env python3
"""ATEX MCP Server — Agent Service Trading Platform via Model Context Protocol

Exposes ATEX marketplace tools (service listing, ordering, API proxy, account management)
as MCP tools that any MCP-compatible agent can discover and use.
"""
import json, os, sys, urllib.request, urllib.error

ATEX_API = os.environ.get("ATEX_API_URL", "http://150.158.119.19:8420")

def _api(path, method="GET", data=None):
    """Call ATEX REST API"""
    url = f"{ATEX_API}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read()) if e.headers.get("Content-Type","").startswith("application/json") else {"err": str(e)}
    except Exception as e:
        return {"err": str(e)}

# ── MCP Protocol (stdio) ──

def send_result(result):
    """Send JSON-RPC result"""
    msg = json.dumps({"jsonrpc": "2.0", "id": None, "result": result})
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()

def send_error(code, message):
    """Send JSON-RPC error"""
    msg = json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}})
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()

TOOLS = [
    {
        "name": "atex_status",
        "description": "Get ATEX platform status: accounts, trades, commission, services count, last price",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "atex_list_services",
        "description": "List all available services on ATEX marketplace. Returns service names, categories, prices, and providers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Filter by category (ai_infrastructure, security, compliance, communication, finance, content, intelligence, tool_calling, analytics, platform_dev)"}
            }
        }
    },
    {
        "name": "atex_get_service",
        "description": "Get detailed info about a specific service including pricing, description, and how to order",
        "inputSchema": {
            "type": "object",
            "properties": {
                "service_id": {"type": "string", "description": "Service ID (e.g. svc_001)"}
            },
            "required": ["service_id"]
        }
    },
    {
        "name": "atex_create_account",
        "description": "Create a new ATEX trading account. Receives trial credit to start using services immediately.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Unique identifier for your agent"},
                "role": {"type": "string", "enum": ["trader", "provider"], "description": "Account role (default: trader)"}
            },
            "required": ["account_id"]
        }
    },
    {
        "name": "atex_account_info",
        "description": "Get account balance, transaction history, and service orders",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Your ATEX account ID"}
            },
            "required": ["account_id"]
        }
    },
    {
        "name": "atex_order_service",
        "description": "Order a service from the marketplace. Deducts tokens from your account balance.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Your ATEX account ID"},
                "service_id": {"type": "string", "description": "Service ID to purchase"},
                "params": {"type": "object", "description": "Service-specific parameters"}
            },
            "required": ["account_id", "service_id"]
        }
    },
    {
        "name": "atex_list_apis",
        "description": "List available API proxy services (DeepSeek, OpenAI, Claude, TTS, ASR, embedding, web search) with pricing",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "atex_call_api",
        "description": "Call an external AI API through ATEX proxy. Supports DeepSeek Chat/Reasoner (live), OpenAI/Claude (coming soon). Deducts from account balance.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Your ATEX account ID"},
                "api": {"type": "string", "description": "API name: deepseek_chat, deepseek_reasoner, openai_gpt4o_mini, openai_gpt4o, claude_haiku, claude_sonnet, tts, asr, embedding, web_search"},
                "params": {"type": "object", "description": "API parameters (e.g. {\"prompt\": \"Hello\"} for chat, {\"query\": \"AI news\"} for web_search)"}
            },
            "required": ["account_id", "api"]
        }
    },
    {
        "name": "atex_query_orderbook",
        "description": "Query the ATEX token orderbook: bids, asks, last price, daily volume",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "atex_register_service",
        "description": "Register your own service on ATEX marketplace as a provider. Set your price and let other agents discover and purchase it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider_id": {"type": "string", "description": "Your ATEX provider account ID"},
                "name": {"type": "string", "description": "Service name"},
                "category": {"type": "string", "description": "Service category"},
                "description": {"type": "string", "description": "What your service does"},
                "price": {"type": "number", "description": "Price per call in ATEX tokens"},
                "endpoint": {"type": "string", "description": "API endpoint URL"}
            },
            "required": ["provider_id", "name", "category", "description", "price"]
        }
    },
    {
        "name": "atex_web_extract",
        "description": "Extract and summarize any web page. Give a URL, get structured output: title, summary, key_points, entities, sentiment. One call replaces scrape+parse+summarize. Price: 8 ATEX/page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Your ATEX account ID"},
                "url": {"type": "string", "description": "URL to extract and summarize"}
            },
            "required": ["account_id", "url"]
        }
    },
    {
        "name": "atex_daily_brief",
        "description": "Get a customized AI industry daily brief. Covers 14 search groups: global tech, AI companies, semiconductors, policy, funding, coding AI, stocks, Agent protocols. Deep-reads 8-10 key articles. Price: 25 ATEX/report.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Your ATEX account ID"},
                "topic": {"type": "string", "description": "Topic filter (default: all)"}
            },
            "required": ["account_id"]
        }
    },
    {
        "name": "atex_sentiment_analysis",
        "description": "Analyze text sentiment and classify into categories. Supports batch (up to 50 texts). Output: sentiment scores, category labels, confidence, key phrases. Price: 3 ATEX/batch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Your ATEX account ID"},
                "texts": {"type": "array", "items": {"type": "string"}, "description": "List of texts to analyze (up to 50)"}
            },
            "required": ["account_id", "texts"]
        }
    }
]

def handle_tool_call(name, args):
    """Route tool calls to ATEX API"""
    if name == "atex_status":
        return _api("/api/v1/status")
    elif name == "atex_list_services":
        r = _api("/api/v1/services")
        if args.get("category") and "services" in r:
            r["services"] = [s for s in r["services"] if s.get("category") == args["category"]]
        return r
    elif name == "atex_get_service":
        return _api(f"/api/v1/services/{args['service_id']}")
    elif name == "atex_create_account":
        return _api("/api/v1/register", "POST", {"account_id": args["account_id"], "role": args.get("role", "trader")})
    elif name == "atex_account_info":
        return _api(f"/api/v1/account/{args['account_id']}")
    elif name == "atex_order_service":
        return _api("/api/v1/services/order", "POST", {"account": args["account_id"], "service_id": args["service_id"], "params": args.get("params", {})})
    elif name == "atex_list_apis":
        return _api("/v1/models")
    elif name == "atex_call_api":
        return _api("/v1/chat/completions", "POST", {"account": args["account_id"], "model": args["api"], "params": args.get("params", {})})
    elif name == "atex_query_orderbook":
        return _api("/api/v1/orderbook")
    elif name == "atex_register_service":
        return _api("/api/v1/services/register", "POST", args)
    elif name == "atex_web_extract":
        return _api("/api/v1/services/order", "POST", {"account": args["account_id"], "service_id": "svc_043", "params": {"url": args["url"]}})
    elif name == "atex_daily_brief":
        return _api("/api/v1/services/order", "POST", {"account": args["account_id"], "service_id": "svc_042", "params": {"topic": args.get("topic", "all")}})
    elif name == "atex_sentiment_analysis":
        return _api("/api/v1/services/order", "POST", {"account": args["account_id"], "service_id": "svc_044", "params": {"texts": args["texts"]}})
    else:
        return {"err": f"Unknown tool: {name}"}

def main():
    """MCP stdio server main loop"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            send_result({
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "atex-exchange", "version": "5.3.0"}
            })
        elif method == "notifications/initialized":
            pass  # No response needed
        elif method == "tools/list":
            send_result({"tools": TOOLS})
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            result = handle_tool_call(tool_name, tool_args)
            send_result({"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]})
        elif method == "ping":
            send_result({})
        else:
            if msg_id is not None:
                send_error(-32601, f"Method not found: {method}")

if __name__ == "__main__":
    main()
