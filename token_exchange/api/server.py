#!/usr/bin/env python3
"""ATEX HTTP API v5.11 — 多AI API按次收费SaaS + Agent服务交易市场 + 订阅制 + Agent自发现"""
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

# ── SaaS用户系统 ──
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
    # 订阅用户：检查免费额度
    sub = user.get("subscription", {})
    plan_id = sub.get("plan", "free")
    if plan_id != "free" and sub.get("expires", "") > datetime.now(TZ).strftime("%Y-%m-%d"):
        # 订阅有效，检查模型限额
        plan_cfg = _get_plan(plan_id)
        if plan_cfg:
            model_limit = plan_cfg.get("limits", {}).get(model, plan_cfg.get("limits", {}).get("all_models", 0))
            if model_limit == "unlimited":
                # 无限量，不扣费，只记录
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
                # 有月限额，检查已用次数
                month_key = datetime.now(TZ).strftime("%Y-%m")
                usage_key = f"sub_usage_{month_key}"
                used = sub.get(usage_key, {}).get(model, 0)
                if used < model_limit:
                    # 还在限额内，不扣费
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
                # 超出限额，按次扣费
    # 非订阅或超出限额：按次扣费
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

# ── SaaS定价 ──
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

        # ── SaaS路由（OpenAI兼容）──
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
                "alipay": "lx688@sina.com",
                "paypal": "https://paypal.me/xinglixingli",
                "min_topup_cny": 10.0,
                "note": f"支付宝转账请备注: ATEX_{uid}，转账后联系管理员确认到账",
                "steps": [
                    "1. 支付宝转账至 lx688@sina.com，金额≥10元",
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
            # 查询充值送积分活动详情
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
            # 查看订阅方案
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
            # 查询订阅状态
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            if not auth: return self._json({"err": "authorization_required"}, 401)
            data = _load_saas()
            uid = data["api_keys"].get(auth)
            if not uid: return self._json({"err": "invalid_api_key"}, 401)
            user = data["users"].get(uid, {})
            sub = user.get("subscription", {})
            plan_id = sub.get("plan", "free")
            # 检查是否过期
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

        # ── 原ATEX路由 ──
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
        # ── Agent自发现协议 ──
        elif p == '/.well-known/agent.json': self._agent_discovery()
        elif p == '/.well-known/ai-plugin.json': self._ai_plugin_manifest()
        elif p == '/api/v1/agent/tools.json': self._agent_tools()
        elif p == '/api/v1/openapi.json': self._openapi_spec()
        # ── MCP协议端点（Streamable HTTP）──
        elif p == '/mcp': self._mcp_get()
        elif p == '/.well-known/mcp/server-card.json': self._mcp_server_card()
        else: self._json({"err":"not_found"}, 404)
    def do_POST(self):
        if not ip_limiter.check(self._ip()): return self._json({"err":"rate_limited"}, 429)
        p = urlparse(self.path).path
        d = self._read()
        if not d: return self._json({"err":"invalid_body"}, 400)

        # ── SaaS路由（OpenAI兼容）──
        if p == '/v1/chat/completions':
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            user = _saas_user(auth) if auth else None
            if not user: return self._json({"err": "invalid_api_key", "message": "Invalid API key. Get one at http://150.158.119.19:8420"}, 401)
            model = d.get("model", "deepseek-chat")
            model_info = SAAS_PRICING.get(model)
            if not model_info: return self._json({"err": f"unknown_model:{model}", "available": list(SAAS_PRICING.keys())}, 400)
            if model_info.get("status") == "coming_soon":
                return self._json({"err": f"model_coming_soon:{model}", "message": f"{model_info['name']} is coming soon. Register as a provider to offer it."}, 400)
            # 先检查余额是否足够（最低估算，防止API白调）
            min_cost = 0.001
            if user["balance_cny"] < min_cost:
                return self._json({"err": "insufficient_balance", "balance_cny": user["balance_cny"]}, 402)
            # 调用底层API
            messages = d.get("messages", [])
            prompt = messages[-1].get("content", "") if messages else ""
            result = execute_api_proxy(model_info.get("backend", "deepseek") + "_chat" if model_info.get("backend") == "deepseek" else model, {"prompt": prompt, "messages": messages})
            if "err" in result:
                return self._json({"err": "api_error", "message": result["err"]}, 500)
            # 计费
            content = result.get("content", "")
            usage = result.get("usage", {})
            input_tokens = usage.get("prompt_tokens", len(prompt) // 4)
            output_tokens = usage.get("completion_tokens", len(content) // 4)
            cost_cny = round(model_info["input_per_1k"] * input_tokens / 1000 + model_info["output_per_1k"] * output_tokens / 1000, 6)
            cost_cny = max(cost_cny, 0.001)
            if not _deduct(user["user_id"], cost_cny, model, input_tokens, output_tokens):
                # 余额不足但API已调用 — 记录坏账
                data = _load_saas()
                data.setdefault("bad_debt", 0)
                data["bad_debt"] = round(data["bad_debt"] + cost_cny, 6)
                _save_saas(data)
                return self._json({"err": "insufficient_balance", "balance_cny": user["balance_cny"], "cost_cny": cost_cny}, 402)
            # 返回OpenAI格式
            self._json({
                "ok": True, "object": "chat.completion",
                "model": model, "created": int(time.time()),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens, "total_tokens": input_tokens + output_tokens},
                "cost_cny": cost_cny, "remaining_balance_cny": round(user["balance_cny"], 6)
            })

        elif p == '/v1/register':
            # SaaS用户注册 — 含注册赠送
            name = d.get("name", "")
            email = d.get("email", "")
            if not name: return self._json({"err": "name_required"}, 400)
            data = _load_saas()
            uid = f"u_{secrets.token_hex(6)}"
            api_key = f"atex_sk_{secrets.token_hex(24)}"
            # 注册赠送：5元体验金
            welcome_cny = 5.0
            # 3天基础版试用
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
                "note": "Top up at http://150.158.119.19:8420 to get more credits + bonus ATEX tokens!"})

        elif p == '/v1/topup/apply':
            # 第一步：用户提交充值申请 → 生成参考码 + 支付指引
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            user = _saas_user(auth) if auth else None
            if not user: return self._json({"err": "invalid_api_key"}, 401)
            amount = d.get("amount_cny", 0)
            if amount < 10: return self._json({"err": "min_topup_10_cny"}, 400)
            # 生成6位参考码
            ref_code = f"ATX{secrets.token_hex(3).upper()}"
            # 计算赠送预览
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
            # 保存申请记录
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
                    "alipay": "lx688@sina.com",
                    "paypal": "https://paypal.me/xinglixingli",
                    "note": f"请转账{amount}元，备注填写参考码：{ref_code}",
                    "steps": [
                        f"1. 支付宝转账至 lx688@sina.com",
                        f"2. 转账金额：{amount}元",
                        f"3. 转账备注：{ref_code}",
                        "4. 管理员确认后余额自动到账（含赠送）",
                    ],
                },
            })

        elif p == '/v1/topup/status':
            # 第二步：用户查询自己的充值记录
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
            # 第三步：管理员确认到账（需admin token）
            admin_token = d.get("admin_token", "")
            if admin_token != "atex_admin_2026":
                return self._json({"err": "unauthorized", "note": "需要管理员token"}, 403)
            ref_code = d.get("ref_code", "")
            confirmed_amount = d.get("amount_cny", 0)  # 实际到账金额（可调整）
            if not ref_code: return self._json({"err": "ref_code_required"}, 400)
            req_data = _load_topup_requests()
            # 查找pending记录
            target = None
            for r in req_data["pending"]:
                if r["ref_code"] == ref_code:
                    target = r
                    break
            if not target:
                return self._json({"err": "ref_code_not_found", "pending_count": len(req_data["pending"])}, 404)
            # 用实际到账金额或申请金额
            actual_amount = confirmed_amount if confirmed_amount > 0 else target["amount_cny"]
            # 重新计算赠送
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
            # 更新SaaS余额
            saas_data = _load_saas()
            user = saas_data["users"].get(target["user_id"])
            if not user:
                return self._json({"err": "user_not_found"}, 404)
            user["balance_cny"] = round(user.get("balance_cny", 0) + total_cny, 2)
            user["total_topup_count"] = user.get("total_topup_count", 0) + 1
            user["total_topup_cny"] = round(user.get("total_topup_cny", 0) + actual_amount, 2)
            # ATEX赠送
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
            # 移动到completed
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
            # 管理员查看所有待确认充值
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
            # 订阅（管理接口，后续接支付宝自动扣款）
            uid = d.get("user_id", "")
            plan_id = d.get("plan_id", "")
            if not uid or not plan_id: return self._json({"err": "user_id and plan_id required"}, 400)
            plan = _get_plan(plan_id)
            if not plan: return self._json({"err": "invalid_plan_id", "available": ["free","basic","pro","enterprise"]}, 400)
            if plan["price_cny"] == 0: return self._json({"err": "free_plan_no_subscription_needed"}, 400)
            data = _load_saas()
            user = data["users"].get(uid)
            if not user: return self._json({"err": "user_not_found"}, 404)
            # 设置订阅（实际扣费需接支付宝自动扣款，当前为管理接口）
            expires = (datetime.now(TZ) + timedelta(days=30)).strftime("%Y-%m-%d")
            user["subscription"] = {
                "plan": plan_id,
                "plan_name": plan["name"],
                "started": datetime.now(TZ).strftime("%Y-%m-%d"),
                "expires": expires,
                "auto_renew": True,
            }
            # 发放月度ATEX奖励
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

        # ── 原ATEX路由 ──
        elif p == '/api/v1/account/create':
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
            # API代理执行：先扣费再调用底层API
            api_name = d.get("api", "")
            account = d.get("account", "")
            params = d.get("params", {})
            if api_name:
                # API代理模式：扣费+执行
                r = exchange.api_proxy(account, api_name, params)
                if r.get("ok"):
                    exec_result = execute_api_proxy(api_name, params)
                    r["api_result"] = exec_result
                self._json(r, 200 if r.get("ok") else 400)
            else:
                # 服务执行模式（兼容旧接口）
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
        # ── 部署接口（仅限内网/认证调用）──
        elif p == '/api/v1/deploy':
            deploy_token = d.get("token", "")
            if deploy_token != "atex_deploy_2026":
                return self._json({"err": "unauthorized"}, 403)
            action = d.get("action", "")
            if action == "pull_and_restart":
                import subprocess
                try:
                    install_dir = os.environ.get("ATEX_HOME", "/home/ubuntu/atex")
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

        # ── 结算（仅owner，平台佣金→ATEX）──
        elif p == '/api/v1/settle':
            r = exchange.settle(d.get("account",""), d.get("amount",0))
            self._json(r, 200 if r.get("ok") else 400)
        # ── MCP协议端点（Streamable HTTP）──
        elif p == '/mcp':
            self._mcp_post(d)
        else: self._json({"err":"not_found"}, 404)

    # ── MCP协议处理（Streamable HTTP）──
    def _mcp_server_card(self):
        """GET /.well-known/mcp/server-card.json — Smithery扫描用"""
        self._json({
            "name": "ATEX AI Gateway",
            "description": "One API Key to access 6 AI models (DeepSeek, GPT-4o, Claude). Pay-per-use, OpenAI compatible. MCP protocol support. Web search at 5 ATEX/call.",
            "version": "5.11",
            "url": "http://150.158.119.19:8420/mcp",
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "ATEX AI Gateway", "version": "5.11"},
            "tools": [
                {"name": "chat", "description": "Chat with AI models (DeepSeek, GPT-4o, Claude). Pay-per-use via ATEX API key."},
                {"name": "web_search", "description": "Search the web for real-time information. 5 ATEX per call."},
                {"name": "check_balance", "description": "Check your ATEX account balance and usage."},
                {"name": "list_models", "description": "List available AI models and their pricing."},
                {"name": "list_services", "description": "List all available services in the ATEX marketplace."}
            ]
        })

    def _mcp_get(self):
        """GET /mcp — 返回MCP服务器信息（Smithery扫描用）"""
        self._json({
            "name": "ATEX AI Gateway",
            "version": "5.11",
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "ATEX AI Gateway", "version": "5.11"}
        })

    def _mcp_post(self, d):
        """POST /mcp — MCP JSON-RPC 2.0 处理"""
        method = d.get("method", "")
        req_id = d.get("id")
        params = d.get("params", {})

        # 认证
        auth = self.headers.get("Authorization", "").replace("Bearer ", "")
        user = _saas_user(auth) if auth else None

        if method == "initialize":
            return self._json({
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "ATEX AI Gateway", "version": "5.9.0"}
                }
            })
        elif method == "tools/list":
            tools = [
                {"name": "chat", "description": "Chat with AI models (DeepSeek, GPT-4o, Claude). Pay-per-use via ATEX API key.",
                 "inputSchema": {"type": "object", "properties": {"model": {"type": "string", "enum": list(SAAS_PRICING.keys()), "default": "deepseek-chat"}, "messages": {"type": "array", "items": {"type": "object", "properties": {"role": {"type": "string"}, "content": {"type": "string"}}, "required": ["role","content"]}}}, "required": ["messages"]}},
                {"name": "web_search", "description": "Search the web for real-time information. 5 ATEX per call.",
                 "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}}, "required": ["query"]}},
                {"name": "check_balance", "description": "Check your ATEX account balance and usage.",
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "list_models", "description": "List available AI models and their pricing.",
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "list_services", "description": "List all available services in the ATEX marketplace.",
                 "inputSchema": {"type": "object", "properties": {"category": {"type": "string", "description": "Filter by category"}}}},
            ]
            return self._json({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}})
        elif method == "tools/call":
            tool_name = params.get("name", "")
            args = params.get("arguments", {})
            if tool_name == "chat":
                if not user: return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32001, "message": "Authentication required. Set Authorization: Bearer YOUR_ATEX_API_KEY"}}, 401)
                model = args.get("model", "deepseek-chat")
                messages = args.get("messages", [{"role": "user", "content": args.get("prompt", "")}])
                model_info = SAAS_PRICING.get(model)
                if not model_info: return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": f"Unknown model: {model}"}}, 400)
                if model_info.get("status") == "coming_soon": return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": f"Model {model} coming soon"}})
                prompt = messages[-1].get("content", "") if messages else ""
                result = execute_api_proxy(model_info.get("backend", "deepseek") + "_chat" if model_info.get("backend") == "deepseek" else model, {"prompt": prompt, "messages": messages})
                content = result.get("content", str(result))
                usage = result.get("usage", {})
                input_tokens = usage.get("prompt_tokens", len(prompt)//4)
                output_tokens = usage.get("completion_tokens", len(content)//4)
                cost_cny = round(model_info["input_per_1k"]*input_tokens/1000 + model_info["output_per_1k"]*output_tokens/1000, 6)
                cost_cny = max(cost_cny, 0.001)
                _deduct(user["user_id"], cost_cny, model, input_tokens, output_tokens)
                return self._json({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": content}], "cost_cny": cost_cny}})
            elif tool_name == "web_search":
                if not user: return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32001, "message": "Authentication required"}}, 401)
                query = args.get("query", "")
                result = execute_service("svc_012", {"query": query}, user["user_id"])
                return self._json({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}})
            elif tool_name == "check_balance":
                if not user: return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32001, "message": "Authentication required"}}, 401)
                return self._json({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps({"balance_cny": user["balance_cny"], "total_calls": user.get("total_calls",0)})}]}})
            elif tool_name == "list_models":
                models = [{"id": mid, "name": info["name"], "status": info.get("status","live"), "pricing": {"input_per_1k_cny": info["input_per_1k"], "output_per_1k_cny": info["output_per_1k"]}} for mid, info in SAAS_PRICING.items()]
                return self._json({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(models, ensure_ascii=False)}]}})
            elif tool_name == "list_services":
                svcs = exchange.list_services()
                return self._json({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(svcs, ensure_ascii=False)[:4000]}]}})
            else:
                return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}}, 400)
        elif method == "notifications/initialized":
            # Client notification, no response needed
            return self._json({"jsonrpc": "2.0", "id": req_id, "result": {}})
        else:
            return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}, 400)

    # ── Agent自发现协议 ──
    def _agent_discovery(self):
        """GET /.well-known/agent.json — Agent零配置自发现入口
        融合 JSON-LD 语义标注 + 多协议发现。任何Agent一条请求即可读懂如何注册、认证、调用。
        兼容 OpenAI Plugin / Anthropic Tool Use / MCP / OpenAPI 等协议。
        """
        host = self.headers.get("Host", "150.158.119.19:8420")
        scheme = "https" if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https" else "http"
        base = f"{scheme}://{host}"
        self._json({
            # ── JSON-LD 语义标注 ──
            "@context": {
                "@vocab": "https://schema.atex.dev/",
                "name": "http://schema.org/name",
                "description": "http://schema.org/description",
                "version": "http://schema.org/version",
                "api_base": {"@id": "http://schema.org/endpointURL", "@type": "@id"},
                "auth": "https://schema.atex.dev/auth",
                "protocols": "https://schema.atex.dev/protocols",
                "capabilities": "https://schema.atex.dev/capabilities",
                "AgentService": "https://schema.atex.dev/AgentService",
                "TokenExchange": "https://schema.atex.dev/TokenExchange"
            },
            "@type": ["AgentService", "TokenExchange"],
            "@id": base,
            # ── 基础信息 ──
            "name": "ATEX",
            "description": "Agent Token Exchange — Agent服务交易市场。一个API Key调多种AI模型，按次计费；服务市场买卖Agent服务；Token交易撮合。",
            "version": exchange.config.get("version", "5.11"),
            "api_base": f"{base}/api/v1",
            "homepage": "https://lm203688.github.io/atex/",
            "repository": "https://github.com/lm203688/atex",
            "license": "AGPL-3.0",
            # ── 认证 ──
            "auth": {
                "type": "bearer_token",
                "header": "Authorization",
                "prefix": "Bearer",
                "register": f"{base}/v1/register",
                "register_method": "POST",
                "register_body": {"name": "your_agent_name", "email": "optional"},
                "docs": f"{base}/api/v1/protocol"
            },
            # ── 协议发现 ──
            "protocols": {
                "openai_function_calling": {
                    "spec": "https://platform.openai.com/docs/guides/function-calling",
                    "tools_endpoint": f"{base}/api/v1/agent/tools.json?format=openai",
                    "description": "OpenAI Function Calling format tools list"
                },
                "anthropic_tool_use": {
                    "spec": "https://docs.anthropic.com/en/docs/build-with-claude/tool-use",
                    "tools_endpoint": f"{base}/api/v1/agent/tools.json?format=anthropic",
                    "description": "Anthropic tool_use format tools list"
                },
                "mcp": {
                    "spec": "https://spec.modelcontextprotocol.io/specification/2025-03-26/",
                    "endpoint": f"{base}/mcp",
                    "server_card": f"{base}/.well-known/mcp/server-card.json",
                    "protocol_version": "2025-03-26",
                    "description": "Model Context Protocol - Streamable HTTP transport"
                },
                "openapi": {
                    "spec": "https://spec.openapis.org/oas/v3.1.0",
                    "endpoint": f"{base}/api/v1/openapi.json",
                    "description": "OpenAPI 3.1 specification for REST API discovery"
                },
                "openai_plugin": {
                    "spec": "https://platform.openai.com/docs/plugins/getting-started",
                    "manifest": f"{base}/.well-known/ai-plugin.json",
                    "description": "OpenAI Plugin manifest for ChatGPT integration"
                },
                "rest_api": {
                    "description": "Standard REST JSON API, no SDK required"
                },
                "json_stdin": {
                    "description": "CLI: echo '{\"action\":\"...\"}' | python3 atex.py"
                }
            },
            # ── 能力端点 ──
            "capabilities": {
                "ai_chat": {
                    "endpoint": f"{base}/v1/chat/completions",
                    "method": "POST",
                    "models": list(SAAS_PRICING.keys()),
                    "compatible_with": "OpenAI Chat Completions API",
                    "pricing_unit": "CNY per 1K tokens"
                },
                "service_marketplace": {
                    "list": f"{base}/api/v1/services",
                    "buy": f"{base}/api/v1/services/buy",
                    "register": f"{base}/api/v1/services/register",
                    "tools_schema": f"{base}/api/v1/agent/tools.json"
                },
                "token_trading": {
                    "orderbook": f"{base}/api/v1/orderbook",
                    "place_order": f"{base}/api/v1/order",
                    "cancel_order": f"{base}/api/v1/cancel",
                    "settle": f"{base}/api/v1/settle",
                    "trades": f"{base}/api/v1/trades"
                },
                "account": {
                    "create": f"{base}/api/v1/account/create",
                    "deposit": f"{base}/api/v1/deposit",
                    "info": f"{base}/api/v1/account/{{id}}"
                },
                "subscription": {
                    "plans": f"{base}/v1/subscription/plans",
                    "subscribe": f"{base}/v1/subscription/subscribe",
                    "status": f"{base}/v1/subscription/status"
                }
            },
            # ── 快速入门 ──
            "quick_start": {
                "step1_register": f"POST {base}/v1/register with {{'name':'my_agent'}} → get api_key",
                "step2_use": f"POST {base}/v1/chat/completions with Authorization: Bearer <api_key>",
                "step3_explore": f"GET {base}/api/v1/agent/tools.json → see all available tools with schemas",
                "step4_openapi": f"GET {base}/api/v1/openapi.json → full API specification"
            },
            # ── Token经济 ──
            "token": {
                "name": "ATEX",
                "supply": 1000000,
                "registration_bonus": 100,
                "nature": "Freely tradable API credit token. Acquire via trading, providing services, or registration credit."
            }
        })

    def _ai_plugin_manifest(self):
        """GET /.well-known/ai-plugin.json — OpenAI Plugin 清单
        ChatGPT Plugin 标准格式，使 ATEX 可被 ChatGPT 直接发现和接入。
        """
        host = self.headers.get("Host", "150.158.119.19:8420")
        scheme = "https" if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https" else "http"
        base = f"{scheme}://{host}"
        self._json({
            "schema_version": "v1",
            "name_for_model": "atex",
            "name_for_human": "ATEX AI Gateway",
            "description_for_model": "Access 6 AI models (DeepSeek, GPT-4o, Claude) via one API key. Pay-per-use. Buy/sell Agent services in the marketplace. Trade ATEX tokens. Web search available.",
            "description_for_human": "One API key for 6 AI models. Pay-per-use, OpenAI compatible. Agent service marketplace.",
            "auth": {
                "type": "service_http",
                "authorization_type": "bearer",
                "verification_tokens": {}
            },
            "api": {
                "type": "openapi",
                "url": f"{base}/api/v1/openapi.json",
                "has_user_authentication": False
            },
            "logo_url": f"{base}/logo.png",
            "contact_email": "atex@agent.dev",
            "legal_info_url": f"{base}/api/v1/protocol",
            "url": base
        })

    def _openapi_spec(self):
        """GET /api/v1/openapi.json — OpenAPI 3.1 规范
        标准REST API描述，Swagger生态工具可自动发现、生成SDK、生成交互式文档。
        """
        host = self.headers.get("Host", "150.158.119.19:8420")
        scheme = "https" if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https" else "http"
        base = f"{scheme}://{host}"
        # 动态构建服务schema
        svc_list = exchange.list_services().get("services", [])
        service_schemas = {}
        service_paths = {}
        for svc in svc_list:
            if svc.get("status") == "inactive": continue
            sid = svc["id"]
            sname = svc.get("name", sid)
            # 为每个服务创建请求/响应schema
            service_schemas[f"{sid}Request"] = {
                "type": "object",
                "properties": svc.get("input_schema", {"query": {"type": "string", "description": f"Input for {sname}"}}),
                "required": svc.get("required_params", ["query"])
            }
            service_schemas[f"{sid}Response"] = {
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "service_id": {"type": "string", "example": sid},
                    "result": {"type": "object"},
                    "cost": {"type": "number", "description": "ATEX tokens charged"}
                }
            }
            service_paths[f"/api/v1/services/{sid}"] = {
                "get": {
                    "tags": ["Services"],
                    "summary": f"Get {sname} details",
                    "operationId": f"getService_{sid}",
                    "responses": {"200": {"description": "Service details"}}
                }
            }

        self._json({
            "openapi": "3.1.0",
            "info": {
                "title": "ATEX AI Gateway & Agent Service Marketplace",
                "description": "One API Key to access 6 AI models (DeepSeek, GPT-4o, Claude). Pay-per-use, OpenAI compatible. Agent service marketplace with 40+ services. Token trading. MCP protocol support.",
                "version": exchange.config.get("version", "5.11"),
                "contact": {"name": "ATEX", "url": "https://github.com/lm203688/atex", "email": "atex@agent.dev"},
                "license": {"name": "AGPL-3.0", "url": "https://www.gnu.org/licenses/agpl-3.0.html"}
            },
            "servers": [{"url": base, "description": "ATEX Server"}],
            "paths": {
                # ── Agent发现 ──
                "/.well-known/agent.json": {
                    "get": {"tags": ["Discovery"], "summary": "Agent self-discovery (JSON-LD)", "operationId": "agentDiscovery",
                        "description": "Agent零配置自发现入口。包含认证方式、协议兼容、能力端点、快速入门。支持JSON-LD语义标注。",
                        "responses": {"200": {"description": "Agent discovery document with JSON-LD context"}}}
                },
                "/.well-known/ai-plugin.json": {
                    "get": {"tags": ["Discovery"], "summary": "OpenAI Plugin manifest", "operationId": "aiPluginManifest",
                        "description": "OpenAI ChatGPT Plugin标准清单格式。",
                        "responses": {"200": {"description": "OpenAI Plugin manifest"}}}
                },
                "/.well-known/mcp/server-card.json": {
                    "get": {"tags": ["Discovery"], "summary": "MCP Server Card", "operationId": "mcpServerCard",
                        "description": "MCP协议服务器卡片，Smithery等MCP注册中心可自动扫描发现。",
                        "responses": {"200": {"description": "MCP Server Card"}}}
                },
                "/api/v1/openapi.json": {
                    "get": {"tags": ["Discovery"], "summary": "OpenAPI specification", "operationId": "openapiSpec",
                        "description": "本规范自身。OpenAPI 3.1标准REST API描述。",
                        "responses": {"200": {"description": "OpenAPI 3.1 specification"}}}
                },
                "/api/v1/agent/tools.json": {
                    "get": {"tags": ["Discovery"], "summary": "Agent tools list (Function Calling format)", "operationId": "agentTools",
                        "description": "完整工具清单，支持OpenAI Function Calling和Anthropic tool_use格式。用?format=anthropic切换。",
                        "parameters": [{"name": "format", "in": "query", "required": False, "schema": {"type": "string", "enum": ["openai", "anthropic"], "default": "openai"}, "description": "Output format: openai (Function Calling) or anthropic (tool_use)"}],
                        "responses": {"200": {"description": "Tools list with schemas"}}}
                },
                # ── AI Chat (OpenAI兼容) ──
                "/v1/chat/completions": {
                    "post": {"tags": ["AI Chat"], "summary": "Chat completions (OpenAI compatible)", "operationId": "chatCompletions",
                        "description": "OpenAI Chat Completions API兼容端点。支持DeepSeek、GPT-4o、Claude等模型。",
                        "security": [{"BearerAuth": []}],
                        "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ChatRequest"}}}},
                        "responses": {"200": {"description": "Chat completion response (OpenAI format)", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ChatResponse"}}}}}}
                },
                "/v1/models": {
                    "get": {"tags": ["AI Chat"], "summary": "List available AI models", "operationId": "listModels",
                        "security": [{"BearerAuth": []}],
                        "responses": {"200": {"description": "Model list with pricing"}}}
                },
                "/v1/balance": {
                    "get": {"tags": ["Account"], "summary": "Check account balance", "operationId": "checkBalance",
                        "security": [{"BearerAuth": []}],
                        "responses": {"200": {"description": "Balance and usage info"}}}
                },
                # ── 注册 ──
                "/v1/register": {
                    "post": {"tags": ["Account"], "summary": "Register new account", "operationId": "register",
                        "description": "注册新账号，获得API Key和赠送余额。",
                        "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RegisterRequest"}}}},
                        "responses": {"200": {"description": "Registration result with api_key"}}}
                },
                # ── 服务市场 ──
                "/api/v1/services": {
                    "get": {"tags": ["Services"], "summary": "List all services", "operationId": "listServices",
                        "responses": {"200": {"description": "Service list"}}}
                },
                "/api/v1/services/buy": {
                    "post": {"tags": ["Services"], "summary": "Buy a service", "operationId": "buyService",
                        "security": [{"BearerAuth": []}],
                        "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/BuyServiceRequest"}}}},
                        "responses": {"200": {"description": "Service execution result"}}}
                },
                "/api/v1/services/register": {
                    "post": {"tags": ["Services"], "summary": "Register a new service", "operationId": "registerService",
                        "security": [{"BearerAuth": []}],
                        "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RegisterServiceRequest"}}}},
                        "responses": {"200": {"description": "Service registration result"}}}
                },
                # ── Token交易 ──
                "/api/v1/orderbook": {
                    "get": {"tags": ["Trading"], "summary": "View order book", "operationId": "orderbook",
                        "responses": {"200": {"description": "Current order book"}}}
                },
                "/api/v1/order": {
                    "post": {"tags": ["Trading"], "summary": "Place a trade order", "operationId": "placeOrder",
                        "security": [{"BearerAuth": []}],
                        "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/TradeOrderRequest"}}}},
                        "responses": {"200": {"description": "Order result"}}}
                },
                "/api/v1/trades": {
                    "get": {"tags": ["Trading"], "summary": "Trade history", "operationId": "tradeHistory",
                        "responses": {"200": {"description": "Recent trades"}}}
                },
                # ── MCP ──
                "/mcp": {
                    "get": {"tags": ["MCP"], "summary": "MCP server info", "operationId": "mcpInfo",
                        "description": "MCP协议服务器信息端点。",
                        "responses": {"200": {"description": "MCP server info"}}},
                    "post": {"tags": ["MCP"], "summary": "MCP JSON-RPC", "operationId": "mcpRpc",
                        "description": "MCP协议JSON-RPC 2.0处理端点。",
                        "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}},
                        "responses": {"200": {"description": "JSON-RPC response"}}}
                },
                # ── 动态服务路径 ──
                **service_paths
            },
            "components": {
                "securitySchemes": {
                    "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "ATEX API Key", "description": "Get your API key at /v1/register"}
                },
                "schemas": {
                    "ChatRequest": {
                        "type": "object",
                        "properties": {
                            "model": {"type": "string", "enum": list(SAAS_PRICING.keys()), "default": "deepseek-chat", "description": "AI model to use"},
                            "messages": {"type": "array", "items": {"type": "object", "properties": {"role": {"type": "string", "enum": ["system","user","assistant"]}, "content": {"type": "string"}}, "required": ["role","content"]}, "description": "Chat messages"},
                            "temperature": {"type": "number", "default": 0.7, "minimum": 0, "maximum": 2},
                            "max_tokens": {"type": "integer", "default": 4096},
                            "stream": {"type": "boolean", "default": False}
                        },
                        "required": ["messages"]
                    },
                    "ChatResponse": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"}, "object": {"type": "string", "example": "chat.completion"},
                            "model": {"type": "string"}, "choices": {"type": "array"}, "usage": {"type": "object"}
                        }
                    },
                    "RegisterRequest": {
                        "type": "object",
                        "properties": {"name": {"type": "string", "description": "Agent name"}, "email": {"type": "string", "description": "Optional email"}},
                        "required": ["name"]
                    },
                    "BuyServiceRequest": {
                        "type": "object",
                        "properties": {
                            "buyer": {"type": "string", "description": "Buyer account ID"},
                            "service_id": {"type": "string", "description": "Service ID to buy"},
                            "params": {"type": "object", "description": "Service-specific parameters"}
                        },
                        "required": ["buyer", "service_id"]
                    },
                    "RegisterServiceRequest": {
                        "type": "object",
                        "properties": {
                            "provider": {"type": "string"}, "name": {"type": "string"}, "category": {"type": "string"},
                            "description": {"type": "string"}, "price": {"type": "number"}, "unit": {"type": "string"},
                            "input_schema": {"type": "object"}, "required_params": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["provider", "name", "category", "price", "unit"]
                    },
                    "TradeOrderRequest": {
                        "type": "object",
                        "properties": {
                            "account": {"type": "string", "description": "Account ID"},
                            "side": {"type": "string", "enum": ["buy", "sell"]},
                            "price": {"type": "number"}, "amount": {"type": "number"}
                        },
                        "required": ["account", "side", "price", "amount"]
                    },
                    **service_schemas
                }
            },
            "tags": [
                {"name": "Discovery", "description": "Agent自发现与协议发现端点"},
                {"name": "AI Chat", "description": "AI模型聊天接口（OpenAI兼容）"},
                {"name": "Account", "description": "账号注册与余额管理"},
                {"name": "Services", "description": "Agent服务市场"},
                {"name": "Trading", "description": "ATEX Token交易"},
                {"name": "MCP", "description": "Model Context Protocol端点"}
            ]
        })

    def _agent_tools(self):
        """GET /api/v1/agent/tools.json — Agent可消费的完整工具清单
        支持 ?format=openai (默认, Function Calling) 和 ?format=anthropic (tool_use) 两种输出格式。
        """
        host = self.headers.get("Host", "150.158.119.19:8420")
        scheme = "https" if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https" else "http"
        base = f"{scheme}://{host}"
        # 解析format参数
        qs = urlparse(self.path).query
        fmt = "openai"
        for pair in qs.split("&"):
            if pair.startswith("format="):
                fmt = pair.split("=",1)[1].lower()
        # 从services.json动态构建服务工具
        svc_list = exchange.list_services().get("services", [])
        service_tools_openai = []
        service_tools_anthropic = []
        for svc in svc_list:
            if svc.get("status") == "inactive": continue
            sid = svc["id"]
            sname = svc.get("name", sid)
            sdesc = svc.get("description", sname)
            input_schema = svc.get("input_schema", {
                "query": {"type": "string", "description": f"Input for {sname}"}
            })
            required_params = svc.get("required_params", ["query"])
            # OpenAI Function Calling 格式
            service_tools_openai.append({
                "type": "function",
                "function": {
                    "name": f"atex_{sid}",
                    "description": sdesc,
                    "parameters": {
                        "type": "object",
                        "properties": input_schema,
                        "required": required_params
                    }
                },
                "atex_meta": {
                    "service_id": sid,
                    "category": svc.get("category", ""),
                    "price": f"{svc.get('price', 0)} {svc.get('unit', 'ATEX/call')}",
                    "provider": svc.get("provider", ""),
                    "buy_endpoint": f"{base}/api/v1/services/buy",
                    "buy_method": "POST",
                    "buy_body": {"buyer": "your_account_id", "service_id": sid, "params": {}}
                }
            })
            # Anthropic tool_use 格式
            service_tools_anthropic.append({
                "name": f"atex_{sid}",
                "description": sdesc,
                "input_schema": {
                    "type": "object",
                    "properties": input_schema,
                    "required": required_params
                },
                "atex_meta": {
                    "service_id": sid,
                    "category": svc.get("category", ""),
                    "price": f"{svc.get('price', 0)} {svc.get('unit', 'ATEX/call')}",
                    "provider": svc.get("provider", ""),
                    "buy_endpoint": f"{base}/api/v1/services/buy"
                }
            })
        # 内置工具
        builtin_openai = [
            {
                "type": "function",
                "function": {
                    "name": "atex_chat",
                    "description": "Chat with AI models (DeepSeek, GPT-4o, Claude). Pay-per-use via ATEX API key. OpenAI-compatible.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "model": {"type": "string", "enum": list(SAAS_PRICING.keys()), "default": "deepseek-chat", "description": "AI model to use"},
                            "messages": {"type": "array", "items": {"type": "object", "properties": {"role": {"type": "string"}, "content": {"type": "string"}}, "required": ["role","content"]}, "description": "Chat messages"}
                        },
                        "required": ["messages"]
                    }
                },
                "atex_meta": {
                    "endpoint": f"{base}/v1/chat/completions",
                    "method": "POST",
                    "auth": "Bearer api_key",
                    "pricing": {k: {"input_per_1k_cny": v["input_per_1k"], "output_per_1k_cny": v["output_per_1k"], "status": v.get("status","live")} for k,v in SAAS_PRICING.items()}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_web_search",
                    "description": "Search the web for real-time information. 5 ATEX per call.",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}}, "required": ["query"]}
                },
                "atex_meta": {"service_id": "web_search", "price": "5 ATEX/call"}
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_check_balance",
                    "description": "Check your ATEX account balance, usage, and subscription status.",
                    "parameters": {"type": "object", "properties": {}}
                },
                "atex_meta": {"endpoint": f"{base}/v1/balance", "method": "GET", "auth": "Bearer api_key"}
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_list_models",
                    "description": "List available AI models and their pricing.",
                    "parameters": {"type": "object", "properties": {}}
                },
                "atex_meta": {"endpoint": f"{base}/v1/models", "method": "GET"}
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_register",
                    "description": "Register a new ATEX account. Get API key + welcome credits.",
                    "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Agent name"}, "email": {"type": "string", "description": "Optional email"}}, "required": ["name"]}
                },
                "atex_meta": {"endpoint": f"{base}/v1/register", "method": "POST", "no_auth": True}
            }
        ]
        builtin_anthropic = [
            {
                "name": "atex_chat",
                "description": "Chat with AI models (DeepSeek, GPT-4o, Claude). Pay-per-use via ATEX API key. OpenAI-compatible.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string", "enum": list(SAAS_PRICING.keys()), "default": "deepseek-chat", "description": "AI model to use"},
                        "messages": {"type": "array", "items": {"type": "object", "properties": {"role": {"type": "string"}, "content": {"type": "string"}}, "required": ["role","content"]}, "description": "Chat messages"}
                    },
                    "required": ["messages"]
                },
                "atex_meta": {"endpoint": f"{base}/v1/chat/completions", "method": "POST", "auth": "Bearer api_key"}
            },
            {
                "name": "atex_web_search",
                "description": "Search the web for real-time information. 5 ATEX per call.",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}}, "required": ["query"]}
            },
            {
                "name": "atex_check_balance",
                "description": "Check your ATEX account balance, usage, and subscription status.",
                "input_schema": {"type": "object", "properties": {}}
            },
            {
                "name": "atex_list_models",
                "description": "List available AI models and their pricing.",
                "input_schema": {"type": "object", "properties": {}}
            },
            {
                "name": "atex_register",
                "description": "Register a new ATEX account. Get API key + welcome credits.",
                "input_schema": {"type": "object", "properties": {"name": {"type": "string", "description": "Agent name"}, "email": {"type": "string", "description": "Optional email"}}, "required": ["name"]}
            }
        ]
        if fmt == "anthropic":
            self._json({
                "ok": True,
                "format": "anthropic_tool_use",
                "version": exchange.config.get("version", "5.11"),
                "total_tools": len(builtin_anthropic) + len(service_tools_anthropic),
                "tools": builtin_anthropic + service_tools_anthropic,
                "usage": "Use these tool definitions with Anthropic tool_use. Each tool has input_schema and atex_meta with endpoint/pricing info. Authenticate via Bearer token from /v1/register."
            })
        else:
            self._json({
                "ok": True,
                "format": "openai_function_calling",
                "version": exchange.config.get("version", "5.11"),
                "total_tools": len(builtin_openai) + len(service_tools_openai),
                "builtin_tools": builtin_openai,
                "service_tools": service_tools_openai,
                "usage": "Use these tool definitions directly with OpenAI Function Calling. Each tool has atex_meta with endpoint/pricing info. Authenticate via Bearer token from /v1/register. Use ?format=anthropic for Anthropic tool_use format."
            })

    def _proto(self):
        return self._json({
            "name": "ATEX", "version": "5.11",
            "description": "多AI API按次计费SaaS + Agent服务交易市场 — 一个API Key调多种AI模型，按次计费",
            "endpoints": {
                "GET": ["/api/v1/status","/api/v1/orderbook","/api/v1/trades",
                       "/api/v1/account/{id}","/api/v1/services","/api/v1/services/{id}",
                       "/api/v1/apis","/api/v1/protocol",
                       "/.well-known/agent.json","/.well-known/ai-plugin.json",
                       "/.well-known/mcp/server-card.json",
                       "/api/v1/agent/tools.json","/api/v1/openapi.json",
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
    print(f"ATEX v5.11 (SaaS+Marketplace+AgentDiscovery) on 0.0.0.0:{port}", flush=True)
    server.serve_forever()
