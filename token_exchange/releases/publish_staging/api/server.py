#!/usr/bin/env python3
"""ATEX HTTP API v6.0 — 多AI API按次收费SaaS + Agent服务交易市场"""
import json, os, sys, time, threading, hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from collections import defaultdict
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from atex import ATEX, validate_account_id, safe_json_loads, MAX_INPUT_SIZE
from service_executor import execute_service, execute_api_proxy

exchange = ATEX()
TZ = timezone(timedelta(hours=8))

SAAS_DATA = os.path.join(BASE, "saas_data")
os.makedirs(SAAS_DATA, exist_ok=True)

def _load_saas():
 path = os.path.join(SAAS_DATA, "users.json")
 if os.path.exists(path):
 with open(path) as f: return json.load(f)
 return {"users": {}, "credentials": {}, "usage": []}

def _save_saas(data):
 path = os.path.join(SAAS_DATA, "users.json")
 with open(path, "w") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def _saas_user(auth_token):
 data = _load_saas()
 uid = data["credentials"].get(auth_token)
 if not uid: return None
 return data["users"].get(uid)

def _deduct(uid, cost_cny, model, input_tokens, output_tokens):
 data = _load_saas()
 user = data["users"].get(uid)
 if not user: return False
 if user["balance_cny"] < cost_cny: return False
 user["balance_cny"] = round(user["balance_cny"] - cost_cny, 6)
 user["total_spent_cny"] = round(user.get("total_spent_cny", 0) + cost_cny, 6)
 user["total_calls"] = user.get("total_calls", 0) + 1
 data["usage"].append({
 "user_id": uid, "model": model,
 "input_tokens": input_tokens, "output_tokens": output_tokens,
 "cost_cny": cost_cny, "time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
 })
 if len(data["usage"]) > 10000: data["usage"] = data["usage"][-5000:]
 _save_saas(data)
 return True

