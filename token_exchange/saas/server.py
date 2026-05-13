#!/usr/bin/env python3
"""
ATEX SaaS — 多AI API按次收费服务
一个API Key调多种AI模型，按次计费，用法币结算。

支持模型：
- DeepSeek Chat (deepseek-chat)
- DeepSeek Reasoner (deepseek-reasoner)
- 更多模型持续接入中

计费方式：
- 预充值余额，按调用扣费
- 价格透明，比官方API略高（含平台服务费）
- 余额不足自动拒绝调用

API Key格式：atex_sk_xxxxxxxxxxxx
"""

import json, os, sys, time, hashlib, secrets, threading, uuid
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TZ = timezone(timedelta(hours=8))

# ── 配置 ──

PRICING = {
    "deepseek-chat": {
        "name": "DeepSeek Chat",
        "input_per_1k": 0.001,   # ¥/1K tokens
        "output_per_1k": 0.002,  # ¥/1K tokens
        "min_charge": 0.001,     # 最低收费
        "backend": "deepseek",
        "model": "deepseek-chat",
    },
    "deepseek-reasoner": {
        "name": "DeepSeek Reasoner",
        "input_per_1k": 0.004,
        "output_per_1k": 0.016,
        "min_charge": 0.005,
        "backend": "deepseek",
        "model": "deepseek-reasoner",
    },
    "gpt-4o-mini": {
        "name": "GPT-4o Mini",
        "input_per_1k": 0.01,
        "output_per_1k": 0.03,
        "min_charge": 0.01,
        "backend": "openai",
        "model": "gpt-4o-mini",
        "status": "coming_soon",
    },
    "gpt-4o": {
        "name": "GPT-4o",
        "input_per_1k": 0.05,
        "output_per_1k": 0.15,
        "min_charge": 0.05,
        "backend": "openai",
        "model": "gpt-4o",
        "status": "coming_soon",
    },
    "claude-3-5-sonnet": {
        "name": "Claude 3.5 Sonnet",
        "input_per_1k": 0.021,
        "output_per_1k": 0.105,
        "min_charge": 0.02,
        "backend": "anthropic",
        "model": "claude-3-5-sonnet-latest",
        "status": "coming_soon",
    },
    "claude-3-5-haiku": {
        "name": "Claude 3.5 Haiku",
        "input_per_1k": 0.008,
        "output_per_1k": 0.004,
        "min_charge": 0.005,
        "backend": "anthropic",
        "model": "claude-3-5-haiku-latest",
        "status": "coming_soon",
    },
}

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-db4c943047934a6bbd1640a3efd98e6b")
DEEPSEEK_BASE = "https://api.deepseek.com/v1"

# ── 支付配置 ──
PAYMENT = {
    "alipay": "lx688@sina.com",
    "paypal": "https://paypal.me/xinglixingli",
    "min_topup_cny": 10.0,       # 最低充值10元
    "topup_note": "支付宝转账请备注: ATEX_{user_id}，转账后联系管理员确认到账",
}

# ── 数据存储 ──

DATA_DIR = f"{BASE}/saas_data"
os.makedirs(DATA_DIR, exist_ok=True)

def _load_json(path, default=None):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default if default is not None else {}

def _save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# 用户数据：{user_id: {api_key, name, balance, created, ...}}
USERS_FILE = f"{DATA_DIR}/users.json"
# 调用记录：[{id, user_id, model, input_tokens, output_tokens, cost, time, ...}]
USAGE_FILE = f"{DATA_DIR}/usage.json"
# 充值记录
TOPUP_FILE = f"{DATA_DIR}/topups.json"

users_lock = threading.Lock()
usage_lock = threading.Lock()

def get_users():
    return _load_json(USERS_FILE, {})

def save_users(data):
    _save_json(USERS_FILE, data)

def get_usage():
    return _load_json(USAGE_FILE, [])

def save_usage(data):
    _save_json(USAGE_FILE, data)

# ── API Key管理 ──

def generate_api_key():
    """生成 atex_sk_xxx 格式的API Key"""
    raw = secrets.token_hex(24)
    return f"atex_sk_{raw}"

def hash_api_key(key):
    """API Key的哈希，用于存储（不存明文）"""
    return hashlib.sha256(key.encode()).hexdigest()

# ── 用户管理 ──

