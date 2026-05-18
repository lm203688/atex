#!/usr/bin/env python3
"""ATEX HTTP API v5.6 — 多AI API按次收费SaaS + Agent服务交易市场 + 订阅制"""
import json, os, sys, time, threading, hashlib, secrets
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
 return {"users": {}, "api_keys": {}, "usage": [], "topup_requests": []}

def _save_saas(data):
 path = os.path.join(SAAS_DATA, "users.json")
 with open(path, "w") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def _load_topup_requests():
 path = os.path.join(SAAS_DATA, "topup_requests.json")
 if os.path.exists(path):
 with open(path) as f: return json.load(f)
 return {"pending": [], "completed": []}

def _save_topup_requests(data):
 path = os.path.join(SAAS_DATA, "topup_requests.json")
 with open(path, "w") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def _saas_user(api_key):
 data = _load_saas()
 uid = data["api_keys"].get(api_key)
 if not uid: return None
 return data["users"].get(uid)

def _deduct(uid, cost_cny, model, input_tokens, output_tokens):
 data = _load_saas()
 user = data["users"].get(uid)
 if not user: return False
 sub = user.get("subscription", {})
 plan_id = sub.get("plan", "free")
 if plan_id != "free" and sub.get("expires", "") > datetime.now(TZ).strftime("%Y-%m-%d"):
 plan_cfg = _get_plan(plan_id)
 if plan_cfg:
 model_limit = plan_cfg.get("limits", {}).get(model, plan_cfg.get("limits", {}).get("all_models", 0))
 if model_limit == "unlimited":
 user["total_calls"] = user.get("total_calls", 0) + 1
 data["usage"].append({
 "user_id": uid, "model": model,
 "input_tokens": input_tokens, "output_tokens": output_tokens,
 "cost_cny": 0, "subscription": plan_id,
 "time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
 })
 if len(data["usage"]) > 10000: data["usage"] = data["usage"][-5000:]
 _save_saas(data)
 return True
 elif isinstance(model_limit, int) and model_limit > 0:
 month_key = datetime.now(TZ).strftime("%Y-%m")
 usage_key = f"sub_usage_{month_key}"
 used = sub.get(usage_key, {}).get(model, 0)
 if used < model_limit:
 if usage_key not in sub: sub[usage_key] = {}
 sub[usage_key][model] = used + 1
 user["total_calls"] = user.get("total_calls", 0) + 1
 data["usage"].append({
 "user_id": uid, "model": model,
 "input_tokens": input_tokens, "output_tokens": output_tokens,
 "cost_cny": 0, "subscription": plan_id,
 "time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
 })
 if len(data["usage"]) > 10000: data["usage"] = data["usage"][-5000:]
 _save_saas(data)
 return True
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

