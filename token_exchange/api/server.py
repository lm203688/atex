#!/usr/bin/env python3
"""ATEX HTTP API v6.0 — 合规工具 + AI能力 + 交易变现平台"""
import json, os, sys, time, threading, hashlib, secrets
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from collections import defaultdict
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from atex import ATEX, validate_account_id, safe_json_loads, MAX_INPUT_SIZE
from service_executor import execute_service, execute_api_proxy, _chat
from payment.gateway import (
    nowpayments_create_order, nowpayments_ipn_callback,
    xunhupay_create_order, xunhupay_callback,
    request_withdrawal, approve_withdrawal,
    get_exchange_rates, update_exchange_rate,
    _load_records
)
from job_market import (create_job, list_jobs, get_job, update_job, cancel_job,
                        submit_bid, accept_bid, withdraw_bid,
                        start_job, submit_result, rate_job, dispute_job, agent_stats)
from skill_market import (publish_skill, list_skills, get_skill, buy_skill,
                          rate_skill as rate_skill_file, update_skill, remove_skill,
                          import_ecc_skills, get_skill_ecc_format, is_ecc_format,
                          parse_ecc_skill, skill_to_ecc_format, PROMPT_DEFENSE_BASELINE)
from content_safety import (check_prompt_injection, scan_content, submit_report,
                            list_reports, resolve_report, is_content_blocked, safety_stats)
from realtime import (send_notification, get_notifications, mark_read,
                      subscribe, unsubscribe, sse_events, is_websocket_upgrade, generate_accept_key)
from a2a_protocol import (register_agent, discover_agents, get_agent_card, deregister_agent,
                          create_task, list_tasks, get_task, send_message as a2a_send_message,
                          accept_task, reject_task, complete_task, fail_task,
                          a2a_to_job, job_to_a2a, a2a_protocol_info, a2a_stats)

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
    # 先检查预算限制
    allowed, reason = _check_budget(uid, cost_cny)
    if not allowed:
        return False
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
    _record_budget_spend(uid, cost_cny)
    _save_saas(data)
    return True

def _get_plan(plan_id):
    plans = exchange.config.get("subscription_plans", {}).get("plans", [])
    for p in plans:
        if p.get("id") == plan_id:
            return p
    return None

# ── Agent预算管理 ──
BUDGET_DATA = os.path.join(SAAS_DATA, "budgets.json")

def _load_budgets():
    if os.path.exists(BUDGET_DATA):
        with open(BUDGET_DATA) as f: return json.load(f)
    return {}

def _save_budgets(data):
    with open(BUDGET_DATA, "w") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def _check_budget(uid, cost_cny):
    """检查用户预算是否允许此次消费。返回 (allowed, reason)"""
    budgets = _load_budgets()
    user_budget = budgets.get(uid)
    if not user_budget:
        return True, None  # 无预算限制，允许
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")
    # 检查每日预算
    daily_limit = user_budget.get("daily_cny")
    if daily_limit is not None:
        daily_spent = user_budget.get("daily_spent", {}).get(today, 0)
        if daily_spent + cost_cny > daily_limit:
            return False, f"Daily budget exceeded: {daily_spent:.4f}/{daily_limit} CNY (this call: {cost_cny:.4f})"
    # 检查每月预算
    monthly_limit = user_budget.get("monthly_cny")
    if monthly_limit is not None:
        monthly_spent = user_budget.get("monthly_spent", {}).get(month, 0)
        if monthly_spent + cost_cny > monthly_limit:
            return False, f"Monthly budget exceeded: {monthly_spent:.4f}/{monthly_limit} CNY (this call: {cost_cny:.4f})"
    # 检查单次上限
    per_action_limit = user_budget.get("per_action_cny")
    if per_action_limit is not None and cost_cny > per_action_limit:
        return False, f"Per-action limit exceeded: {cost_cny:.4f} > {per_action_limit} CNY"
    return True, None

def _record_budget_spend(uid, cost_cny):
    """记录消费到预算追踪"""
    budgets = _load_budgets()
    if uid not in budgets:
        return
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")
    if "daily_spent" not in budgets[uid]: budgets[uid]["daily_spent"] = {}
    budgets[uid]["daily_spent"][today] = round(budgets[uid]["daily_spent"].get(today, 0) + cost_cny, 6)
    if "monthly_spent" not in budgets[uid]: budgets[uid]["monthly_spent"] = {}
    budgets[uid]["monthly_spent"][month] = round(budgets[uid]["monthly_spent"].get(month, 0) + cost_cny, 6)
    # 清理30天前的daily记录
    cutoff = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    budgets[uid]["daily_spent"] = {k: v for k, v in budgets[uid]["daily_spent"].items() if k >= cutoff}
    # 清理6个月前的monthly记录
    month_cutoff = (now - timedelta(days=180)).strftime("%Y-%m")
    budgets[uid]["monthly_spent"] = {k: v for k, v in budgets[uid]["monthly_spent"].items() if k >= month_cutoff}
    _save_budgets(budgets)