SAAS_PRICING = {
 "deepseek-chat": {"name":"DeepSeek Chat","input_per_1k":0.001,"output_per_1k":0.002,"backend":"deepseek","model":"deepseek-chat"},
 "deepseek-reasoner": {"name":"DeepSeek Reasoner","input_per_1k":0.004,"output_per_1k":0.016,"backend":"deepseek","model":"deepseek-reasoner"},
 "gpt-4o-mini": {"name":"GPT-4o Mini","input_per_1k":0.01,"output_per_1k":0.03,"backend":"openai","model":"gpt-4o-mini","status":"coming_soon"},
 "gpt-4o": {"name":"GPT-4o","input_per_1k":0.05,"output_per_1k":0.15,"backend":"openai","model":"gpt-4o","status":"coming_soon"},
 "claude-3-5-sonnet": {"name":"Claude 3.5 Sonnet","input_per_1k":0.03,"output_per_1k":0.15,"backend":"anthropic","model":"claude-3-5-sonnet-latest","status":"coming_soon"},
 "claude-3-5-haiku": {"name":"Claude 3.5 Haiku","input_per_1k":0.008,"output_per_1k":0.04,"backend":"anthropic","model":"claude-3-5-haiku-latest","status":"coming_soon"},
}

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
 try:
 body = json.dumps(data, ensure_ascii=False).encode()
 self.send_response(status)
 self.send_header('Content-Type', 'application/json')
 self.send_header('X-Content-Type-Options', 'nosniff')
 self.send_header('Access-Control-Allow-Origin', '*')
 self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
 self.send_header('Access-Control-Allow-Headers', 'Content-Type')
 self.end_headers()
 self.wfile.write(body)
 except (ConnectionResetError, BrokenPipeError):
 pass
 def _read(self):
 l = int(self.headers.get('Content-Length', 0))
 if l > MAX_INPUT_SIZE: return None
 return json.loads(self.rfile.read(l)) if l > 0 else {}
 def do_OPTIONS(self): self._json({}, 204)
 def do_GET(self):
 if not ip_limiter.check(self._ip()): return self._json({"err":"rate_limited"}, 429)
 p = urlparse(self.path).path

 if p == '/v1/models':
 models = []
 for mid, info in SAAS_PRICING.items():
 models.append({"id": mid, "name": info["name"], "status": info.get("status", "live"),
 "pricing": {"input_per_1k_cny": info["input_per_1k"], "output_per_1k_cny": info["output_per_1k"]}})
 self._json({"object": "list", "data": models})
 elif p == '/v1/balance':
 auth = self.headers.get("Authorization", "").replace("Bearer ", "")
 user = _saas_user(auth) if auth else None
 if not user: return self._json({"err": "invalid_credentials"}, 401)
 self._json({"user_id": user["user_id"], "name": user["name"],
 "balance_cny": user["balance_cny"], "total_spent_cny": user.get("total_spent_cny", 0),
 "total_calls": user.get("total_calls", 0)})
 elif p == '/v1/payment/info':
 auth = self.headers.get("Authorization", "").replace("Bearer ", "")
 data = _load_saas()
 uid = data["credentials"].get(auth) if auth else None
 if not uid: return self._json({"err": "invalid_credentials"}, 401)
 self._json({
 "user_id": uid,
 "alipay": "contact@atex.example.com",
 "paypal": "https://paypal.me/atexproject",
 "min_topup_cny": 10.0,
 "note": f"支付宝转账请备注: ATEX_{uid}，转账后联系管理员确认到账",
 "steps": [
 "1. 支付宝转账至官方账户，金额≥10元",
 f"2. 转账备注: ATEX_{uid}",
 "3. 联系管理员确认到账",
 "4. 余额自动更新",
 ],
 })

 elif p == '/api/v1/status': self._json(exchange.status())
 elif p == '/api/v1/orderbook': self._json(exchange.query_orderbook())
 elif p == '/api/v1/trades': self._json(exchange.trade_history())
 elif p.startswith('/api/v1/account/'):
 self._json(exchange.get_account(p.split('/')[-1]) or {"err":"not_found"})
 elif p == '/api/v1/services':
 self._json(exchange.list_services())
 elif p == '/api/v1/apis':
 self._json(exchange.list_apis())
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

 if p == '/v1/chat/completions':
 auth = self.headers.get("Authorization", "").replace("Bearer ", "")
 user = _saas_user(auth) if auth else None
 if not user: return self._json({"err": "invalid_credentials", "message": "Invalid API key. Get one at http://150.158.119.19:8420"}, 401)
 model = d.get("model", "deepseek-chat")
 model_info = SAAS_PRICING.get(model)
 if not model_info: return self._json({"err": f"unknown_model:{model}", "available": list(SAAS_PRICING.keys())}, 400)
 if model_info.get("status") == "coming_soon":
 return self._json({"err": f"model_coming_soon:{model}", "message": f"{model_info['name']} is coming soon. Register as a provider to offer it."}, 400)
 messages = d.get("messages", [])
 prompt = messages[-1].get("content", "") if messages else ""
 result = execute_api_proxy(model_info.get("backend", "deepseek") + "_chat" if model_info.get("backend") == "deepseek" else model, {"prompt": prompt, "messages": messages})
 if "err" in result:
 return self._json({"err": "api_error", "message": result["err"]}, 500)
 content = result.get("content", "")
 usage = result.get("usage", {})
 input_tokens = usage.get("prompt_tokens", len(prompt) // 4)
 output_tokens = usage.get("completion_tokens", len(content) // 4)
 cost_cny = round(model_info["input_per_1k"] * input_tokens / 1000 + model_info["output_per_1k"] * output_tokens / 1000, 6)
 cost_cny = max(cost_cny, 0.001)
 if not _deduct(user["user_id"], cost_cny, model, input_tokens, output_tokens):
 return self._json({"err": "insufficient_balance", "balance_cny": user["balance_cny"], "cost_cny": cost_cny}, 402)
 self._json({
 "ok": True, "object": "chat.completion",
 "model": model, "created": int(time.time()),
 "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
 "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens, "total_tokens": input_tokens + output_tokens},
 "cost_cny": cost_cny, "remaining_balance_cny": round(user["balance_cny"] - cost_cny, 6)
 })

 elif p == '/v1/register':
 name = d.get("name", "")
 email = d.get("email", "")
 if not name: return self._json({"err": "name_required"}, 400)
 data = _load_saas()
 uid = f"u_{__import__("rng").token_hex(6)}"
 auth_token = f"atex_sk_{os.urandom(24).hex()[:48]}"
 data["users"][uid] = {"user_id": uid, "name": name, "email": email,
 "credential": auth_token, "balance_cny": 0.0, "total_spent_cny": 0.0, "total_calls": 0,
 "created": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")}
 data["credentials"][auth_token] = uid
 _save_saas(data)
 self._json({"ok": True, "user_id": uid, "credential": auth_token, "balance_cny": 0.0,
 "note": "Top up at http://150.158.119.19:8420 to start using APIs"})

 elif p == '/v1/topup':
 topup_uid = d.get("user_id", "")
 amount = d.get("amount_cny", 0)
 if not topup_uid or amount <= 0: return self._json({"err": "user_id and positive amount required"}, 400)
 data = _load_saas()
 user = data["users"].get(topup_uid)
 if not user: return self._json({"err": "user_not_found"}, 404)
 user["balance_cny"] = round(user["balance_cny"] + amount, 2)
 _save_saas(data)
 self._json({"ok": True, "user_id": topup_uid, "balance_cny": user["balance_cny"], "topup": amount})

 elif p == '/api/v1/account/create':
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
 elif p == '/api/v1/services/register':
 r = exchange.register_service(d.get("provider",""), d.get("name",""),
 d.get("description",""), d.get("price",0), d.get("unit",""), d.get("category",""))
 self._json(r, 200 if r.get("ok") else 400)
 elif p == '/api/v1/services/buy':
 r = exchange.buy_service(d.get("buyer",""), d.get("service_id",""), d.get("quantity",1))
 if r.get("ok"):
 svc_params = d.get("params", {})
 exec_result = execute_service(d.get("service_id",""), svc_params, d.get("buyer",""))
 r["service_result"] = exec_result
 self._json(r, 200 if r.get("ok") else 400)
 elif p == '/api/v1/services/execute':
 api_name = d.get("api", "")
 account = d.get("account", "")
 params = d.get("params", {})
 if api_name:
 r = exchange.api_proxy(account, api_name, params)
 if r.get("ok"):
 exec_result = execute_api_proxy(api_name, params)
 r["api_result"] = exec_result
 self._json(r, 200 if r.get("ok") else 400)
 else:
 r = execute_service(d.get("service_id",""), params, account)
 self._json(r, 200 if r.get("ok") else 400)
 elif p == '/api/v1/services/update':
 r = exchange.update_service(d.get("provider",""), d.get("service_id",""),
 name=d.get("name"), description=d.get("description"), price=d.get("price"),
 unit=d.get("unit"), category=d.get("category"), status=d.get("status"))
 self._json(r, 200 if r.get("ok") else 400)
 elif p == '/api/v1/services/remove':
 r = exchange.remove_service(d.get("provider",""), d.get("service_id",""))
 self._json(r, 200 if r.get("ok") else 400)
 elif p == '/api/v1/deploy':
 deploy_token = d.get("token", "")
 if deploy_token != "atex_deploy_2026":
 return self._json({"err": "unauthorized"}, 403)
 action = d.get("action", "")
 if action == "pull_and_restart":
 import subprocess
 try:
 install_dir = os.environ.get("ATEX_HOME", "/opt/atex")
 r1 = subprocess.run(["curl", "-L", "https://ghfast.top/https://github.com/lm203688/atex/archive/refs/heads/main.tar.gz", "-o", "/tmp/atex_latest.tar.gz"], capture_output=True, timeout=120)
 r2 = subprocess.run(["tar", "xzf", "/tmp/atex_latest.tar.gz", "-C", "/tmp/"], capture_output=True, timeout=30)
 r3 = subprocess.run(["cp", "-r", "/tmp/atex-main/token_exchange/.", install_dir + "/"], capture_output=True, timeout=10)
 subprocess.run(["rm", "-rf", "/tmp/atex-main", "/tmp/atex_latest.tar.gz"], capture_output=True, timeout=5)
 subprocess.run(["bash", "-c", f"fuser -k 8420/tcp; sleep 2; nohup python3 {install_dir}/api/server.py > /dev/null 2>&1 &"], capture_output=True, timeout=15)
 self._json({"ok": True, "message": "Code updated and service restarted."})
 except Exception as e:
 self._json({"ok": False, "err": str(e)})
 else:
 self._json({"err": "unknown_action"})

 elif p == '/api/v1/settle':
 r = exchange.settle(d.get("account",""), d.get("amount",0))
 self._json(r, 200 if r.get("ok") else 400)
 else: self._json({"err":"not_found"}, 404)
 def _proto(self):
 return self._json({
 "name": "ATEX", "version": "5.3",
 "description": "多AI API按次计费SaaS + Agent服务交易市场 — 一个API Key调多种AI模型，按次计费",
 "endpoints": {
 "GET": ["/api/v1/status","/api/v1/orderbook","/api/v1/trades",
 "/api/v1/account/{id}","/api/v1/services","/api/v1/services/{id}",
 "/api/v1/apis","/api/v1/protocol",
 "/v1/models","/v1/balance","/v1/payment/info"],
 "POST": ["/api/v1/account/create","/api/v1/deposit","/api/v1/order",
 "/api/v1/cancel","/api/v1/settle",
 "/api/v1/services/register","/api/v1/services/buy",
 "/api/v1/services/execute","/api/v1/services/update","/api/v1/services/remove",
 "/v1/register","/v1/topup","/v1/chat/completions","/api/v1/deploy"]
 },
 "commission": {"maker":0.03,"taker":0.05},
 "matching": "price_time_priority",
 "pricing": "market_driven (orderbook determines ATEX price, no fixed rate)",
 "token_nature": "ATEX is a freely tradable API credit token. Agents spend their own tokens — not purchased from the platform. Acquire ATEX via external trading, providing services, or registration trial credit.",
 "service_marketplace": "fixed_price_direct_transfer_with_execution",
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
 print(f"ATEX v6.0 (SaaS+Marketplace) on 0.0.0.0:{port}", flush=True)
 server.serve_forever()