def _get_plan(plan_id):
 plans = exchange.config.get("subscription_plans", {}).get("plans", [])
 for p in plans:
 if p.get("id") == plan_id:
 return p
 return None

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
 def handle_one_request(self):
 try:
 super().handle_one_request()
 except Exception as e:
 try:
 self._json({"err":"internal_error","message":str(e)}, 500)
 except:
 pass
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
 if not user: return self._json({"err": "invalid_api_key"}, 401)
 self._json({"user_id": user["user_id"], "name": user["name"],
 "balance_cny": user["balance_cny"], "total_spent_cny": user.get("total_spent_cny", 0),
 "total_calls": user.get("total_calls", 0)})
 elif p == '/v1/payment/info':
 auth = self.headers.get("Authorization", "").replace("Bearer ", "")
 data = _load_saas()
 uid = data["api_keys"].get(auth) if auth else None
 if not uid: return self._json({"err": "invalid_api_key"}, 401)
 user = data["users"].get(uid, {})
 bonus_cfg = exchange.config.get("payment", {}).get("topup_bonus", {})
 bonus_active = bonus_cfg.get("active", False)
 is_first = user.get("total_topup_count", 0) == 0
 result = {
 "user_id": uid,
 "alipay": "demo@example.com",
 "paypal": "https://paypal.me/xinglixingli",
 "min_topup_cny": 10.0,
 "note": f"支付宝转账请备注: ATEX_{uid}，转账后联系管理员确认到账",
 "steps": [
 "1. 支付宝转账至 demo@example.com，金额≥10元",
 f"2. 转账备注: ATEX_{uid}",
 "3. 联系管理员确认到账",
 "4. 余额自动更新（含赠送积分）",
 ],
 }
 if bonus_active:
 tiers = bonus_cfg.get("tiers", [])
 result["bonus_promotion"] = {
 "active": True,
 "expires": bonus_cfg.get("expires", ""),
 "description": bonus_cfg.get("description", ""),
 "tiers": tiers,
 "first_topup_bonus_atex": bonus_cfg.get("first_topup_bonus_atex", 0) if is_first else 0,
 "topup_atex_rate": bonus_cfg.get("topup_atex_rate", 0),
 "is_first_topup": is_first,
 }
 self._json(result)

 elif p == '/v1/bonus/info':
 bonus_cfg = exchange.config.get("payment", {}).get("topup_bonus", {})
 auth = self.headers.get("Authorization", "").replace("Bearer ", "")
 is_first = True
 if auth:
 saas_data = _load_saas()
 uid = saas_data["api_keys"].get(auth)
 if uid:
 is_first = saas_data["users"].get(uid, {}).get("total_topup_count", 0) == 0
 self._json({
 "promotion": bonus_cfg if bonus_cfg.get("active") else {"active": False},
 "your_first_topup_bonus_atex": bonus_cfg.get("first_topup_bonus_atex", 0) if (bonus_cfg.get("active") and is_first) else 0,
 "is_first_topup": is_first,
 "examples": [
 {"topup_cny": 10, "bonus_cny": 1, "bonus_atex": 5, "note": "充10送1元+5ATEX"},
 {"topup_cny": 100, "bonus_cny": 20, "bonus_atex": 50, "note": "充100送20元+50ATEX"},
 {"topup_cny": 500, "bonus_cny": 150, "bonus_atex": 250, "note": "充500送150元+250ATEX"},
 {"topup_cny": 1000, "bonus_cny": 400, "bonus_atex": 500, "note": "充1000送400元+500ATEX"},
 ] if bonus_cfg.get("active") else [],
 })

 elif p == '/v1/subscription/plans':
 sub_cfg = exchange.config.get("subscription_plans", {})
 plans = sub_cfg.get("plans", [])
 result = {
 "active": sub_cfg.get("active", False),
 "trial_days": sub_cfg.get("trial_days", 0),
 "trial_plan": sub_cfg.get("trial_plan", ""),
 "plans": []
 }
 for plan in plans:
 result["plans"].append({
 "id": plan["id"],
 "name": plan["name"],
 "price_cny": plan["price_cny"],
 "period": plan["period"],
 "features": plan["features"],
 "bonus_atex": plan.get("bonus_atex", 0),
 "highlight": plan.get("highlight", ""),
 })
 self._json(result)

 elif p == '/v1/subscription/status':
 auth = self.headers.get("Authorization", "").replace("Bearer ", "")
 if not auth: return self._json({"err": "authorization_required"}, 401)
 data = _load_saas()
 uid = data["api_keys"].get(auth)
 if not uid: return self._json({"err": "invalid_api_key"}, 401)
 user = data["users"].get(uid, {})
 sub = user.get("subscription", {})
 plan_id = sub.get("plan", "free")
 if plan_id != "free" and sub.get("expires", "") < datetime.now(TZ).strftime("%Y-%m-%d"):
 sub["plan"] = "free"
 sub["plan_name"] = "免费版"
 sub["expired"] = True
 _save_saas(data)
 plan_id = "free"
 plan = _get_plan(plan_id) or _get_plan("free")
 self._json({
 "user_id": uid,
 "plan": plan_id,
 "plan_name": sub.get("plan_name", plan.get("name", "免费版")),
 "started": sub.get("started", ""),
 "expires": sub.get("expires", ""),
 "auto_renew": sub.get("auto_renew", False),
 "features": plan.get("features", []),
 "bonus_atex_monthly": plan.get("bonus_atex", 0),
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
 if not user: return self._json({"err": "invalid_api_key", "message": "Invalid API key. Get one at http://your-server-ip:8420"}, 401)
 model = d.get("model", "deepseek-chat")
 model_info = SAAS_PRICING.get(model)
 if not model_info: return self._json({"err": f"unknown_model:{model}", "available": list(SAAS_PRICING.keys())}, 400)
 if model_info.get("status") == "coming_soon":
 return self._json({"err": f"model_coming_soon:{model}", "message": f"{model_info['name']} is coming soon. Register as a provider to offer it."}, 400)
 min_cost = 0.001
 if user["balance_cny"] < min_cost:
 return self._json({"err": "insufficient_balance", "balance_cny": user["balance_cny"]}, 402)
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
 data = _load_saas()
 data.setdefault("bad_debt", 0)
 data["bad_debt"] = round(data["bad_debt"] + cost_cny, 6)
 _save_saas(data)
 return self._json({"err": "insufficient_balance", "balance_cny": user["balance_cny"], "cost_cny": cost_cny}, 402)
 self._json({
 "ok": True, "object": "chat.completion",
 "model": model, "created": int(time.time()),
 "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
 "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens, "total_tokens": input_tokens + output_tokens},
 "cost_cny": cost_cny, "remaining_balance_cny": round(user["balance_cny"], 6)
 })

 elif p == '/v1/register':
 name = d.get("name", "")
 email = d.get("email", "")
 if not name: return self._json({"err": "name_required"}, 400)
 data = _load_saas()
 uid = f"u_{secrets.token_hex(6)}"
 api_key = f"atex_sk_{secrets.token_hex(24)}"
 welcome_cny = 5.0
 sub_cfg = exchange.config.get("subscription_plans", {})
 trial_days = sub_cfg.get("trial_days", 3)
 trial_plan = sub_cfg.get("trial_plan", "basic")
 trial_plan_cfg = _get_plan(trial_plan) or {}
 trial_expires = (datetime.now(TZ) + timedelta(days=trial_days)).strftime("%Y-%m-%d")
 data["users"][uid] = {"user_id": uid, "name": name, "email": email,
 "api_key": api_key, "balance_cny": welcome_cny, "total_spent_cny": 0.0, "total_calls": 0,
 "total_topup_count": 0, "total_topup_cny": 0.0,
 "subscription": {
 "plan": trial_plan,
 "plan_name": trial_plan_cfg.get("name", "基础版试用"),
 "started": datetime.now(TZ).strftime("%Y-%m-%d"),
 "expires": trial_expires,
 "auto_renew": False,
 "is_trial": True,
 },
 "created": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")}
 data["api_keys"][api_key] = uid
 _save_saas(data)
 self._json({"ok": True, "user_id": uid, "api_key": api_key, "balance_cny": welcome_cny,
 "welcome_bonus": f"注册即送{welcome_cny}元体验金",
 "subscription_trial": f"{trial_days}天{trial_plan_cfg.get('name','基础版')}免费试用",
 "trial_expires": trial_expires,
 "note": "Top up at http://your-server-ip:8420 to get more credits + bonus ATEX tokens!"})

 elif p == '/v1/topup/apply':
 auth = self.headers.get("Authorization", "").replace("Bearer ", "")
 user = _saas_user(auth) if auth else None
 if not user: return self._json({"err": "invalid_api_key"}, 401)
 amount = d.get("amount_cny", 0)
 if amount < 10: return self._json({"err": "min_topup_10_cny"}, 400)
 ref_code = f"ATX{secrets.token_hex(3).upper()}"
 bonus_cfg = exchange.config.get("payment", {}).get("topup_bonus", {})
 bonus_active = bonus_cfg.get("active", False)
 bonus_pct = 0
 bonus_note = ""
 if bonus_active:
 for t in sorted(bonus_cfg.get("tiers", []), key=lambda x: x.get("min_cny", 0)):
 if amount >= t.get("min_cny", 0):
 bonus_pct = t.get("bonus_pct", 0)
 bonus_note = t.get("note", "")
 bonus_cny = round(amount * bonus_pct / 100, 2) if bonus_pct > 0 else 0
 atex_rate = bonus_cfg.get("topup_atex_rate", 0) if bonus_active else 0
 atex_from_topup = round(amount * atex_rate, 2)
 is_first = user.get("total_topup_count", 0) == 0
 first_bonus = bonus_cfg.get("first_topup_bonus_atex", 0) if (bonus_active and is_first) else 0
 total_atex = atex_from_topup + first_bonus
 req_data = _load_topup_requests()
 request_record = {
 "ref_code": ref_code,
 "user_id": user["user_id"],
 "user_name": user.get("name", ""),
 "amount_cny": amount,
 "bonus_cny": bonus_cny,
 "bonus_atex": total_atex,
 "status": "pending",
 "created": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
 }
 req_data["pending"].append(request_record)
 _save_topup_requests(req_data)
 self._json({
 "ok": True,
 "ref_code": ref_code,
 "amount_cny": amount,
 "bonus_cny": bonus_cny,
 "bonus_atex": total_atex,
 "total_credited_cny": round(amount + bonus_cny, 2),
 "is_first_topup": is_first,
 "payment": {
 "alipay": "demo@example.com",
 "paypal": "https://paypal.me/xinglixingli",
 "note": f"请转账{amount}元，备注填写参考码：{ref_code}",
 "steps": [
 f"1. 支付宝转账至 demo@example.com",
 f"2. 转账金额：{amount}元",
 f"3. 转账备注：{ref_code}",
 "4. 管理员确认后余额自动到账（含赠送）",
 ],
 },
 })

 elif p == '/v1/topup/status':
 auth = self.headers.get("Authorization", "").replace("Bearer ", "")
 user = _saas_user(auth) if auth else None
 if not user: return self._json({"err": "invalid_api_key"}, 401)
 req_data = _load_topup_requests()
 my_pending = [r for r in req_data["pending"] if r["user_id"] == user["user_id"]]
 my_completed = [r for r in req_data["completed"] if r["user_id"] == user["user_id"]][-10:]
 self._json({
 "pending": my_pending,
 "completed": my_completed,
 "balance_cny": user.get("balance_cny", 0),
 })

 elif p == '/v1/topup':
 admin_token = d.get("admin_token", "")
 if admin_token != "atex_admin_2026":
 return self._json({"err": "unauthorized", "note": "需要管理员token"}, 403)
 ref_code = d.get("ref_code", "")
 confirmed_amount = d.get("amount_cny", 0)
 if not ref_code: return self._json({"err": "ref_code_required"}, 400)
 req_data = _load_topup_requests()
 target = None
 for r in req_data["pending"]:
 if r["ref_code"] == ref_code:
 target = r
 break
 if not target:
 return self._json({"err": "ref_code_not_found", "pending_count": len(req_data["pending"])}, 404)
 actual_amount = confirmed_amount if confirmed_amount > 0 else target["amount_cny"]
 bonus_cfg = exchange.config.get("payment", {}).get("topup_bonus", {})
 bonus_active = bonus_cfg.get("active", False)
 bonus_pct = 0
 bonus_note = ""
 if bonus_active:
 for t in sorted(bonus_cfg.get("tiers", []), key=lambda x: x.get("min_cny", 0)):
 if actual_amount >= t.get("min_cny", 0):
 bonus_pct = t.get("bonus_pct", 0)
 bonus_note = t.get("note", "")
 bonus_cny = round(actual_amount * bonus_pct / 100, 2) if bonus_pct > 0 else 0
 total_cny = round(actual_amount + bonus_cny, 2)
 saas_data = _load_saas()
 user = saas_data["users"].get(target["user_id"])
 if not user:
 return self._json({"err": "user_not_found"}, 404)
 user["balance_cny"] = round(user.get("balance_cny", 0) + total_cny, 2)
 user["total_topup_count"] = user.get("total_topup_count", 0) + 1
 user["total_topup_cny"] = round(user.get("total_topup_cny", 0) + actual_amount, 2)
 atex_bonus = 0
 atex_details = []
 if bonus_active:
 atex_rate = bonus_cfg.get("topup_atex_rate", 0)
 atex_from_topup = round(actual_amount * atex_rate, 2)
 if atex_from_topup > 0:
 atex_bonus += atex_from_topup
 atex_details.append(f"充值送ATEX: {atex_from_topup}")
 is_first = user.get("total_topup_count", 1) == 1
 first_bonus = bonus_cfg.get("first_topup_bonus_atex", 0) if is_first else 0
 if first_bonus > 0:
 atex_bonus += first_bonus
 atex_details.append(f"首次充值奖励: {first_bonus} ATEX")
 atex_result = None
 if atex_bonus > 0:
 if target["user_id"] in exchange.accounts.get("accounts", {}):
 exchange.accounts["accounts"][target["user_id"]]["balance"] = round(
 exchange.accounts["accounts"][target["user_id"]].get("balance", 0) + atex_bonus, 2)
 exchange._save()
 atex_result = {"deposited": atex_bonus, "details": atex_details}
 else:
 atex_result = {"pending": atex_bonus, "details": atex_details}
 _save_saas(saas_data)
 target["status"] = "completed"
 target["confirmed_at"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
 target["actual_amount_cny"] = actual_amount
 target["bonus_cny"] = bonus_cny
 target["bonus_atex"] = atex_bonus
 req_data["pending"].remove(target)
 req_data["completed"].append(target)
 _save_topup_requests(req_data)
 result = {
 "ok": True, "ref_code": ref_code,
 "user_id": target["user_id"], "user_name": target.get("user_name", ""),
 "topup_cny": actual_amount, "bonus_cny": bonus_cny, "total_credited_cny": total_cny,
 "balance_cny": user["balance_cny"],
 }
 if bonus_note: result["bonus_note"] = bonus_note
 if atex_result: result["atex_bonus"] = atex_result
 self._json(result)

 elif p == '/v1/topup/admin/list':
 admin_token = d.get("admin_token", "")
 if admin_token != "atex_admin_2026":
 return self._json({"err": "unauthorized"}, 403)
 req_data = _load_topup_requests()
 self._json({
 "pending": req_data["pending"],
 "completed_count": len(req_data["completed"]),
 "recent_completed": req_data["completed"][-10:],
 })

 elif p == '/v1/subscription/subscribe':
 uid = d.get("user_id", "")
 plan_id = d.get("plan_id", "")
 if not uid or not plan_id: return self._json({"err": "user_id and plan_id required"}, 400)
 plan = _get_plan(plan_id)
 if not plan: return self._json({"err": "invalid_plan_id", "available": ["free","basic","pro","enterprise"]}, 400)
 if plan["price_cny"] == 0: return self._json({"err": "free_plan_no_subscription_needed"}, 400)
 data = _load_saas()
 user = data["users"].get(uid)
 if not user: return self._json({"err": "user_not_found"}, 404)
 expires = (datetime.now(TZ) + timedelta(days=30)).strftime("%Y-%m-%d")
 user["subscription"] = {
 "plan": plan_id,
 "plan_name": plan["name"],
 "started": datetime.now(TZ).strftime("%Y-%m-%d"),
 "expires": expires,
 "auto_renew": True,
 }
 bonus = plan.get("bonus_atex", 0)
 if bonus > 0 and uid in exchange.accounts.get("accounts", {}):
 exchange.accounts["accounts"][uid]["balance"] = round(
 exchange.accounts["accounts"][uid].get("balance", 0) + bonus, 2)
 exchange._save()
 _save_saas(data)
 self._json({
 "ok": True, "user_id": uid,
 "plan": plan_id, "plan_name": plan["name"],
 "price_cny": plan["price_cny"], "period": plan["period"],
 "expires": expires,
 "bonus_atex": bonus,
 "features": plan["features"],
 "note": "订阅已激活。自动扣费功能开发中，当前需管理员确认付款。"
 })

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
 "name": "ATEX", "version": "5.6",
 "description": "多AI API按次计费SaaS + Agent服务交易市场 — 一个API Key调多种AI模型，按次计费",
 "endpoints": {
 "GET": ["/api/v1/status","/api/v1/orderbook","/api/v1/trades",
 "/api/v1/account/{id}","/api/v1/services","/api/v1/services/{id}",
 "/api/v1/apis","/api/v1/protocol",
 "/v1/models","/v1/balance","/v1/payment/info","/v1/bonus/info","/v1/subscription/plans","/v1/subscription/status"],
 "POST": ["/api/v1/account/create","/api/v1/deposit","/api/v1/order",
 "/api/v1/cancel","/api/v1/settle",
 "/api/v1/services/register","/api/v1/services/buy",
 "/api/v1/services/execute","/api/v1/services/update","/api/v1/services/remove",
 "/v1/register","/v1/topup","/v1/topup/apply","/v1/topup/status","/v1/topup/admin/list","/v1/chat/completions","/v1/subscription/subscribe","/api/v1/deploy"]
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
 print(f"ATEX v5.6 (SaaS+Marketplace) on 0.0.0.0:{port}", flush=True)
 server.serve_forever()