def create_user(name, email=""):
    with users_lock:
        users = get_users()
        user_id = f"u_{secrets.token_hex(6)}"
        api_key = generate_api_key()
        key_hash = hash_api_key(api_key)
        users[user_id] = {
            "id": user_id,
            "name": name,
            "email": email,
            "api_key_hash": key_hash,
            "balance": 0.0,
            "total_spent": 0.0,
            "total_calls": 0,
            "created": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_users(users)
        return {"ok": True, "user_id": user_id, "api_key": api_key, "balance": 0.0}

def authenticate(api_key):
    """验证API Key，返回用户信息"""
    if not api_key or not api_key.startswith("atex_sk_"):
        return None
    key_hash = hash_api_key(api_key)
    users = get_users()
    for uid, user in users.items():
        if user.get("api_key_hash") == key_hash:
            return user
    return None

def topup(user_id, amount_cny):
    """充值（人工确认后调用）"""
    with users_lock:
        users = get_users()
        if user_id not in users:
            return {"ok": False, "err": "user_not_found"}
        if amount_cny <= 0:
            return {"ok": False, "err": "amount_must_be_positive"}
        users[user_id]["balance"] += amount_cny
        save_users(users)
    # 记录充值
    topups = _load_json(TOPUP_FILE, [])
    topups.append({
        "id": str(uuid.uuid4())[:8],
        "user_id": user_id,
        "amount": amount_cny,
        "time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
    })
    _save_json(TOPUP_FILE, topups)
    return {"ok": True, "user_id": user_id, "balance": users[user_id]["balance"], "topup": amount_cny}

# ── API调用 ──

def call_deepseek(model, messages, max_tokens=2000, temperature=0.7):
    """调用DeepSeek API"""
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode()
    req = urllib.request.Request(
        f"{DEEPSEEK_BASE}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return {
            "ok": True,
            "content": content,
            "model": data.get("model", model),
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "finish_reason": data["choices"][0].get("finish_reason", "stop"),
        }
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()[:500]
        return {"ok": False, "err": f"API Error {e.code}: {err_body}"}
    except Exception as e:
        return {"ok": False, "err": str(e)}

def calculate_cost(model_id, input_tokens, output_tokens):
    """计算调用费用（人民币）"""
    pricing = PRICING.get(model_id)
    if not pricing:
        return 0
    input_cost = (input_tokens / 1000) * pricing["input_per_1k"]
    output_cost = (output_tokens / 1000) * pricing["output_per_1k"]
    total = input_cost + output_cost
    return max(total, pricing["min_charge"])

def chat_completion(user, model_id, messages, **kwargs):
    """统一的chat completion入口"""
    pricing = PRICING.get(model_id)
    if not pricing:
        available = [k for k, v in PRICING.items() if v.get("status") != "coming_soon"]
        coming = [k for k, v in PRICING.items() if v.get("status") == "coming_soon"]
        return {"ok": False, "err": f"unknown_model:{model_id}", "available": available, "coming_soon": coming}
    
    if pricing.get("status") == "coming_soon":
        return {"ok": False, "err": f"model_coming_soon:{model_id}", "note": pricing.get("note", "This model requires additional API integration.")}
    
    # 估算预扣费（按max_tokens估算）
    max_tokens = kwargs.get("max_tokens", 2000)
    estimated_cost = calculate_cost(model_id, 
        sum(len(m.get("content","").split()) * 1.5 for m in messages),  # 粗估input tokens
        max_tokens)
    
    # 检查余额
    if user["balance"] < pricing["min_charge"]:
        return {"ok": False, "err": "insufficient_balance", "balance": user["balance"], "min_required": pricing["min_charge"]}
    
    # 调用后端API
    backend = pricing["backend"]
    if backend == "deepseek":
        result = call_deepseek(pricing["model"], messages, max_tokens, kwargs.get("temperature", 0.7))
    else:
        return {"ok": False, "err": f"backend_not_implemented:{backend}"}
    
    if not result.get("ok"):
        return result
    
    # 计算实际费用
    actual_cost = calculate_cost(model_id, result.get("input_tokens", 0), result.get("output_tokens", 0))
    
    # 扣费
    with users_lock:
        users = get_users()
        uid = user["id"]
        if users[uid]["balance"] < actual_cost:
            # 余额不足但已调用，允许透支本次，标记欠费
            users[uid]["balance"] -= actual_cost
            users[uid]["total_spent"] += actual_cost
            users[uid]["total_calls"] += 1
            save_users(users)
        else:
            users[uid]["balance"] -= actual_cost
            users[uid]["total_spent"] += actual_cost
            users[uid]["total_calls"] += 1
            save_users(users)
    
    # 记录调用
    with usage_lock:
        usage = get_usage()
        usage.append({
            "id": str(uuid.uuid4())[:8],
            "user_id": uid,
            "model": model_id,
            "input_tokens": result.get("input_tokens", 0),
            "output_tokens": result.get("output_tokens", 0),
            "cost_cny": round(actual_cost, 6),
            "time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        })
        # 只保留最近10000条
        if len(usage) > 10000:
            usage = usage[-10000:]
        save_usage(usage)
    
    # 返回OpenAI兼容格式
    return {
        "ok": True,
        "id": f"atex-{str(uuid.uuid4())[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": result.get("model", model_id),
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result["content"]},
            "finish_reason": result.get("finish_reason", "stop"),
        }],
        "usage": {
            "prompt_tokens": result.get("input_tokens", 0),
            "completion_tokens": result.get("output_tokens", 0),
            "total_tokens": result.get("input_tokens", 0) + result.get("output_tokens", 0),
        },
        "cost_cny": round(actual_cost, 6),
        "remaining_balance": users[uid]["balance"],
    }


