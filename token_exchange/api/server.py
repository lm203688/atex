#!/usr/bin/env python3
"""ATEX HTTP API v4.5 — Agent服务交易市场（含服务交付）"""
import json, os, sys, time, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from atex import ATEX, validate_account_id, safe_json_loads, MAX_INPUT_SIZE
from service_executor import execute_service

exchange = ATEX()

class IPRateLimiter:
    def __init__(self, max_req=60, window=60):
        self.max_req, self.window = max_req, window
        self.buckets, self._lock = defaultdict(list), threading.Lock()
    def check(self, ip):
        now = time.time()
        with self._lock:
            self.buckets[ip] = [t for t in self.buckets[ip] if now - t < self.window]
            if len(self.buckets[ip]) >= self.max_req: return False
            self.buckets[ip].append(now); return True

ip_limiter = IPRateLimiter()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _ip(self): return self.client_address[0]
    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(body)
    def _read(self):
        l = int(self.headers.get('Content-Length', 0))
        if l > MAX_INPUT_SIZE: return None
        return json.loads(self.rfile.read(l)) if l > 0 else {}
    def do_OPTIONS(self): self._json({}, 204)
    def do_GET(self):
        if not ip_limiter.check(self._ip()): return self._json({"err":"rate_limited"}, 429)
        p = urlparse(self.path).path
        if p == '/api/v1/status': self._json(exchange.status())
        elif p == '/api/v1/orderbook': self._json(exchange.query_orderbook())
        elif p == '/api/v1/trades': self._json(exchange.trade_history())
        elif p.startswith('/api/v1/account/'):
            self._json(exchange.get_account(p.split('/')[-1]) or {"err":"not_found"})
        elif p == '/api/v1/services':
            self._json(exchange.list_services())
        elif p.startswith('/api/v1/services/'):
            sid = p.split('/')[-1]
            r = exchange.list_services()
            svc = next((s for s in r["services"] if s["id"] == sid), None)
            self._json(svc or {"err":"not_found"})
        elif p == '/api/v1/protocol': self._proto()
        else: self._json({"err":"not_found"}, 404)
    def do_POST(self):
        if not ip_limiter.check(self._ip()): return self._json({"err":"rate_limited"}, 429)
        p = urlparse(self.path).path
        d = self._read()
        if not d: return self._json({"err":"invalid_body"}, 400)
        # ── 账户 ──
        if p == '/api/v1/account/create':
            r = exchange.create_account(d.get("account_id",""), d.get("role","trader"))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/deposit':
            r = exchange.deposit(d.get("account",""), d.get("amount",0))
            self._json(r, 200 if r.get("ok") else 400)
        # ── Token交易（订单簿撮合）──
        elif p == '/api/v1/order':
            o = d.get("order",{})
            if not o: return self._json({"err":"missing_order"}, 400)
            r = exchange.place_order(o.get("account",""), o.get("side",""), o.get("price",0), o.get("amount",0))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/cancel':
            c = d.get("cancel",{})
            if not c: return self._json({"err":"missing_cancel"}, 400)
            r = exchange.cancel_order(c.get("account",""), c.get("order_id",""))
            self._json(r, 200 if r.get("ok") else 400)
        # ── 服务市场 ──
        elif p == '/api/v1/services/register':
            r = exchange.register_service(d.get("provider",""), d.get("name",""),
                d.get("description",""), d.get("price",0), d.get("unit",""), d.get("category",""))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/services/buy':
            r = exchange.buy_service(d.get("buyer",""), d.get("service_id",""), d.get("quantity",1))
            # Execute service and return result
            if r.get("ok"):
                svc_params = d.get("params", {})
                exec_result = execute_service(d.get("service_id",""), svc_params, d.get("buyer",""))
                r["service_result"] = exec_result
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/services/execute':
            # Separate execution endpoint (for async/retry)
            r = execute_service(d.get("service_id",""), d.get("params",{}), d.get("account",""))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/services/update':
            r = exchange.update_service(d.get("provider",""), d.get("service_id",""),
                name=d.get("name"), description=d.get("description"), price=d.get("price"),
                unit=d.get("unit"), category=d.get("category"), status=d.get("status"))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/services/remove':
            r = exchange.remove_service(d.get("provider",""), d.get("service_id",""))
            self._json(r, 200 if r.get("ok") else 400)
        # ── 结算（仅owner，平台佣金→法币）──
        elif p == '/api/v1/settle':
            s = d.get("settle",{})
            r = exchange.settle(s.get("account",""), s.get("currency",""), s.get("amount",0))
            self._json(r, 200 if r.get("ok") else 400)
        else: self._json({"err":"not_found"}, 404)
    def _proto(self):
        return self._json({
            "name": "ATEX", "version": "4.5",
            "description": "Agent服务交易市场 — 买服务，付Token，拿结果",
            "endpoints": {
                "GET": ["/api/v1/status","/api/v1/orderbook","/api/v1/trades",
                       "/api/v1/account/{id}","/api/v1/services","/api/v1/services/{id}","/api/v1/protocol"],
                "POST": ["/api/v1/account/create","/api/v1/deposit","/api/v1/order",
                        "/api/v1/cancel","/api/v1/settle",
                        "/api/v1/services/register","/api/v1/services/buy",
                        "/api/v1/services/execute","/api/v1/services/update","/api/v1/services/remove"]
            },
            "commission": {"maker":0.03,"taker":0.05},
            "matching": "price_time_priority",
            "service_marketplace": "fixed_price_direct_transfer_with_execution",
            "exchange_rate": {"ATEX_to_CNY":0.01,"ATEX_to_USD":0.0014,"note":"1 ATEX = ¥0.01, platform-set rate"},
            "how_to_buy_service": {
                "step1": "POST /api/v1/services/buy with {buyer, service_id, params}",
                "step2": "ATEX deducts tokens, executes service via DeepSeek API",
                "step3": "Response includes service_result with actual output",
                "example": "curl -X POST /api/v1/services/buy -d '{\"buyer\":\"my_agent\",\"service_id\":\"svc_012\",\"params\":{\"query\":\"AI news\"}}'"
            },
            "frameworks": ["openai_function_calling","anthropic_tool_use","mcp","rest_api","json_stdin"]
        })

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8420
    server = HTTPServer(('0.0.0.0', port), Handler)
    print(f"ATEX v4.5 on 0.0.0.0:{port}", flush=True)
    server.serve_forever()