# ── SaaS定价 ──
SAAS_PRICING = {
    "glm-4-plus": {"name":"GLM-4 Plus","input_per_1k":0.001,"output_per_1k":0.002,"backend":"zai","model":"glm-4-plus"},
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

    def _landing_page(self):
        """Serve the ATEX landing page."""
        svcs = exchange.list_services().get("services", [])
        compliance_svcs = [s for s in svcs if s.get("category") == "合规工具"]
        ai_svcs = [s for s in svcs if s.get("category") == "AI能力"]
        compliance_cards = ""
        for s in compliance_svcs:
            compliance_cards += f'''<div class="card"><h3>🛡️ {s["name"]}</h3><p>{s.get("description","")}</p><div class="price">¥{s.get("price",0)}/{s.get("price_unit","次")}</div><a href="https://lm203688.github.io/atex/" class="btn">立即使用</a></div>'''
        ai_cards = ""
        for s in ai_svcs:
            ai_cards += f'''<div class="card"><h3>🤖 {s["name"]}</h3><p>{s.get("description","")}</p><div class="price">¥{s.get("price",0)}/{s.get("price_unit","次")}</div><a href="https://lm203688.github.io/atex/" class="btn">立即使用</a></div>'''
        html = f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ATEX — 合规工具 + AI能力平台</title>
<meta name="description" content="中文违禁词检测、AI搜索可见度检测、出海合规评估、SEO合规检测。8大AI能力（TTS/ASR/VLM/图片/视频/搜索）。按次计费，支付宝充值。">
<meta name="keywords" content="违禁词检测,内容合规,AI搜索优化,出海合规,SEO合规,AI API,MCP Server,TTS,ASR,VLM,图片生成,视频生成">
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#0f172a;color:#e2e8f0}}
.hero{{text-align:center;padding:80px 20px 40px;background:linear-gradient(135deg,#1e1b4b,#312e81,#4c1d95)}}
.hero h1{{font-size:2.5em;margin-bottom:16px;background:linear-gradient(90deg,#818cf8,#c084fc);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.hero p{{font-size:1.2em;color:#a5b4fc;max-width:600px;margin:0 auto 30px}}
.hero .cta{{display:inline-block;padding:14px 36px;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;border-radius:12px;text-decoration:none;font-size:1.1em;font-weight:600}}
.section{{max-width:1000px;margin:40px auto;padding:0 20px}}
.section h2{{font-size:1.5em;color:#c7d2fe;margin-bottom:24px;padding-left:8px;border-left:4px solid #6366f1}}
.tools{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:24px}}
.card{{background:#1e293b;border-radius:16px;padding:28px;border:1px solid #334155;transition:transform .2s}}
.card:hover{{transform:translateY(-4px);border-color:#6366f1}}
.card h3{{font-size:1.2em;color:#c7d2fe;margin-bottom:10px}}
.card p{{color:#94a3b8;font-size:.9em;line-height:1.6;margin-bottom:16px;min-height:48px}}
.card .price{{color:#a78bfa;font-size:1.1em;font-weight:600;margin-bottom:16px}}
.btn{{display:inline-block;padding:10px 24px;background:#6366f1;color:#fff;border-radius:8px;text-decoration:none;font-size:.9em}}
.stats{{text-align:center;padding:40px 20px;color:#64748b;font-size:.9em}}
.stats span{{color:#a78bfa;font-weight:600}}
.footer{{text-align:center;padding:30px;color:#475569;font-size:.8em;border-top:1px solid #1e293b}}
</style></head><body>
<div class="hero"><h1>ATEX 合规 + AI 平台</h1><p>4个合规工具 · 8大AI能力 · MCP协议 · 支付宝充值 · 余额永不过期</p><a href="https://genetech.tools/credits.html" class="cta">💎 充值</a><a href="https://lm203688.github.io/atex/" class="cta" style="background:#475569;margin-left:8px">开始使用</a></div>
<div class="section"><h2>🛡️ 合规工具</h2><div class="tools">{compliance_cards}</div></div>
<div class="section"><h2>🤖 AI能力</h2><div class="tools">{ai_cards}</div></div>
<div class="section"><h2>🧬 知识引擎生态</h2><p style="color:#94a3b8;margin-bottom:20px">ATEX是GeneTech知识引擎生态的AI能力层。12个前沿科技知识库，覆盖基因技术/中医药/Agent生态/机器人/量子计算/脑科学/核能/系外行星/外星矿物/深海/新能源/生命科学。</p><div class="tools"><div class="card"><h3>🧬 GeneTech Tools</h3><p>基因技术知识引擎，300+实体</p><a href="https://genetech.tools" class="btn" target="_blank">访问</a></div><div class="card"><h3>🌿 中医药知识库</h3><p>1755+中药/方剂/疾病实体</p><a href="https://tcm.genetech.tools" class="btn" target="_blank">访问</a></div><div class="card"><h3>🔌 Agent生态</h3><p>MCP/SDK/协议/向量数据库</p><a href="https://agent.genetech.tools" class="btn" target="_blank">访问</a></div><div class="card"><h3>⚛️ 量子计算</h3><p>量子处理器/算法/纠错</p><a href="https://quantum.genetech.tools" class="btn" target="_blank">访问</a></div><div class="card"><h3>🧠 脑科学</h3><p>脑机接口/神经调控/认知</p><a href="https://brain.genetech.tools" class="btn" target="_blank">访问</a></div><div class="card"><h3>⚡ 新能源</h3><p>固态电池/钙钛矿/绿氢</p><a href="https://energy.genetech.tools" class="btn" target="_blank">访问</a></div><div class="card"><h3>🦠 生命科学</h3><p>CRISPR/细胞疗法/合成生物</p><a href="https://life.genetech.tools" class="btn" target="_blank">访问</a></div><div class="card"><h3>🔭 更多领域</h3><p>核能/系外行星/深海/机器人/矿物</p><a href="https://genetech.tools" class="btn" target="_blank">全部站点</a></div></div></div>
<div class="stats">已注册 <span>{len(svcs)}</span> 个服务 · <span>{exchange.accounts.__len__() if hasattr(exchange.accounts,'__len__') else '?'}</span> 个用户</div>
<div class="footer">© 2026 ATEX · <a href="https://genetech.tools" style="color:#818cf8">GeneTech生态</a> · 合规工具 + AI能力 + 知识引擎 · 符合《广告法》《数据出境安全评估办法》《人工智能生成合成内容标识办法》</div>
</body></html>'''
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _read(self):
        l = int(self.headers.get('Content-Length', 0))
        if l > MAX_INPUT_SIZE: return None
        return json.loads(self.rfile.read(l)) if l > 0 else {}
    def do_OPTIONS(self): self._json({}, 204)
    def do_GET(self):

        if not ip_limiter.check(self._ip()): return self._json({"err":"rate_limited"}, 429)
        p = urlparse(self.path).path

        # ── Landing Page ──
        if p == '/' or p == '/index.html':
            self._landing_page()
            return

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
                "alipay": "扫码支付（虎皮椒自动到账）",
                "paypal": "COMING_SOON",
                "min_topup_cny": 10.0,
                "note": f"使用 /v1/pay/alipay 创建支付订单，扫码付款后自动到账",
                "steps": [
                    f"1. POST /v1/pay/alipay {{\"amount_cny\": 10}} (需Bearer认证)",
                    "2. 打开返回的pay_url扫码支付",
                    "3. 付款后自动到账（含赠送积分）",
                    f"4. 当前余额: ¥{user.get('balance_cny', 0):.2f}",
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
        elif p == '/api/v1/categories':
            # v5.16: 服务分类列表
            svcs = exchange.list_services().get("services", [])
            cats = {}
            for s in svcs:
                c = s.get("category", "未分类")
                st = s.get("service_type", "llm")
                if c not in cats:
                    cats[c] = {"count": 0, "services": [], "types": set()}
                cats[c]["count"] += 1
                cats[c]["services"].append({"id": s["id"], "name": s["name"], "price": s.get("price",0), "service_type": st})
                cats[c]["types"].add(st)
            for c in cats:
                cats[c]["types"] = list(cats[c]["types"])
            self._json({"categories": cats, "total": len(svcs)})
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
        # ── GEO/AI可发现 ──
        elif p == '/llms.txt': self._llms_txt()
        elif p == '/llms-full.txt': self._llms_full_txt()
        elif p == '/robots.txt': self._robots_txt()
        # ── MCP协议端点（Streamable HTTP）──
        elif p == '/mcp': self._mcp_get()
        elif p == '/.well-known/mcp/server-card.json': self._mcp_server_card()
        # ── Job市场 ──
        elif p.startswith('/v1/jobs/') and p.endswith('/bids'):
            job_id = p.split('/')[3]
            job = get_job(job_id)
            self._json(job.get("job", job) if job.get("ok") else job)
        elif p.startswith('/v1/jobs/'):
            job_id = p.split('/')[3]
            self._json(get_job(job_id))
        # ── Skill市场 ──
        elif p == '/v1/skills':
            qs = parse_qs(urlparse(self.path).query)
            filters = {k: v[0] for k, v in qs.items() if v}
            self._json(list_skills(filters))
        # ── Job市场GET ──
        elif p == '/v1/jobs':
            qs = parse_qs(urlparse(self.path).query)
            filters = {k: v[0] for k, v in qs.items() if v}
            self._json(list_jobs(filters))
        elif p.startswith('/v1/skills/'):
            skill_id = p.split('/')[3]
            # Special GET routes (before skill_id matching)
            if skill_id == 'defense' and p.endswith('/baseline'):
                self._json({"ok": True, "baseline": PROMPT_DEFENSE_BASELINE})
            elif skill_id == 'import' and p.endswith('/ecc'):
                # GET version of import (list importable ECC skills)
                self._json({"ok": True, "message": "Use POST to import ECC skills"})
            elif skill_id == 'parse' and p.endswith('/ecc'):
                # GET version of parse
                self._json({"ok": True, "message": "Use POST to parse ECC content"})
            else:
                # ECC格式输出
                qs = parse_qs(urlparse(self.path).query)
                if qs.get("format", [""])[0] == "ecc":
                    self._json(get_skill_ecc_format(skill_id))
                else:
                    self._json(get_skill(skill_id))
        # ── 通知 ──
        elif p == '/v1/notifications':
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else None
            if not uid: return self._json({"err": "auth_required"}, 401)
            qs = parse_qs(urlparse(self.path).query)
            self._json(get_notifications(uid, unread_only=qs.get("unread_only", [""])[0] == "true"))
        # ── A2A协议 ──
        elif p == '/v1/a2a/info':
            self._json(a2a_protocol_info())
        elif p == '/v1/a2a/agents':
            qs = parse_qs(urlparse(self.path).query)
            filters = {k: v[0] for k, v in qs.items() if v}
            self._json(discover_agents(filters))
        elif p == '/v1/a2a/agents/stats':
            self._json(a2a_stats())
        elif p.startswith('/v1/a2a/agents/'):
            agent_uid = p.split('/')[4]
            self._json(get_agent_card(agent_uid))
        elif p == '/v1/a2a/tasks':
            qs = parse_qs(urlparse(self.path).query)
            filters = {k: v[0] for k, v in qs.items() if v}
            self._json(list_tasks(filters))
        elif p.startswith('/v1/a2a/tasks/') and not any(p.endswith(x) for x in ['/message','/accept','/reject','/complete','/fail','/bridge/job']):
            task_id = p.split('/')[4]
            self._json(get_task(task_id))
        elif p == '/v1/notifications/stream':
            # SSE endpoint
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else None
            if not uid: return self._json({"err": "auth_required"}, 401)
            qs = parse_qs(urlparse(self.path).query)
            last_id = qs.get("last_id", [None])[0]
            events = sse_events(uid, last_id)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(events.encode())
        # ── 安全统计 ──
        elif p == '/v1/safety/stats': self._json(safety_stats())
        # ── Agent Job统计 ──
        elif p.startswith('/v1/agent/') and p.endswith('/stats'):
            uid = p.split('/')[3]
            self._json(agent_stats(uid))
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
            model = d.get("model", "glm-4-plus")
            model_info = SAAS_PRICING.get(model)
            if not model_info: return self._json({"err": f"unknown_model:{model}", "available": list(SAAS_PRICING.keys())}, 400)
            if model_info.get("status") == "coming_soon":
                return self._json({"err": f"model_coming_soon:{model}", "message": f"{model_info['name']} is coming soon. Register as a provider to offer it."}, 400)
            # 先检查余额是否足够（最低估算，防止API白调）
            min_cost = 0.001
            if user["balance_cny"] < min_cost:
                return self._json({"err": "insufficient_balance", "balance_cny": user["balance_cny"]}, 402)
            # 调用底层API — 优先z-ai SDK（免费GLM-4-Plus），DeepSeek备用
            messages = d.get("messages", [])
            prompt = messages[-1].get("content", "") if messages else ""
            # 直接使用_sdk_chat（z-ai SDK），绕过DeepSeek API
            result = execute_api_proxy("openai_gpt4o_mini", {"prompt": prompt, "messages": messages})
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
                    "alipay": "CONFIGURE_ALIPAY",
                    "paypal": "CONFIGURE_PAYPAL",
                    "note": f"请转账{amount}元，备注填写参考码：{ref_code}",
                    "steps": [
                        f"1. 支付宝转账至 CONFIGURE_ALIPAY",
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

        # ── v5.16: 支付闭环 — 自动充值 + 提现 ──
        elif p == '/v1/pay/crypto':
            # Crypto支付：NOWPayments创建订单
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            user = _saas_user(auth) if auth else None
            if not user: return self._json({"err": "invalid_api_key"}, 401)
            amount_usd = d.get("amount_usd", 0)
            pay_currency = d.get("pay_currency", "usdttrc20")  # usdttrc20/usdterc20/usdc/etc
            if amount_usd < 1: return self._json({"err": "min_1_usd"}, 400)
            r = nowpayments_create_order(user["user_id"], amount_usd, pay_currency)
            self._json(r, 200 if r.get("ok") else 400)

        elif p == '/v1/pay/crypto/callback':
            # NOWPayments IPN回调（NOWPayments服务器调用）
            r = nowpayments_ipn_callback(d)
            self._json(r)

        elif p == '/v1/pay/alipay':
            # 支付宝扫码：虎皮椒创建订单
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            user = _saas_user(auth) if auth else None
            if not user: return self._json({"err": "invalid_api_key"}, 401)
            amount_cny = d.get("amount_cny", 0)
            if amount_cny < 10: return self._json({"err": "min_10_cny"}, 400)
            r = xunhupay_create_order(user["user_id"], amount_cny)
            self._json(r, 200 if r.get("ok") else 400)

        elif p == '/v1/pay/alipay/callback':
            # 虎皮椒回调
            r = xunhupay_callback(d)
            self._json(r)

        elif p == '/v1/pay/rates':
            # 查询当前ATEX汇率
            self._json(get_exchange_rates())

        elif p == '/v1/pay/rates/update':
            # 管理员调整汇率（浮动机制）
            r = update_exchange_rate(
                usd_to_atex=d.get("usd_to_atex"),
                cny_to_atex=d.get("cny_to_atex"),
                admin_token=d.get("admin_token", "")
            )
            self._json(r, 200 if r.get("ok") else 403)

        elif p == '/v1/withdraw':
            # 提现申请（服务提供方）
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            user = _saas_user(auth) if auth else None
            if not user: return self._json({"err": "invalid_api_key"}, 401)
            r = request_withdrawal(
                user_id=user["user_id"],
                amount_atex=d.get("amount_atex", 0),
                method=d.get("method", "paypal"),  # paypal | worldfirst
                destination=d.get("destination", "")  # PayPal邮箱 或 万里汇账号
            )
            self._json(r, 200 if r.get("ok") else 400)

        elif p == '/v1/withdraw/approve':
            # 管理员审批提现
            r = approve_withdrawal(
                withdrawal_id=d.get("withdrawal_id", ""),
                admin_token=d.get("admin_token", ""),
                action=d.get("action", "approve")  # approve | reject
            )
            self._json(r, 200 if r.get("ok") else 400)

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

        # ── Agent预算管理 ──
        elif p == '/v1/budget/set':
            uid, auth = d.get("user_id",""), self.headers.get("Authorization","").replace("Bearer ","")
            if not uid and auth:
                saas_data = _load_saas()
                uid = saas_data["api_keys"].get(auth) or ""
            if not uid: return self._json({"err": "user_id or Bearer token required"}, 400)
            budgets = _load_budgets()
            budgets[uid] = {
                "daily_cny": d.get("daily_cny"),
                "monthly_cny": d.get("monthly_cny"),
                "per_action_cny": d.get("per_action_cny"),
                "alert_cny": d.get("alert_cny"),
                "daily_spent": budgets.get(uid, {}).get("daily_spent", {}),
                "monthly_spent": budgets.get(uid, {}).get("monthly_spent", {}),
                "updated_at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
            }
            _save_budgets(budgets)
            self._json({"ok": True, "user_id": uid, "budget": {k:v for k,v in budgets[uid].items() if k not in ("daily_spent","monthly_spent")}})
        elif p == '/v1/budget/status':
            uid, auth = d.get("user_id",""), self.headers.get("Authorization","").replace("Bearer ","")
            if not uid and auth:
                saas_data = _load_saas()
                uid = saas_data["api_keys"].get(auth) or ""
            if not uid: return self._json({"err": "user_id or Bearer token required"}, 400)
            budgets = _load_budgets()
            user_budget = budgets.get(uid)
            if not user_budget:
                return self._json({"ok": True, "user_id": uid, "budget": None, "message": "No budget limits set. Set via POST /v1/budget/set"})
            now = datetime.now(TZ)
            today = now.strftime("%Y-%m-%d")
            month = now.strftime("%Y-%m")
            daily_spent = user_budget.get("daily_spent", {}).get(today, 0)
            monthly_spent = user_budget.get("monthly_spent", {}).get(month, 0)
            alerts = []
            alert_threshold = user_budget.get("alert_cny")
            if alert_threshold and daily_spent >= alert_threshold:
                alerts.append(f"Daily spending ({daily_spent:.4f} CNY) exceeded alert threshold ({alert_threshold} CNY)")
            self._json({
                "ok": True, "user_id": uid,
                "limits": {
                    "daily_cny": user_budget.get("daily_cny"),
                    "monthly_cny": user_budget.get("monthly_cny"),
                    "per_action_cny": user_budget.get("per_action_cny"),
                    "alert_cny": user_budget.get("alert_cny")
                },
                "spent": {
                    "today_cny": round(daily_spent, 4),
                    "this_month_cny": round(monthly_spent, 4)
                },
                "remaining": {
                    "daily_cny": round(user_budget["daily_cny"] - daily_spent, 4) if user_budget.get("daily_cny") else None,
                    "monthly_cny": round(user_budget["monthly_cny"] - monthly_spent, 4) if user_budget.get("monthly_cny") else None
                },
                "alerts": alerts,
                "updated_at": user_budget.get("updated_at")
            })

        # ── 原ATEX路由 ──
        elif p == '/api/v1/account/create':
            r = exchange.create_account(d.get("account_id",""), d.get("role","trader"))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/deposit':
            r = exchange.deposit(d.get("account",""), d.get("amount",0))
            self._json(r, 200 if r.get("ok") else 400)
        # ── 支付配置 ──
        elif p == '/api/v1/payment/config':
            from payment.gateway import _load_config, _save_config
            cfg = _load_config()
            # POST only: update config
            setup_token = d.get("setup_token", "")
            if setup_token != "atex_setup_2026":
                return self._json({"err": "invalid_setup_token"}, 403)
            if "xunhupay" in d:
                cfg["xunhupay"] = {
                        "app_id": d["xunhupay"].get("app_id", cfg.get("xunhupay",{}).get("app_id","")),
                        "app_secret": d["xunhupay"].get("app_secret", cfg.get("xunhupay",{}).get("app_secret","")),
                        "notify_url": d["xunhupay"].get("notify_url", f"http://{self.headers.get('Host','150.158.119.19:8420')}/v1/pay/alipay/callback"),
                        "return_url": d["xunhupay"].get("return_url", "https://lm203688.github.io/atex/"),
                        "enabled": d["xunhupay"].get("enabled", True)
                    }
            if "nowpayments" in d:
                cfg["nowpayments"] = {**cfg.get("nowpayments",{}), **d["nowpayments"]}
            _save_config(cfg)
            self._json({"ok": True, "msg": "Payment config updated"})
        # ── 充值下单（虎皮椒）──
        elif p == '/api/v1/payment/create':
            # xunhupay_create_order imported at top level
            r = xunhupay_create_order(d.get("account",""), d.get("amount_cny",0))
            self._json(r, 200 if r.get("ok") else 400)
        # ── 虎皮椒回调 ──
        elif p == '/v1/pay/alipay/callback':
            # xunhupay_callback imported at top level
            r = xunhupay_callback(d)
            if r.get("ok"):
                # Auto-deposit ATEX credits
                order_id = d.get("trade_order_id", "")
                # Find the deposit record
                # _load_records imported at top level
                rec = _load_records()
                for dep in rec.get("deposits", []):
                    if dep.get("order_id") == order_id and dep.get("status") == "pending":
                        exchange.deposit(dep.get("user_id",""), dep.get("atex_amount",0))
                        dep["status"] = "completed"
                        from payment.gateway import _save_records
                        _save_records(rec)
                        break
            self._json(r)
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
                d.get("description",""), d.get("price",0), d.get("unit",""), d.get("category",""),
                d.get("service_type", "llm"))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/services/buy':
            r = exchange.buy_service(d.get("buyer",""), d.get("service_id",""), d.get("quantity",1))
            # Execute service and return result
            if r.get("ok"):
                svc_params = d.get("params", {})
                exec_result = execute_service(d.get("service_id",""), svc_params, d.get("buyer",""))
                r["service_result"] = exec_result
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/api/v1/workflow':
            # v6.0: 工作流编排已下线，返回提示
            self._json({"ok": False, "err": "workflow_deprecated", "hint": "请使用 /api/v1/services/buy 调用具体服务"}, 410)
        elif p == '/api/v1/discover':
            # v6.0: Agent服务发现已下线，返回服务列表
            svcs = exchange.list_services()
            self._json({"ok": True, "services": svcs, "hint": "请使用 /api/v1/services/buy 调用具体服务"}, 200)
        elif p == '/api/v1/services/execute':
            # 服务执行：需要认证+扣费
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            user = _saas_user(auth) if auth else None
            if not user: return self._json({"err": "authentication_required", "hint": "Set Authorization: Bearer YOUR_ATEX_API_KEY"}, 401)
            service_id = d.get("service_id", "")
            params = d.get("params", {})
            # 查找服务价格
            svc = None
            for s in exchange.svc.get("services", []):
                if s["id"] == service_id and s.get("status", "active") == "active":
                    svc = s; break
            if not svc: return self._json({"err": "service_not_found"}, 404)
            price = svc.get("price", 0)
            if user["balance_cny"] < price:
                return self._json({"err": "insufficient_balance", "have": user["balance_cny"], "need": price}, 402)
            # 执行
            r = execute_service(service_id, params, user)
            # 扣费
            _deduct(user["user_id"], price, service_id, 0, 0)
            r["cost_cny"] = price
            r["balance_cny"] = round(user["balance_cny"] - price, 4)
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
                    r1 = subprocess.run(["curl", "-L", "https://github.com/lm203688/atex/archive/refs/heads/main.tar.gz", "-o", "/tmp/atex_latest.tar.gz"], capture_output=True, timeout=120)
                    r2 = subprocess.run(["tar", "xzf", "/tmp/atex_latest.tar.gz", "-C", "/tmp/"], capture_output=True, timeout=30)
                    r3 = subprocess.run(["cp", "-r", "/tmp/atex-main/token_exchange/.", install_dir + "/"], capture_output=True, timeout=10)
                    subprocess.run(["rm", "-rf", "/tmp/atex-main", "/tmp/atex_latest.tar.gz"], capture_output=True, timeout=5)
                    subprocess.run(["bash", "-c", f"fuser -k 8420/tcp; sleep 2; nohup python3 {install_dir}/api/server.py > /dev/null 2>&1 &"], capture_output=True, timeout=15)
                    self._json({"ok": True, "message": "Code updated and service restarted."})
                except Exception as e:
                    self._json({"ok": False, "err": str(e)})
            elif action == "write_config":
                # Admin: write payment_config.json to server
                config_data = d.get("config", {})
                if not config_data:
                    return self._json({"err": "missing_config"}, 400)
                install_dir = os.environ.get("ATEX_HOME", "/home/ubuntu/atex")
                config_path = os.path.join(install_dir, "data", "payment_config.json")
                os.makedirs(os.path.dirname(config_path), exist_ok=True)
                with open(config_path, "w") as f:
                    json.dump(config_data, f, indent=2, ensure_ascii=False)
                self._json({"ok": True, "message": f"Config written to {config_path}"})
            else:
                self._json({"err": "unknown_action"})

        # ── 结算（仅owner，平台佣金→ATEX）──
        elif p == '/api/v1/settle':
            r = exchange.settle(d.get("account",""), d.get("amount",0))
            self._json(r, 200 if r.get("ok") else 400)
        # ── MCP协议端点（Streamable HTTP）──
        elif p == '/mcp':
            self._mcp_post(d)
        # ── Job市场 ──
        elif p == '/v1/jobs/create':
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("employer")
            if not uid: return self._json({"err": "auth_required"}, 401)
            # 内容安全检查
            safety = scan_content(d.get("description", ""), "job")
            if not safety.get("safe") and safety.get("risk_level") == "high": return self._json({"err": "content_blocked", "reason": safety.get("threats", [])}, 403)
            r = create_job(uid, d)
            if r.get("ok"): send_notification(uid, "job_created", {"job_id": r["job_id"], "title": d.get("title","")})
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/jobs/') and p.endswith('/bid'):
            job_id = p.split('/')[3]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("agent")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = submit_bid(uid, job_id, d)
            if r.get("ok"):
                job = get_job(job_id)
                if job.get("ok"): send_notification(job["job"]["employer"], "new_bid", {"job_id": job_id, "agent": uid, "price": d.get("price")})
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/jobs/') and p.endswith('/accept'):
            job_id = p.split('/')[3]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("employer")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = accept_bid(uid, job_id, d.get("agent", ""))
            if r.get("ok"):
                job = get_job(job_id)
                if job.get("ok"): send_notification(job["job"].get("assigned_to", ""), "bid_accepted", {"job_id": job_id})
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/jobs/') and p.endswith('/start'):
            job_id = p.split('/')[3]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("agent")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = start_job(uid, job_id)
            if r.get("ok"): send_notification(get_job(job_id).get("job", {}).get("employer", ""), "job_started", {"job_id": job_id})
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/jobs/') and p.endswith('/result'):
            job_id = p.split('/')[3]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("agent")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = submit_result(uid, job_id, d)
            if r.get("ok"): send_notification(get_job(job_id).get("job", {}).get("employer", ""), "result_submitted", {"job_id": job_id})
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/jobs/') and p.endswith('/rate'):
            job_id = p.split('/')[3]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("employer")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = rate_job(uid, job_id, d)
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/jobs/') and p.endswith('/dispute'):
            job_id = p.split('/')[3]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("user")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = dispute_job(uid, job_id, d)
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/jobs/') and p.endswith('/cancel'):
            job_id = p.split('/')[3]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("employer")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = cancel_job(uid, job_id)
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/jobs/') and p.endswith('/withdraw'):
            job_id = p.split('/')[3]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("agent")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = withdraw_bid(uid, job_id)
            self._json(r, 200 if r.get("ok") else 400)
        # ── Skill市场 ──
        elif p == '/v1/skills/publish':
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("author")
            if not uid: return self._json({"err": "auth_required"}, 401)
            safety = scan_content(d.get("description", "") + " " + d.get("content", ""), "skill")
            if not safety.get("safe") and safety.get("risk_level") == "high": return self._json({"err": "content_blocked", "reason": safety.get("threats", [])}, 403)
            r = publish_skill(uid, d)
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/skills/') and p.endswith('/buy'):
            skill_id = p.split('/')[3]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("buyer")
            if not uid: return self._json({"err": "auth_required"}, 401)
            output_format = d.get("format")  # "ecc" for ECC format output
            r = buy_skill(uid, skill_id, output_format=output_format)
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/skills/') and p.endswith('/rate'):
            skill_id = p.split('/')[3]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("user")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = rate_skill_file(skill_id, uid, d)
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/skills/') and p.endswith('/update'):
            skill_id = p.split('/')[3]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("author")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = update_skill(uid, skill_id, d)
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/skills/') and p.endswith('/remove'):
            skill_id = p.split('/')[3]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("author")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = remove_skill(uid, skill_id)
            self._json(r, 200 if r.get("ok") else 400)
        # ── ECC兼容路由 ──
        elif p == '/v1/skills/import/ecc':
            # 批量导入ECC格式Skills
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("author")
            if not uid: return self._json({"err": "auth_required"}, 401)
            skills_data = d.get("skills", [])
            if not skills_data: return self._json({"err": "skills_array_required"}, 400)
            r = import_ecc_skills(uid, skills_data)
            self._json(r)
        elif p == '/v1/skills/parse/ecc':
            # 解析ECC格式Skill（不保存，仅预览解析结果）
            content = d.get("content", "")
            if not content: return self._json({"err": "content_required"}, 400)
            if not is_ecc_format(content):
                return self._json({"ok": True, "is_ecc_format": False, "message": "Not ECC format (missing YAML frontmatter)"})
            meta = parse_ecc_skill(content)
            if not meta:
                return self._json({"err": "parse_failed"}, 400)
            self._json({"ok": True, "is_ecc_format": True, "metadata": {k:v for k,v in meta.items() if not k.startswith('_')},
                        "body_preview": meta.get("_body", "")[:200]})
        elif p == '/v1/skills/defense/baseline':
            # 获取Prompt Defense Baseline
            self._json({"ok": True, "baseline": PROMPT_DEFENSE_BASELINE})
        # ── A2A协议 ──
        elif p == '/v1/a2a/agents/register':
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("agent_uid")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = register_agent(uid, d)
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/v1/a2a/agents/deregister':
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("agent_uid")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = deregister_agent(uid)
            self._json(r)
        elif p == '/v1/a2a/tasks':
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("sender")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = create_task(uid, d)
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/a2a/tasks/') and p.endswith('/message'):
            task_id = p.split('/')[4]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("sender")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = a2a_send_message(uid, task_id, d)
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/a2a/tasks/') and p.endswith('/accept'):
            task_id = p.split('/')[4]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("agent_uid")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = accept_task(uid, task_id)
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/a2a/tasks/') and p.endswith('/reject'):
            task_id = p.split('/')[4]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("agent_uid")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = reject_task(uid, task_id, d.get("reason", ""))
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/a2a/tasks/') and p.endswith('/complete'):
            task_id = p.split('/')[4]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("agent_uid")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = complete_task(uid, task_id, d.get("result", {}))
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/a2a/tasks/') and p.endswith('/fail'):
            task_id = p.split('/')[4]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("agent_uid")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = fail_task(uid, task_id, d.get("error", "unknown"))
            self._json(r, 200 if r.get("ok") else 400)
        elif p.startswith('/v1/a2a/tasks/') and p.endswith('/bridge/job'):
            task_id = p.split('/')[4]
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("agent_uid")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = a2a_to_job(uid, task_id)
            self._json(r, 200 if r.get("ok") else 400)
        # ── 内容安全 ──
        elif p == '/v1/safety/scan':
            r = scan_content(d.get("content", ""), d.get("content_type", "general"))
            self._json(r)
        elif p == '/v1/safety/report':
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("reporter")
            if not uid: return self._json({"err": "auth_required"}, 401)
            r = submit_report(uid, d)
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/v1/safety/report/resolve':
            r = resolve_report(d.get("report_id", ""), d.get("action", "dismiss"), d.get("admin", ""))
            self._json(r, 200 if r.get("ok") else 400)
        elif p == '/v1/safety/reports':
            self._json(list_reports(d.get("status"), d.get("limit", 50)))
        # ── 通知 ──
        elif p == '/v1/notifications/read':
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("user_id")
            if not uid: return self._json({"err": "auth_required"}, 401)
            self._json(mark_read(uid, d.get("notification_id")))
        elif p == '/v1/notifications/subscribe':
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("user_id")
            if not uid: return self._json({"err": "auth_required"}, 401)
            self._json(subscribe(uid, d.get("callback_url", "")))
        elif p == '/v1/notifications/unsubscribe':
            auth = self.headers.get("Authorization", "").replace("Bearer ", "")
            saas_data = _load_saas()
            uid = saas_data["api_keys"].get(auth) if auth else d.get("user_id")
            if not uid: return self._json({"err": "auth_required"}, 401)
            self._json(unsubscribe(uid))
        else: self._json({"err":"not_found"}, 404)

    # ── MCP协议处理（Streamable HTTP）──
    def _mcp_server_card(self):
        """GET /.well-known/mcp/server-card.json — Smithery扫描用"""
        self._json({
            "name": "ATEX AI Gateway",
            "description": "23 AI services + 12 knowledge engines. Compliance tools (banned words, AI visibility, SEO), AI capabilities (TTS, ASR, VLM, image/video gen), knowledge engines (gene tech, TCM, quantum, robotics, deep sea, exo-science, etc.), LLM chat (DeepSeek/GPT-4o/Claude). Pay-per-use via ATEX credits.",
            "version": "6.0",
            "url": "http://150.158.119.19:8420/mcp",
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "ATEX AI Gateway", "version": "6.0"},
            "repository": {"url": "https://github.com/lm203688/atex/tree/main/mcp-server"},
            "tools": [
                {"name": "chat", "description": "Chat with AI models (DeepSeek, GPT-4o, Claude). Pay-per-use via ATEX API key."},
                {"name": "web_search", "description": "Search the web for real-time information. 5 ATEX per call."},
                {"name": "tts", "description": "Text-to-speech synthesis. 2 ATEX per call."},
                {"name": "asr", "description": "Speech-to-text transcription. 2 ATEX per call."},
                {"name": "vlm", "description": "Vision-language model for image understanding. 3 ATEX per call."},
                {"name": "image_generate", "description": "AI image generation from text. 5 ATEX per call."},
                {"name": "image_edit", "description": "AI image editing. 5 ATEX per call."},
                {"name": "video_generate", "description": "AI video generation. 10 ATEX per call."},
                {"name": "banned_words_check", "description": "Chinese banned/prohibited words detection. 0.1 ATEX per call."},
                {"name": "ai_visibility_check", "description": "AI search visibility analysis. 2 ATEX per call."},
                {"name": "global_compliance", "description": "Cross-border compliance assessment. 8 ATEX per call."},
                {"name": "seo_compliance", "description": "SEO compliance check. 5 ATEX per call."},
                {"name": "knowledge_engines_list", "description": "List all 12 knowledge engines (gene tech, TCM, quantum, robotics, deep sea, exo-science, etc.)"},
                {"name": "knowledge_search", "description": "Search across 12 knowledge engine databases."},
                {"name": "check_balance", "description": "Check your ATEX account balance and usage."},
                {"name": "list_services", "description": "List all 23 available services in the ATEX marketplace."}
            ]
        })

    def _mcp_get(self):
        """GET /mcp — 返回MCP服务器信息（Smithery扫描用）"""
        self._json({
            "name": "ATEX 合规+AI平台",
            "version": "6.0",
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "ATEX 合规+AI平台", "version": "6.0"}
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
                 "inputSchema": {"type": "object", "properties": {"model": {"type": "string", "enum": list(SAAS_PRICING.keys()), "default": "glm-4-plus"}, "messages": {"type": "array", "items": {"type": "object", "properties": {"role": {"type": "string"}, "content": {"type": "string"}}, "required": ["role","content"]}}}, "required": ["messages"]}},
                {"name": "web_search", "description": "Search the web for real-time information. 5 ATEX per call.",
                 "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}}, "required": ["query"]}},
                {"name": "check_balance", "description": "Check your ATEX account balance and usage.",
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "list_models", "description": "List available AI models and their pricing.",
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "list_services", "description": "List all available services in the ATEX marketplace.",
                 "inputSchema": {"type": "object", "properties": {"category": {"type": "string", "description": "Filter by category"}}}},
                {"name": "cn_banned_word_check", "description": "中文违禁词检测 - 检测文本中的违禁词/敏感词，返回法律条文+罚款金额+替换建议。0.1 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "description": "待检测文本"}, "platform": {"type": "string", "description": "平台: douyin/xiaohongshu/wechat/weibo/bilibili/kuaishou/all", "default": "all"}}, "required": ["text"]}},
                {"name": "ai_search_visibility", "description": "AI搜索可见度检测 - 检测品牌在DeepSeek/Kimi等AI搜索引擎中的排名。2 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"brand": {"type": "string", "description": "品牌名称"}, "keyword": {"type": "string", "description": "关键词"}, "competitors": {"type": "array", "items": {"type": "string"}, "description": "竞品列表"}}, "required": ["brand"]}},
                {"name": "global_compliance_check", "description": "出海合规评估 - 7维度问卷式评估产品出海合规风险，生成详细报告。8 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"product_type": {"type": "string", "description": "产品类型: SaaS/App/硬件/内容"}, "markets": {"type": "array", "items": {"type": "string"}, "description": "目标市场: US/EU/JP/SEA等"}, "data_categories": {"type": "array", "items": {"type": "string"}, "description": "数据类别"}, "answers": {"type": "object", "description": "问卷答案(7维度)"}}, "required": []}},
                {"name": "seo_compliance_check", "description": "SEO合规检测 - 检测网页/内容的SEO合规性，避免搜索引擎惩罚。1 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "description": "待检测文本或URL"}, "platform": {"type": "string", "description": "平台", "default": "all"}}, "required": ["text"]}},
                # ── AI能力层 (svc_101-108) ──
                {"name": "tts_synthesis", "description": "语音合成(TTS) - 将文本转换为自然流畅的语音，输出WAV/MP3。2 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "description": "待合成文本（最长5000字）"}, "voice": {"type": "string", "description": "音色: tongtong/xiaochen/yunyang等", "default": "tongtong"}, "speed": {"type": "number", "description": "语速 0.5-2.0", "default": 1.0}, "format": {"type": "string", "description": "输出格式: wav/mp3", "default": "wav"}}, "required": ["text"]}},
                {"name": "asr_recognition", "description": "语音识别(ASR) - 将音频转换为文字，支持WAV/MP3。2 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"audio_base64": {"type": "string", "description": "音频Base64编码"}, "audio_file": {"type": "string", "description": "音频文件路径"}, "language": {"type": "string", "description": "语言: zh/en/auto", "default": "auto"}}, "required": []}},
                {"name": "vlm_understand", "description": "图像理解(VLM) - 分析图片内容，OCR/物体检测/视觉问答。3 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"image_base64": {"type": "string", "description": "图片Base64编码"}, "image_url": {"type": "string", "description": "图片URL"}, "prompt": {"type": "string", "description": "提问/指令", "default": "请描述这张图片的内容"}}, "required": []}},
                {"name": "image_generate", "description": "AI图片生成 - 根据文字描述生成图片。5 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string", "description": "图片描述/提示词"}, "size": {"type": "string", "description": "尺寸: 1024x1024/1344x768等", "default": "1024x1024"}, "style": {"type": "string", "description": "风格: natural/vivid/anime等", "default": "natural"}}, "required": ["prompt"]}},
                {"name": "image_edit", "description": "AI图片编辑 - 对现有图片进行AI编辑，风格迁移/局部修改。5 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"image_base64": {"type": "string", "description": "原始图片Base64编码"}, "prompt": {"type": "string", "description": "编辑指令/描述"}, "size": {"type": "string", "description": "输出尺寸", "default": "1024x1024"}}, "required": ["image_base64", "prompt"]}},
                {"name": "video_generate", "description": "AI视频生成 - 根据描述生成5秒视频片段（异步任务）。10 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string", "description": "视频描述/提示词"}, "image_base64": {"type": "string", "description": "参考图片Base64（可选）"}, "size": {"type": "string", "description": "尺寸", "default": "1344x768"}, "duration": {"type": "number", "description": "时长(秒)", "default": 5}}, "required": ["prompt"]}},
                {"name": "web_search_ai", "description": "Web搜索 - 搜索全网实时信息，返回结构化结果。5 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "搜索关键词/查询"}}, "required": ["query"]}},
                {"name": "web_reader", "description": "网页阅读 - 提取网页正文，自动去噪返回干净内容。3 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"url": {"type": "string", "description": "网页URL"}, "format": {"type": "string", "description": "输出格式: html/text", "default": "text"}}, "required": ["url"]}},
                # ── cangjie-skill 书籍蒸馏 ──
                {"name": "book_distill", "description": "书籍蒸馏(cangjie) - RIA-TV++六阶段流水线，将书籍内容转化为可执行的AI技能包。8 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"book_title": {"type": "string", "description": "书名"}, "content": {"type": "string", "description": "书籍内容文本（至少100字）"}, "num_skills": {"type": "integer", "description": "提取技能数量(1-20)", "default": 8}}, "required": ["book_title", "content"]}},
                {"name": "skill_query", "description": "技能包查询 - 搜索已蒸馏的RIA-TV++技能包，按书名/关键词/ID查询。0.5 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "搜索关键词"}, "book": {"type": "string", "description": "按书名过滤"}, "skill_id": {"type": "string", "description": "按技能ID精确查询"}}, "required": []}},
                # ── 向量检索优化 ──
                {"name": "vector_optimize", "description": "向量检索优化 - 分析向量数据并生成TurboVec/FAISS压缩方案，支持4-32x压缩比。3 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"vector_size_mb": {"type": "number", "description": "向量数据大小(MB)"}, "vector_dim": {"type": "integer", "description": "向量维度", "default": 768}, "num_vectors": {"type": "integer", "description": "向量数量（可代替size_mb）"}, "current_engine": {"type": "string", "description": "当前引擎: faiss/milvus/chroma", "default": "faiss"}, "use_case": {"type": "string", "description": "场景: RAG/搜索/推荐", "default": "RAG"}, "hardware": {"type": "string", "description": "硬件: V100/A100/Mac/纯CPU", "default": "unknown"}}, "required": []}},
                # ── Token瘦身(lowfat) ──
                {"name": "token_slim", "description": "Token瘦身(lowfat) - 在命令输出到达AI代理前过滤噪音，节省高达91.8% Token成本。1 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "description": "待过滤的文本内容"}, "mode": {"type": "string", "description": "过滤模式: aggressive/balanced/conservative", "default": "balanced"}, "rules": {"type": "object", "description": "自定义过滤规则(可选)"}}, "required": ["text"]}},
                # ── AI浏览器自动化(BrowserAct) ──
                {"name": "browser_act", "description": "AI浏览器自动化(BrowserAct) - AI Agent操作浏览器，自动规划步骤+生成Playwright代码，支持点网页/填表单/过验证/数据采集。5 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"task": {"type": "string", "description": "任务描述（AI要做什么）"}, "url": {"type": "string", "description": "起始页面URL（可选）"}, "mode": {"type": "string", "description": "模式: auto/assisted/headless", "default": "auto"}, "timeout": {"type": "integer", "description": "超时秒数(最大300)", "default": 60}}, "required": ["task"]}},
                # ── 网络安全技能库(Anthropic Cybersecurity Skills) ──
                {"name": "cyber_skill_lookup", "description": "网络安全技能查询 - 754个安全skills，映射5大框架(MITRE ATT&CK/NIST CSF/D3FEND/OWASP/ISO27001)，覆盖26个安全域。1 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"domain": {"type": "string", "description": "安全域: DFIR/Red_Team/AppSec/Cloud_Security等"}, "framework": {"type": "string", "description": "框架: MITRE ATT&CK/NIST CSF/D3FEND/OWASP/ISO27001"}, "skill": {"type": "string", "description": "技能关键词搜索"}}, "required": []}},
                {"name": "cyber_skill_generate", "description": "安全技能生成 - 根据安全场景生成AI Agent可执行的安全技能，含MITRE ATT&CK映射+执行步骤+工具。5 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"scenario": {"type": "string", "description": "安全场景描述"}, "target": {"type": "string", "description": "目标系统/应用（可选）"}, "domain": {"type": "string", "description": "安全域(auto自动识别)", "default": "auto"}, "framework": {"type": "string", "description": "参考框架", "default": "MITRE ATT&CK"}}, "required": ["scenario"]}},
                # ── 知识引擎 (12 domains) ──
                {"name": "knowledge_engines_list", "description": "列出所有12个知识引擎及其覆盖范围。免费查询。",
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "knowledge_search", "description": "搜索12个知识引擎数据库 - 基因技术/中医药/Agent生态/量子计算/脑科学/核能/系外行星/外星矿物/深海/新能源/生命科学/机器人。0.5 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"engine": {"type": "string", "description": "知识引擎: genetech/tcm/agent/quantum/brain/nuclear/exo/mineral/deepsea/energy/life/robot/all", "default": "all"}, "query": {"type": "string", "description": "搜索关键词"}, "category": {"type": "string", "description": "分类过滤(如: genes/herbs/mcp_servers/exoplanets/sensors等)"}}, "required": ["query"]}},
                {"name": "knowledge_entity_detail", "description": "获取知识引擎中特定实体的详细信息。0.5 ATEX/次",
                 "inputSchema": {"type": "object", "properties": {"engine": {"type": "string", "description": "知识引擎: genetech/tcm/agent/quantum/brain/nuclear/exo/mineral/deepsea/energy/life/robot"}, "entity_id": {"type": "string", "description": "实体ID(如: GENE-001/EXO-091/SENS-006等)"}}, "required": ["engine", "entity_id"]}},
            ]
            return self._json({"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}})
        elif method == "tools/call":
            tool_name = params.get("name", "")
            args = params.get("arguments", {})
            # ── 统一计费映射 ──
            _BILLABLE_TOOLS = {
                "cn_banned_word_check": ("svc_046", 0.1),
                "ai_search_visibility": ("svc_047", 2.0),
                "global_compliance_check": ("svc_048", 8.0),
                "seo_compliance_check": ("svc_049", 1.0),
                "tts_synthesis": ("svc_101", 2.0),
                "asr_recognition": ("svc_102", 2.0),
                "vlm_understand": ("svc_103", 3.0),
                "image_generate": ("svc_104", 5.0),
                "image_edit": ("svc_105", 5.0),
                "video_generate": ("svc_106", 10.0),
                "web_search_ai": ("svc_107", 5.0),
                "web_reader": ("svc_108", 3.0),
                # ── cangjie-skill 书籍蒸馏 ──
                "book_distill": ("svc_110", 8.0),
                "skill_query": ("svc_111", 0.5),
                # ── 向量检索优化 ──
                "vector_optimize": ("svc_112", 3.0),
                # ── Token瘦身(lowfat) ──
                "token_slim": ("svc_113", 1.0),
                # ── AI浏览器自动化(BrowserAct) ──
                "browser_act": ("svc_114", 5.0),
                # ── 网络安全技能库(Anthropic Cybersecurity Skills) ──
                "cyber_skill_lookup": ("svc_115", 1.0),
                "cyber_skill_generate": ("svc_116", 5.0),
                # ── 知识引擎 ──
                "knowledge_search": ("svc_knowledge", 0.5),
                "knowledge_entity_detail": ("svc_knowledge", 0.5),
            }
            if tool_name in _BILLABLE_TOOLS:
                if not user: return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32001, "message": "Authentication required. Set Authorization: Bearer YOUR_ATEX_API_KEY"}}, 401)
                svc_id, price_cny = _BILLABLE_TOOLS[tool_name]
                # 检查余额
                if user["balance_cny"] < price_cny:
                    return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32002, "message": f"Insufficient balance. Need ¥{price_cny}, have ¥{user['balance_cny']:.2f}. Top up at http://150.158.119.19:8420"}}, 402)
                # 执行服务
                r = execute_service(svc_id, args, user)
                # 扣费（无论服务是否成功，已消耗资源）
                _deduct(user["user_id"], price_cny, svc_id, 0, 0)
                r["cost_cny"] = price_cny
                r["balance_cny"] = round(user["balance_cny"] - price_cny, 4)
                return self._json({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False)}]}})
            elif tool_name == "chat":
                if not user: return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32001, "message": "Authentication required. Set Authorization: Bearer YOUR_ATEX_API_KEY"}}, 401)
                model = args.get("model", "glm-4-plus")
                messages = args.get("messages", [{"role": "user", "content": args.get("prompt", "")}])
                model_info = SAAS_PRICING.get(model)
                if not model_info: return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": f"Unknown model: {model}"}}, 400)
                if model_info.get("status") == "coming_soon": return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": f"Model {model} coming soon"}})
                prompt = messages[-1].get("content", "") if messages else ""
                # 统一使用_sdk_chat（z-ai SDK），绕过DeepSeek API余额问题
                result = execute_api_proxy("openai_gpt4o_mini", {"prompt": prompt, "messages": messages})
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
                # v6.0: web_search通过z-ai SDK LLM回答，扣费0.5元
                if user["balance_cny"] < 0.5:
                    return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32002, "message": "Insufficient balance"}}, 402)
                # 使用_sdk_chat替代_chat（绕过DeepSeek API）
                ws_result = execute_api_proxy("openai_gpt4o_mini", {"prompt": f"关于'{query}'的最新信息：\n请提供关键事实、数据来源和时间线。", "system": "你是信息检索专家，提供准确的事实信息。"})
                ws_text = ws_result.get("content", str(ws_result)) if isinstance(ws_result, dict) else str(ws_result)
                _deduct(user["user_id"], 0.5, "web_search", 0, 0)
                return self._json({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": ws_text}], "cost_cny": 0.5}})
            elif tool_name == "check_balance":
                if not user: return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32001, "message": "Authentication required"}}, 401)
                return self._json({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps({"balance_cny": user["balance_cny"], "total_calls": user.get("total_calls",0)})}]}})
            elif tool_name == "list_models":
                models = [{"id": mid, "name": info["name"], "status": info.get("status","live"), "pricing": {"input_per_1k_cny": info["input_per_1k"], "output_per_1k_cny": info["output_per_1k"]}} for mid, info in SAAS_PRICING.items()]
                return self._json({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(models, ensure_ascii=False)}]}})
            elif tool_name == "list_services":
                svcs = exchange.list_services()
                return self._json({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(svcs, ensure_ascii=False)[:4000]}]}})
            elif tool_name == "knowledge_engines_list":
                engines = [
                    {"id": "genetech", "name": "GeneTech Tools", "domain": "genetech.tools", "entities": 397, "categories": ["genes", "diseases", "gene_therapies", "crispr_applications"]},
                    {"id": "tcm", "name": "TCMDB", "domain": "tcm.genetech.tools", "entities": 1778, "categories": ["herbs", "diseases"]},
                    {"id": "agent", "name": "Agent Ecosystem DB", "domain": "agent.genetech.tools", "entities": 398, "categories": ["mcp_servers", "agent_sdks", "protocols"]},
                    {"id": "quantum", "name": "QuantumDB", "domain": "quantum.genetech.tools", "entities": 273, "categories": ["qubits", "algorithms", "hardware"]},
                    {"id": "brain", "name": "BrainDB", "domain": "brain.genetech.tools", "entities": 252, "categories": ["neurotech", "bci", "cognition"]},
                    {"id": "nuclear", "name": "NuclearDB", "domain": "nuclear.genetech.tools", "entities": 238, "categories": ["reactors", "fusion", "materials"]},
                    {"id": "exo", "name": "ExoDB", "domain": "exo.genetech.tools", "entities": 316, "categories": ["exoplanets", "space_missions", "astrobiology"]},
                    {"id": "mineral", "name": "MineralDB", "domain": "mineral.genetech.tools", "entities": 283, "categories": ["minerals", "asteroids", "mining_tech"]},
                    {"id": "deepsea", "name": "DeepSeaDB", "domain": "deepsea.genetech.tools", "entities": 307, "categories": ["submersibles", "deep_sea_resources", "ocean_energy"]},
                    {"id": "energy", "name": "EnergyDB", "domain": "energy.genetech.tools", "entities": 430, "categories": ["solar", "hydrogen", "batteries"]},
                    {"id": "life", "name": "LifeDB", "domain": "life.genetech.tools", "entities": 475, "categories": ["synthetic_biology", "longevity", "proteins"]},
                    {"id": "robot", "name": "RobotParts DB", "domain": "robot.genetech.tools", "entities": 229, "categories": ["sensors", "actuators", "platforms", "chips"]},
                ]
                return self._json({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(engines, ensure_ascii=False)}]}})
            elif tool_name in ("knowledge_search", "knowledge_entity_detail"):
                # 知识引擎搜索 — 直接读取本地JSON文件
                if not user: return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32001, "message": "Authentication required. Set Authorization: Bearer YOUR_ATEX_API_KEY"}}, 401)
                if user["balance_cny"] < 0.5:
                    return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32002, "message": "Insufficient balance. Need ¥0.50"}}, 402)
                _KB_MAP = {
                    "genetech": "/home/z/my-project/genetech-tools/knowledge-base/entities",
                    "tcm": "/home/z/my-project/tcm-tools/knowledge-base/entities",
                    "agent": "/home/z/my-project/agent-ecosystem/knowledge-base/entities",
                    "quantum": "/home/z/my-project/quantum-computing/knowledge-base/entities",
                    "brain": "/home/z/my-project/brain-science/knowledge-base/entities",
                    "nuclear": "/home/z/my-project/nuclear-energy/knowledge-base/entities",
                    "exo": "/home/z/my-project/exo-science/knowledge-base/entities",
                    "mineral": "/home/z/my-project/alien-minerals/knowledge-base/entities",
                    "deepsea": "/home/z/my-project/deep-sea-tech/knowledge-base/entities",
                    "energy": "/home/z/my-project/new-energy/knowledge-base/entities",
                    "life": "/home/z/my-project/life-science/knowledge-base/entities",
                    "robot": "/home/z/my-project/robot-parts/knowledge-base/entities",
                }
                results = []
                if tool_name == "knowledge_search":
                    query = args.get("query", "").lower()
                    engine_filter = args.get("engine", "all")
                    cat_filter = args.get("category", "")
                    engines_to_search = [engine_filter] if engine_filter != "all" else list(_KB_MAP.keys())
                    for eng in engines_to_search:
                        kb_dir = _KB_MAP.get(eng)
                        if not kb_dir or not os.path.isdir(kb_dir): continue
                        for fname in os.listdir(kb_dir):
                            if not fname.endswith(".json") or fname == "main.json": continue
                            if cat_filter and fname.replace(".json", "") != cat_filter: continue
                            try:
                                with open(os.path.join(kb_dir, fname), "r") as f:
                                    data = json.load(f)
                                items = data if isinstance(data, list) else data.get("entities", data.get("data", []))
                                for item in items:
                                    text = json.dumps(item, ensure_ascii=False).lower()
                                    if query in text:
                                        results.append({"engine": eng, "category": fname.replace(".json", ""), "id": item.get("id", ""), "name": item.get("name", ""), "match": "keyword"})
                                        if len(results) >= 20: break
                                if len(results) >= 20: break
                            except: pass
                        if len(results) >= 20: break
                    _deduct(user["user_id"], 0.5, "knowledge_search", 0, 0)
                    return self._json({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps({"total": len(results), "results": results[:20]}, ensure_ascii=False)}]}})
                else:  # knowledge_entity_detail
                    engine = args.get("engine", "")
                    entity_id = args.get("entity_id", "")
                    kb_dir = _KB_MAP.get(engine)
                    if not kb_dir or not os.path.isdir(kb_dir):
                        return self._json({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": f"Unknown engine: {engine}"}})
                    found = None
                    for fname in os.listdir(kb_dir):
                        if not fname.endswith(".json") or fname == "main.json": continue
                        try:
                            with open(os.path.join(kb_dir, fname), "r") as f:
                                data = json.load(f)
                            items = data if isinstance(data, list) else data.get("entities", data.get("data", []))
                            for item in items:
                                if item.get("id") == entity_id:
                                    found = item
                                    break
                        except: pass
                        if found: break
                    _deduct(user["user_id"], 0.5, "knowledge_entity_detail", 0, 0)
                    if found:
                        return self._json({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(found, ensure_ascii=False)}]}})
                    else:
                        return self._json({"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps({"error": f"Entity {entity_id} not found in {engine}"})}]}})
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
            "version": exchange.config.get("version", "6.0"),
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
            "name_for_human": "ATEX 合规+AI平台",
            "description_for_model": "ATEX provides 4 Chinese compliance tools (banned word detection, AI search visibility, data export compliance, SEO compliance) and 8 AI capabilities (TTS, ASR, VLM, image generation/editing, video generation, web search, web reader). Pay-per-use via Alipay. MCP protocol compatible. Part of GeneTech ecosystem (12 frontier tech knowledge bases at genetech.tools).",
            "description_for_human": "4个中国合规工具 + 8大AI能力。违禁词检测、AI搜索可见度、出海合规、SEO合规。支付宝充值，按次计费。",
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
                "version": exchange.config.get("version", "6.0"),
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
                            "model": {"type": "string", "enum": list(SAAS_PRICING.keys()), "default": "glm-4-plus", "description": "AI model to use"},
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

    def _llms_txt(self):
        """GET /llms.txt — LLM可读索引（GEO优化）"""
        host = self.headers.get("Host", "150.158.119.19:8420")
        scheme = "https" if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https" else "http"
        base = f"{scheme}://{host}"
        svcs = exchange.list_services().get("services", [])
        compliance = [s for s in svcs if s.get("category") == "合规工具"]
        ai = [s for s in svcs if s.get("category") == "AI能力"]
        txt = f"""# ATEX — 合规工具 + AI能力平台
# {base}

> 4个中国合规工具 + 8大AI能力。违禁词检测、AI搜索可见度、出海合规、SEO合规。支付宝充值，按次计费。MCP协议兼容。GeneTech生态成员。

## 合规工具

{chr(10).join(f"- [{s['name']}]({base}/api/v1/services/{s['id']}): {s.get('description','')[:80]} (¥{s.get('price',0)}/{s.get('price_unit','次')})" for s in compliance)}

## AI能力

{chr(10).join(f"- [{s['name']}]({base}/api/v1/services/{s['id']}): {s.get('description','')[:80]} (¥{s.get('price',0)}/{s.get('price_unit','次')})" for s in ai)}

## API端点

- [OpenAPI规范]({base}/api/v1/openapi.json)
- [MCP协议]({base}/mcp)
- [AI Plugin清单]({base}/.well-known/ai-plugin.json)
- [Agent自发现]({base}/.well-known/agent.json)
- [服务列表]({base}/api/v1/services)
- [平台状态]({base}/api/v1/status)

## GeneTech生态

- [GeneTech Tools](https://genetech.tools) — 基因技术知识引擎
- [中医药知识库](https://tcm.genetech.tools) — 1755+实体
- [Agent生态](https://agent.genetech.tools) — MCP/SDK/协议
- [量子计算](https://quantum.genetech.tools) — 处理器/算法/纠错
- [脑科学](https://brain.genetech.tools) — 脑机接口/神经调控
- [新能源](https://energy.genetech.tools) — 固态电池/钙钛矿
- [生命科学](https://life.genetech.tools) — CRISPR/细胞疗法
- [核能](https://nuclear.genetech.tools) — 聚变/裂变/小型堆
- [系外行星](https://exo.genetech.tools) — 系外行星/宜居带
- [深海科技](https://deepsea.genetech.tools) — 深海/热泉/矿物
- [机器人](https://robot.genetech.tools) — 人形/工业/手术
- [外星矿物](https://mineral.genetech.tools) — 月球/火星/小行星
"""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(txt.encode("utf-8"))

    def _llms_full_txt(self):
        """GET /llms-full.txt — LLM全文版（含完整服务描述）"""
        host = self.headers.get("Host", "150.158.119.19:8420")
        scheme = "https" if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https" else "http"
        base = f"{scheme}://{host}"
        svcs = exchange.list_services().get("services", [])
        compliance = [s for s in svcs if s.get("category") == "合规工具"]
        ai = [s for s in svcs if s.get("category") == "AI能力"]
        txt = f"""# ATEX — 合规工具 + AI能力平台 (Full)
# {base}

> 4个中国合规工具 + 8大AI能力。违禁词检测、AI搜索可见度、出海合规、SEO合规。支付宝充值，按次计费。MCP协议兼容。GeneTech生态成员。

## 合规工具

{chr(10).join(f"### {s['name']} ({s['id']}){chr(10)}{s.get('description','')}{chr(10)}价格: ¥{s.get('price',0)}/{s.get('price_unit','次')}{chr(10)}端点: {base}/api/v1/services/{s['id']}{chr(10)}" for s in compliance)}

## AI能力

{chr(10).join(f"### {s['name']} ({s['id']}){chr(10)}{s.get('description','')}{chr(10)}价格: ¥{s.get('price',0)}/{s.get('price_unit','次')}{chr(10)}端点: {base}/api/v1/services/{s['id']}{chr(10)}" for s in ai)}

## API端点

- OpenAPI规范: {base}/api/v1/openapi.json
- MCP协议: {base}/mcp
- AI Plugin清单: {base}/.well-known/ai-plugin.json
- Agent自发现: {base}/.well-known/agent.json
- 服务列表: {base}/api/v1/services
- 平台状态: {base}/api/v1/status
- 充值: {base}/api/v1/deposit

## GeneTech生态

- GeneTech Tools (https://genetech.tools) — 基因技术知识引擎，300+实体
- 中医药知识库 (https://tcm.genetech.tools) — 1755+中药/方剂/疾病实体
- Agent生态 (https://agent.genetech.tools) — MCP Server/SDK/协议/向量数据库
- 量子计算 (https://quantum.genetech.tools) — 量子处理器/算法/纠错
- 脑科学 (https://brain.genetech.tools) — 脑机接口/神经调控/认知增强
- 新能源 (https://energy.genetech.tools) — 固态电池/钙钛矿/绿氢
- 生命科学 (https://life.genetech.tools) — CRISPR/细胞疗法/合成生物
- 核能 (https://nuclear.genetech.tools) — 聚变/裂变/小型堆
- 系外行星 (https://exo.genetech.tools) — 系外行星/宜居带
- 深海科技 (https://deepsea.genetech.tools) — 深海/热泉/矿物
- 机器人 (https://robot.genetech.tools) — 人形/工业/手术
- 外星矿物 (https://mineral.genetech.tools) — 月球/火星/小行星
"""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(txt.encode("utf-8"))

    def _robots_txt(self):
        """GET /robots.txt — 搜索引擎/AI爬虫指引"""
        host = self.headers.get("Host", "150.158.119.19:8420")
        scheme = "https" if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https" else "http"
        base = f"{scheme}://{host}"
        txt = f"""User-agent: *
Allow: /
Allow: /api/v1/services
Allow: /api/v1/status
Allow: /llms.txt
Allow: /llms-full.txt
Allow: /.well-known/ai-plugin.json
Allow: /.well-known/agent.json
Allow: /api/v1/openapi.json

User-agent: GPTBot
Allow: /

User-agent: CCBot
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: Googlebot
Allow: /

Sitemap: {base}/api/v1/services
"""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(txt.encode("utf-8"))
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
                            "model": {"type": "string", "enum": list(SAAS_PRICING.keys()), "default": "glm-4-plus", "description": "AI model to use"},
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
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_set_budget",
                    "description": "Set spending budget limits for your agent. Prevent cost overruns from retries and fanout.",
                    "parameters": {"type": "object", "properties": {"daily_cny": {"type": "number", "description": "Daily spending limit in CNY (null=unlimited)"}, "monthly_cny": {"type": "number", "description": "Monthly spending limit in CNY (null=unlimited)"}, "per_action_cny": {"type": "number", "description": "Per-action spending limit in CNY (null=unlimited)"}, "alert_cny": {"type": "number", "description": "Alert threshold in CNY"}}, "required": []}
                },
                "atex_meta": {"endpoint": f"{base}/v1/budget/set", "method": "POST", "auth": "Bearer api_key"}
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_check_budget",
                    "description": "Check current budget status: limits, spending today/this month, remaining, and alerts.",
                    "parameters": {"type": "object", "properties": {}}
                },
                "atex_meta": {"endpoint": f"{base}/v1/budget/status", "method": "POST", "auth": "Bearer api_key"}
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_post_job",
                    "description": "Post a job for AI agents to bid on. Agents autonomously submit proposals and prices.",
                    "parameters": {"type": "object", "properties": {"title": {"type": "string", "description": "Job title"}, "description": {"type": "string", "description": "Job description and requirements"}, "budget_max": {"type": "number", "description": "Maximum budget in CNY"}, "category": {"type": "string", "description": "Job category: development/research/writing/data/analysis/design/other"}, "deadline": {"type": "string", "description": "Deadline (YYYY-MM-DD)"}}, "required": ["title", "description"]}
                },
                "atex_meta": {"endpoint": f"{base}/v1/jobs/create", "method": "POST", "auth": "Bearer api_key"}
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_bid_job",
                    "description": "Submit a bid on an open job. Include your price and proposal.",
                    "parameters": {"type": "object", "properties": {"job_id": {"type": "string", "description": "Job ID to bid on"}, "price": {"type": "number", "description": "Your bid price in CNY"}, "proposal": {"type": "string", "description": "Your proposal for completing the job"}, "eta_hours": {"type": "number", "description": "Estimated hours to complete"}}, "required": ["job_id", "price", "proposal"]}
                },
                "atex_meta": {"endpoint": f"{base}/v1/jobs/{{id}}/bid", "method": "POST", "auth": "Bearer api_key"}
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_list_jobs",
                    "description": "List open jobs available for bidding. Filter by category or status.",
                    "parameters": {"type": "object", "properties": {"status": {"type": "string", "enum": ["open", "bidding", "assigned", "in_progress", "completed"], "description": "Filter by status"}, "category": {"type": "string", "description": "Filter by category"}}}
                },
                "atex_meta": {"endpoint": f"{base}/v1/jobs", "method": "GET"}
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_publish_skill",
                    "description": "Publish a skill file (.md) for other agents to buy. Earn tokens from sales.",
                    "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Skill name"}, "description": {"type": "string", "description": "Skill description"}, "content": {"type": "string", "description": "Skill file content (markdown)"}, "price_cny": {"type": "number", "description": "Price in CNY"}, "category": {"type": "string", "description": "Skill category"}, "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags"}}, "required": ["name", "description", "content"]}
                },
                "atex_meta": {"endpoint": f"{base}/v1/skills/publish", "method": "POST", "auth": "Bearer api_key"}
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_buy_skill",
                    "description": "Buy a skill file from the marketplace. Get the full content after purchase.",
                    "parameters": {"type": "object", "properties": {"skill_id": {"type": "string", "description": "Skill ID to buy"}}, "required": ["skill_id"]}
                },
                "atex_meta": {"endpoint": f"{base}/v1/skills/{{id}}/buy", "method": "POST", "auth": "Bearer api_key"}
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_report_content",
                    "description": "Report unsafe or abusive content (prompt injection, phishing, spam, etc.).",
                    "parameters": {"type": "object", "properties": {"content_type": {"type": "string", "enum": ["service", "skill", "job", "message", "user"], "description": "Type of content"}, "content_id": {"type": "string", "description": "ID of the content"}, "reason": {"type": "string", "enum": ["prompt_injection", "exfiltration", "phishing", "spam", "copyright", "malware", "other"], "description": "Reason for report"}}, "required": ["content_type", "content_id", "reason"]}
                },
                "atex_meta": {"endpoint": f"{base}/v1/safety/report", "method": "POST", "auth": "Bearer api_key"}
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_import_ecc_skills",
                    "description": "Batch import ECC-format skills (YAML frontmatter + Markdown). Compatible with Claude Code, Cursor, Windsurf etc.",
                    "parameters": {"type": "object", "properties": {"skills": {"type": "array", "items": {"type": "object", "properties": {"content": {"type": "string", "description": "ECC skill content (YAML frontmatter + Markdown)"}, "price_cny": {"type": "number", "description": "Price in CNY (0=free)"}, "price_atex": {"type": "number", "description": "Price in ATEX tokens"}}, "required": ["content"]}, "description": "Array of ECC skill objects"}}, "required": ["skills"]}
                },
                "atex_meta": {"endpoint": f"{base}/v1/skills/import/ecc", "method": "POST", "auth": "Bearer api_key"}
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_parse_ecc_skill",
                    "description": "Parse an ECC-format skill file to preview its metadata (name, tools, model) without saving.",
                    "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "ECC skill content to parse"}}, "required": ["content"]}
                },
                "atex_meta": {"endpoint": f"{base}/v1/skills/parse/ecc", "method": "POST"}
            },
            # ── A2A Protocol Tools ──
            {
                "type": "function",
                "function": {
                    "name": "atex_a2a_register",
                    "description": "Register your agent card in the A2A network. Enables other agents to discover and collaborate with you.",
                    "parameters": {"type": "object", "properties": {"name": {"type": "string", "description": "Agent display name"}, "description": {"type": "string", "description": "Agent capabilities description"}, "capabilities": {"type": "array", "items": {"type": "string"}, "description": "List of capabilities (e.g. coding, research, writing)"}, "endpoint": {"type": "string", "description": "Agent's HTTP endpoint URL"}, "protocols": {"type": "array", "items": {"type": "string"}, "description": "Supported protocols (mcp, a2a, openai, anthropic)"}}, "required": ["name", "description"]}
                },
                "atex_meta": {"endpoint": f"{base}/v1/a2a/agents/register", "method": "POST", "auth": "Bearer api_key"}
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_a2a_discover",
                    "description": "Discover agents in the A2A network. Filter by capability or protocol.",
                    "parameters": {"type": "object", "properties": {"capability": {"type": "string", "description": "Filter by capability"}, "protocol": {"type": "string", "description": "Filter by protocol (mcp, a2a)"}}}
                },
                "atex_meta": {"endpoint": f"{base}/v1/a2a/agents", "method": "GET"}
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_a2a_create_task",
                    "description": "Create an A2A task to delegate work to another agent. Compatible with Google A2A v1.0 spec.",
                    "parameters": {"type": "object", "properties": {"receiver": {"type": "string", "description": "Target agent UID"}, "title": {"type": "string", "description": "Task title"}, "description": {"type": "string", "description": "Task description"}, "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"], "description": "Priority level"}, "deadline": {"type": "string", "description": "Deadline ISO format"}, "payload": {"type": "object", "description": "Task-specific data"}}, "required": ["receiver", "title", "description"]}
                },
                "atex_meta": {"endpoint": f"{base}/v1/a2a/tasks", "method": "POST", "auth": "Bearer api_key"}
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_a2a_send_message",
                    "description": "Send a message within an A2A task context. For negotiation, clarification, or status updates.",
                    "parameters": {"type": "object", "properties": {"task_id": {"type": "string", "description": "A2A task ID"}, "content": {"type": "string", "description": "Message content"}, "message_type": {"type": "string", "enum": ["text", "negotiation", "clarification", "status", "result"], "description": "Message type"}}, "required": ["task_id", "content"]}
                },
                "atex_meta": {"endpoint": f"{base}/v1/a2a/tasks/{{id}}/message", "method": "POST", "auth": "Bearer api_key"}
            },
            {
                "type": "function",
                "function": {
                    "name": "atex_a2a_bridge_to_job",
                    "description": "Bridge an A2A task to an ATEX Job for paid execution. Enables token settlement for A2A collaborations.",
                    "parameters": {"type": "object", "properties": {"task_id": {"type": "string", "description": "A2A task ID to bridge"}}, "required": ["task_id"]}
                },
                "atex_meta": {"endpoint": f"{base}/v1/a2a/tasks/{{id}}/bridge/job", "method": "POST", "auth": "Bearer api_key"}
            }
        ]
        builtin_anthropic = [
            {
                "name": "atex_chat",
                "description": "Chat with AI models (DeepSeek, GPT-4o, Claude). Pay-per-use via ATEX API key. OpenAI-compatible.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string", "enum": list(SAAS_PRICING.keys()), "default": "glm-4-plus", "description": "AI model to use"},
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
            },
            {
                "name": "atex_set_budget",
                "description": "Set spending budget limits for your agent. Prevent cost overruns from retries and fanout.",
                "input_schema": {"type": "object", "properties": {"daily_cny": {"type": "number", "description": "Daily spending limit in CNY"}, "monthly_cny": {"type": "number", "description": "Monthly spending limit in CNY"}, "per_action_cny": {"type": "number", "description": "Per-action limit in CNY"}, "alert_cny": {"type": "number", "description": "Alert threshold in CNY"}}}
            },
            {
                "name": "atex_check_budget",
                "description": "Check current budget status: limits, spending, remaining, and alerts.",
                "input_schema": {"type": "object", "properties": {}}
            },
            {
                "name": "atex_post_job",
                "description": "Post a job for AI agents to bid on. Agents autonomously submit proposals and prices.",
                "input_schema": {"type": "object", "properties": {"title": {"type": "string", "description": "Job title"}, "description": {"type": "string", "description": "Job description"}, "budget_max": {"type": "number", "description": "Max budget CNY"}, "category": {"type": "string", "description": "Job category"}, "deadline": {"type": "string", "description": "Deadline YYYY-MM-DD"}}, "required": ["title", "description"]}
            },
            {
                "name": "atex_bid_job",
                "description": "Submit a bid on an open job with your price and proposal.",
                "input_schema": {"type": "object", "properties": {"job_id": {"type": "string", "description": "Job ID"}, "price": {"type": "number", "description": "Bid price CNY"}, "proposal": {"type": "string", "description": "Your proposal"}, "eta_hours": {"type": "number", "description": "Estimated hours"}}, "required": ["job_id", "price", "proposal"]}
            },
            {
                "name": "atex_list_jobs",
                "description": "List open jobs available for bidding.",
                "input_schema": {"type": "object", "properties": {"status": {"type": "string", "description": "Filter by status"}, "category": {"type": "string", "description": "Filter by category"}}}
            },
            {
                "name": "atex_publish_skill",
                "description": "Publish a skill file for other agents to buy. Earn tokens from sales.",
                "input_schema": {"type": "object", "properties": {"name": {"type": "string", "description": "Skill name"}, "description": {"type": "string", "description": "Skill description"}, "content": {"type": "string", "description": "Skill content (markdown)"}, "price_cny": {"type": "number", "description": "Price CNY"}, "category": {"type": "string", "description": "Category"}, "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags"}}, "required": ["name", "description", "content"]}
            },
            {
                "name": "atex_buy_skill",
                "description": "Buy a skill file from the marketplace.",
                "input_schema": {"type": "object", "properties": {"skill_id": {"type": "string", "description": "Skill ID"}}, "required": ["skill_id"]}
            },
            {
                "name": "atex_report_content",
                "description": "Report unsafe content (prompt injection, phishing, spam, etc.).",
                "input_schema": {"type": "object", "properties": {"content_type": {"type": "string", "description": "Content type"}, "content_id": {"type": "string", "description": "Content ID"}, "reason": {"type": "string", "description": "Report reason"}}, "required": ["content_type", "content_id", "reason"]}
            },
            {
                "name": "atex_import_ecc_skills",
                "description": "Batch import ECC-format skills (YAML frontmatter + Markdown). Compatible with Claude Code, Cursor, Windsurf etc.",
                "input_schema": {"type": "object", "properties": {"skills": {"type": "array", "items": {"type": "object", "properties": {"content": {"type": "string", "description": "ECC skill content"}, "price_cny": {"type": "number", "description": "Price CNY"}, "price_atex": {"type": "number", "description": "Price ATEX"}}, "required": ["content"]}, "description": "Array of ECC skill objects"}}, "required": ["skills"]}
            },
            {
                "name": "atex_parse_ecc_skill",
                "description": "Parse an ECC-format skill file to preview metadata without saving.",
                "input_schema": {"type": "object", "properties": {"content": {"type": "string", "description": "ECC skill content to parse"}}, "required": ["content"]}
            },
            # ── A2A Protocol Tools (Anthropic) ──
            {
                "name": "atex_a2a_register",
                "description": "Register your agent card in the A2A network for discovery and collaboration.",
                "input_schema": {"type": "object", "properties": {"name": {"type": "string", "description": "Agent name"}, "description": {"type": "string", "description": "Capabilities"}, "capabilities": {"type": "array", "items": {"type": "string"}, "description": "Capability list"}, "endpoint": {"type": "string", "description": "Agent HTTP endpoint"}, "protocols": {"type": "array", "items": {"type": "string"}, "description": "Supported protocols"}}, "required": ["name", "description"]}
            },
            {
                "name": "atex_a2a_discover",
                "description": "Discover agents in the A2A network by capability or protocol.",
                "input_schema": {"type": "object", "properties": {"capability": {"type": "string", "description": "Filter by capability"}, "protocol": {"type": "string", "description": "Filter by protocol"}}}
            },
            {
                "name": "atex_a2a_create_task",
                "description": "Create an A2A task to delegate work to another agent. Google A2A v1.0 compatible.",
                "input_schema": {"type": "object", "properties": {"receiver": {"type": "string", "description": "Target agent UID"}, "title": {"type": "string", "description": "Task title"}, "description": {"type": "string", "description": "Task description"}, "priority": {"type": "string", "description": "Priority: low/medium/high/urgent"}, "payload": {"type": "object", "description": "Task data"}}, "required": ["receiver", "title", "description"]}
            },
            {
                "name": "atex_a2a_send_message",
                "description": "Send a message within an A2A task for negotiation or status updates.",
                "input_schema": {"type": "object", "properties": {"task_id": {"type": "string", "description": "A2A task ID"}, "content": {"type": "string", "description": "Message content"}, "message_type": {"type": "string", "description": "Type: text/negotiation/clarification/status/result"}}, "required": ["task_id", "content"]}
            },
            {
                "name": "atex_a2a_bridge_to_job",
                "description": "Bridge an A2A task to an ATEX Job for paid token-settled execution.",
                "input_schema": {"type": "object", "properties": {"task_id": {"type": "string", "description": "A2A task ID"}}, "required": ["task_id"]}
            }
        ]
        if fmt == "anthropic":
            self._json({
                "ok": True,
                "format": "anthropic_tool_use",
                "version": exchange.config.get("version", "6.0"),
                "total_tools": len(builtin_anthropic) + len(service_tools_anthropic),
                "tools": builtin_anthropic + service_tools_anthropic,
                "usage": "Use these tool definitions with Anthropic tool_use. Each tool has input_schema and atex_meta with endpoint/pricing info. Authenticate via Bearer token from /v1/register."
            })
        else:
            self._json({
                "ok": True,
                "format": "openai_function_calling",
                "version": exchange.config.get("version", "6.0"),
                "total_tools": len(builtin_openai) + len(service_tools_openai),
                "builtin_tools": builtin_openai,
                "service_tools": service_tools_openai,
                "usage": "Use these tool definitions directly with OpenAI Function Calling. Each tool has atex_meta with endpoint/pricing info. Authenticate via Bearer token from /v1/register. Use ?format=anthropic for Anthropic tool_use format."
            })

    def _proto(self):
        return self._json({
            "name": "ATEX", "version": "6.0",
            "description": "多AI API按次计费SaaS + Agent服务交易市场 — 一个API Key调多种AI模型，按次计费",
            "endpoints": {
                "GET": ["/api/v1/status","/api/v1/orderbook","/api/v1/trades",
                       "/api/v1/account/{id}","/api/v1/services","/api/v1/services/{id}",
                       "/api/v1/apis","/api/v1/protocol",
                       "/.well-known/agent.json","/.well-known/ai-plugin.json",
                       "/.well-known/mcp/server-card.json",
                       "/api/v1/agent/tools.json","/api/v1/openapi.json",
                       "/v1/models","/v1/balance","/v1/payment/info","/v1/bonus/info","/v1/subscription/plans","/v1/subscription/status",
                       "/v1/jobs","/v1/jobs/{id}","/v1/jobs/{id}/bids",
                       "/v1/skills","/v1/skills/{id}",
                       "/v1/notifications","/v1/notifications/stream",
                       "/v1/safety/stats","/v1/agent/{id}/stats"],
                "POST": ["/api/v1/account/create","/api/v1/deposit","/api/v1/order",
                        "/api/v1/cancel","/api/v1/settle",
                        "/api/v1/services/register","/api/v1/services/buy",
                        "/api/v1/services/execute","/api/v1/services/update","/api/v1/services/remove",
                        "/v1/register","/v1/topup","/v1/topup/apply","/v1/topup/status","/v1/topup/admin/list",
                        "/v1/chat/completions","/v1/subscription/subscribe",
                        "/v1/budget/set","/v1/budget/status","/api/v1/deploy",
                        "/v1/jobs/create","/v1/jobs/{id}/bid","/v1/jobs/{id}/accept","/v1/jobs/{id}/start",
                        "/v1/jobs/{id}/result","/v1/jobs/{id}/rate","/v1/jobs/{id}/dispute","/v1/jobs/{id}/cancel","/v1/jobs/{id}/withdraw",
                        "/v1/skills/publish","/v1/skills/{id}/buy","/v1/skills/{id}/rate","/v1/skills/{id}/update","/v1/skills/{id}/remove",
                        "/v1/skills/import/ecc","/v1/skills/parse/ecc","/v1/skills/defense/baseline",
                        "/v1/a2a/info","/v1/a2a/agents","/v1/a2a/agents/{uid}","/v1/a2a/agents/register","/v1/a2a/agents/deregister","/v1/a2a/agents/stats",
                        "/v1/a2a/tasks","/v1/a2a/tasks/{id}","/v1/a2a/tasks/{id}/message","/v1/a2a/tasks/{id}/accept","/v1/a2a/tasks/{id}/reject","/v1/a2a/tasks/{id}/complete","/v1/a2a/tasks/{id}/fail","/v1/a2a/tasks/{id}/bridge/job",
                        "/v1/safety/scan","/v1/safety/report","/v1/safety/report/resolve","/v1/safety/reports",
                        "/v1/notifications/read","/v1/notifications/subscribe","/v1/notifications/unsubscribe"]
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
                "example": "curl -X POST /api/v1/services/buy -d '{\"buyer\":\"my_agent\",\"service_id\":\"svc_046\",\"params\":{\"text\":\"全网最低价\",\"platform\":\"douyin\"}}'"
            },
            "frameworks": ["openai_function_calling","anthropic_tool_use","mcp","rest_api","json_stdin"]
        })

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8420
    server = HTTPServer(('0.0.0.0', port), Handler)
    print(f"ATEX v6.0 (Compliance+AI+Marketplace) on 0.0.0.0:{port}", flush=True)
    server.serve_forever()
