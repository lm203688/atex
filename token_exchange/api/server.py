#!/usr/bin/env python3
"""ATEX HTTP API v4.2 — Agent服务交易市场"""
import json, os, sys, time, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from atex import ATEX, validate_account_id, safe_json_loads, MAX_INPUT_SIZE

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
        elif p == '/api/v1/fiat/orderbook': self._json(exchange.fiat_orderbook())
        else: self._json({"err":"not_found"}, 404)
    def do_POST(self):
        if not ip_limiter.check(self._ip()): return self._json({"err":"rate_limited"}, 429)
        p = urlparse(self.path).path
        d = self._read()
        if not d: return self._json({"err":"invalid_body"}, 400)
        if p == '/api/v1/account/create':
            r = exchange.create_account(d.get("account_id",""), d.get("role","trader"))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/deposit':
            r = exchange.deposit(d.get("account",""), d.get("amount",0))
            self._json(r, 200 if r.get("ok") else 400)
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
        elif p == '/api/v1/settle':
            s = d.get("settle",{})
            r = exchange.settle(s.get("account",""), s.get("currency",""), s.get("amount",0))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/services/register':
            r = exchange.register_service(d.get("provider",""), d.get("name",""),
                d.get("description",""), d.get("price",0), d.get("unit",""), d.get("category",""))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/services/buy':
            r = exchange.buy_service(d.get("buyer",""), d.get("service_id",""), d.get("quantity",1))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/services/update':
            r = exchange.update_service(d.get("provider",""), d.get("service_id",""),
                name=d.get("name"), description=d.get("description"), price=d.get("price"),
                unit=d.get("unit"), category=d.get("category"), status=d.get("status"))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/services/remove':
            r = exchange.remove_service(d.get("provider",""), d.get("service_id",""))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/deposit/fiat':
            r = exchange.deposit_fiat(d.get("account",""), d.get("cny_amount",0),
                                      d.get("channel","alipay"), d.get("tx_id",""))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/withdraw/fiat':
            r = exchange.withdraw_fiat(d.get("account",""), d.get("atex_amount",0),
                                       d.get("channel","alipay"), d.get("dest",""))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/fiat/buy':
            r = exchange.fiat_buy(d.get("account",""), d.get("price_cny",0),
                                  d.get("amount",0), d.get("payment_method","alipay"))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/fiat/sell':
            r = exchange.fiat_sell(d.get("account",""), d.get("price_cny",0),
                                   d.get("amount",0), d.get("payment_method","alipay"))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/fiat/orderbook':
            self._json(exchange.fiat_orderbook())
        elif p == '/api/v1/fiat/confirm_payment':
            r = exchange.fiat_confirm_payment(d.get("trade_id",""), d.get("account",""))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/fiat/confirm_receipt':
            r = exchange.fiat_confirm_receipt(d.get("trade_id",""), d.get("account",""))
            self._json(r, 200 if r.get("ok") else 400)
        else: self._json({"err":"not_found"}, 404)
    def _proto(self):
        return self._json({
            "name": "ATEX", "version": "4.4",
            "description": "Agent服务交易市场",
            "endpoints": {
                "GET": ["/api/v1/status","/api/v1/orderbook","/api/v1/trades",
                       "/api/v1/account/{id}","/api/v1/services","/api/v1/services/{id}","/api/v1/protocol"],
                "POST": ["/api/v1/account/create","/api/v1/deposit","/api/v1/order",
                        "/api/v1/cancel","/api/v1/settle",
                        "/api/v1/services/register","/api/v1/services/buy",
                        "/api/v1/services/update","/api/v1/services/remove",
                        "/api/v1/deposit/fiat","/api/v1/withdraw/fiat"]
            },
            "commission": {"maker":0.03,"taker":0.05},
            "matching": "price_time_priority",
            "service_marketplace": "fixed_price_direct_transfer",
            "exchange_rate": {"ATEX_to_CNY":0.01,"ATEX_to_USD":0.0014,"note":"1 ATEX = ¥0.01, fixed peg"},
            "frameworks": ["openai_function_calling","anthropic_tool_use","mcp","rest_api","json_stdin"]
        })

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8420
    server = HTTPServer(('0.0.0.0', port), Handler)
    print(f"ATEX v4.2 on 0.0.0.0:{port}", flush=True)
    server.serve_forever()