# ── HTTP Server ──

import urllib.request, urllib.error

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    
    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
    
    def _get_api_key(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return ""
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()
    
    def do_GET(self):
        p = urlparse(self.path).path
        
        if p == "/v1/models":
            # 列出可用模型
            models = []
            for mid, info in PRICING.items():
                models.append({
                    "id": mid,
                    "name": info["name"],
                    "status": info.get("status", "live"),
                    "pricing": {
                        "input_per_1k_cny": info["input_per_1k"],
                        "output_per_1k_cny": info["output_per_1k"],
                    },
                })
            self._json({"object": "list", "data": models})
        
        elif p == "/v1/balance":
            # 查余额
            api_key = self._get_api_key()
            user = authenticate(api_key)
            if not user:
                return self._json({"err": "invalid_api_key"}, 401)
            self._json({
                "user_id": user["id"],
                "name": user["name"],
                "balance_cny": round(user["balance"], 4),
                "total_spent_cny": round(user["total_spent"], 4),
                "total_calls": user["total_calls"],
            })
        
        elif p == "/v1/usage":
            # 查调用记录
            api_key = self._get_api_key()
            user = authenticate(api_key)
            if not user:
                return self._json({"err": "invalid_api_key"}, 401)
            usage = get_usage()
            user_usage = [u for u in usage if u["user_id"] == user["id"]][-50:]
            self._json({"count": len(user_usage), "usage": user_usage})
        
        elif p == "/v1/payment/info":
            # 充值指引
            api_key = self._get_api_key()
            user = authenticate(api_key)
            if not user:
                return self._json({"err": "invalid_api_key"}, 401)
            self._json({
                "user_id": user["id"],
                "alipay": PAYMENT["alipay"],
                "paypal": PAYMENT["paypal"],
                "min_topup_cny": PAYMENT["min_topup_cny"],
                "note": PAYMENT["topup_note"].format(user_id=user["id"]),
                "steps": [
                    f"1. 支付宝转账至 {PAYMENT['alipay']}，金额≥{PAYMENT['min_topup_cny']}元",
                    f"2. 转账备注: ATEX_{user['id']}",
                    "3. 联系管理员确认到账",
                    "4. 余额自动更新",
                ],
            })

        elif p == "/health":
            self._json({"status": "ok", "service": "ATEX SaaS", "version": "1.0"})
        
        else:
            self._json({"err": "not_found"}, 404)
    
    def do_POST(self):
        p = urlparse(self.path).path
        
        if p == "/v1/chat/completions":
            # OpenAI兼容接口
            api_key = self._get_api_key()
            user = authenticate(api_key)
            if not user:
                return self._json({"err": "invalid_api_key"}, 401)
            
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode())
            except:
                return self._json({"err": "invalid_json"}, 400)
            
            model_id = body.get("model", "deepseek-chat")
            messages = body.get("messages", [])
            if not messages:
                return self._json({"err": "messages_required"}, 400)
            
            kwargs = {
                "max_tokens": body.get("max_tokens", 2000),
                "temperature": body.get("temperature", 0.7),
            }
            
            result = chat_completion(user, model_id, messages, **kwargs)
            self._json(result, 200 if result.get("ok") else 400)
        
        elif p == "/admin/create_user":
            # 管理接口：创建用户
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode())
            except:
                return self._json({"err": "invalid_json"}, 400)
            name = body.get("name", "")
            email = body.get("email", "")
            if not name:
                return self._json({"err": "name_required"}, 400)
            result = create_user(name, email)
            self._json(result)
        
        elif p == "/admin/topup":
            # 管理接口：充值
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode())
            except:
                return self._json({"err": "invalid_json"}, 400)
            user_id = body.get("user_id", "")
            amount = body.get("amount_cny", 0)
            if not user_id or amount <= 0:
                return self._json({"err": "user_id and positive amount required"}, 400)
            result = topup(user_id, amount)
            self._json(result)
        
        else:
            self._json({"err": "not_found"}, 404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8430
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"ATEX SaaS v1.0 on 0.0.0.0:{port}", flush=True)
    server.serve_forever()
