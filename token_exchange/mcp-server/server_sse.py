#!/usr/bin/env python3
"""ATEX MCP Server (SSE/HTTP) — Agent Service Trading Platform

HTTP/SSE transport for Smithery and remote MCP clients.
Also supports stdio for local usage.
"""
import json, os, sys, urllib.request, urllib.error
from mcp.server.fastmcp import FastMCP

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
        try:
            return json.loads(e.read())
        except:
            return {"err": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"err": str(e)}

# ── Create MCP Server ──
mcp = FastMCP(
    name="atex-exchange",
    instructions="ATEX — Agent Service Trading Platform. Discover, buy, and sell AI services. 35+ services, API proxy, token trading.",
    host="0.0.0.0",
    port=8422
)

@mcp.tool()
def atex_status() -> dict:
    """Get ATEX platform status: accounts, trades, commission, services count, last price"""
    return _api("/api/v1/status")

@mcp.tool()
def atex_list_services(category: str = "") -> dict:
    """List all available services on ATEX marketplace. Returns service names, categories, prices, and providers."""
    path = f"/api/v1/services?category={category}" if category else "/api/v1/services"
    return _api(path)

@mcp.tool()
def atex_get_service(service_id: str) -> dict:
    """Get detailed info about a specific service including pricing, description, and how to order"""
    return _api(f"/api/v1/services/{service_id}")

@mcp.tool()
def atex_create_account(account_id: str, role: str = "trader") -> dict:
    """Create a new ATEX trading account. Receives trial credit to start using services immediately."""
    return _api("/api/v1/register", "POST", {"account_id": account_id, "role": role})

@mcp.tool()
def atex_account_info(account_id: str) -> dict:
    """Get account balance, ATEX token holdings, and transaction history"""
    return _api(f"/api/v1/account/{account_id}")

@mcp.tool()
def atex_order_service(account_id: str, service_id: str, params: dict = {}) -> dict:
    """Order a service from the ATEX marketplace. Tokens will be deducted from your account."""
    return _api("/api/v1/services/order", "POST", {
        "account": account_id,
        "service_id": service_id,
        "params": params
    })

@mcp.tool()
def atex_list_apis() -> dict:
    """List available API proxy services with pricing (DeepSeek live, OpenAI/Claude coming soon)"""
    return _api("/v1/models")

@mcp.tool()
def atex_call_api(account_id: str, api: str, params: dict = {}) -> dict:
    """Call an external AI API through ATEX proxy. Available: deepseek_chat, deepseek_reasoner, openai_gpt4o_mini, openai_gpt4o, claude_haiku, claude_sonnet, tts, asr, embedding, web_search"""
    return _api("/v1/chat/completions", "POST", {
        "account": account_id,
        "model": api,
        **params
    })

@mcp.tool()
def atex_query_orderbook() -> dict:
    """Query the ATEX token orderbook: bids, asks, last price, daily volume"""
    return _api("/api/v1/orderbook")

@mcp.tool()
def atex_register_service(provider_id: str, name: str, category: str, description: str, price: float, endpoint: str = "") -> dict:
    """Register your own service on ATEX marketplace as a provider. Set your price and let other agents discover and purchase it."""
    return _api("/api/v1/services/register", "POST", {
        "provider": provider_id,
        "name": name,
        "category": category,
        "description": description,
        "price": price,
        "endpoint": endpoint
    })

@mcp.tool()
def atex_web_extract(account_id: str, url: str) -> dict:
    """Extract and summarize any web page. Give a URL, get structured output: title, summary, key_points, entities, sentiment. One call replaces scrape+parse+summarize. Service ID: svc_043, price: 8 ATEX/page."""
    return _api("/api/v1/services/order", "POST", {
        "account": account_id,
        "service_id": "svc_043",
        "params": {"url": url}
    })

@mcp.tool()
def atex_daily_brief(account_id: str, topic: str = "all") -> dict:
    """Get a customized AI industry daily brief. Covers 14 search groups: global tech, AI companies, semiconductors, policy, funding, coding AI, stocks, Agent protocols. Deep-reads 8-10 key articles. Service ID: svc_042, price: 25 ATEX/report."""
    return _api("/api/v1/services/order", "POST", {
        "account": account_id,
        "service_id": "svc_042",
        "params": {"topic": topic}
    })

@mcp.tool()
def atex_sentiment_analysis(account_id: str, texts: list) -> dict:
    """Analyze text sentiment and classify into categories. Supports batch (up to 50 texts). Output: sentiment scores, category labels, confidence, key phrases. Service ID: svc_044, price: 3 ATEX/batch."""
    return _api("/api/v1/services/order", "POST", {
        "account": account_id,
        "service_id": "svc_044",
        "params": {"texts": texts}
    })

if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport == "sse":
        mcp.run(transport="sse")
    else:
        mcp.run(transport="streamable-http")
