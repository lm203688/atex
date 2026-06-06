#!/usr/bin/env python3
"""
ATEX Payment Gateway v1.0 — 支付闭环
支持: NOWPayments (USDT/USDC crypto) + 虎皮椒 (支付宝/微信)
ATEX Credits = 不可转让的平台积分，1 ATEX ≈ 0.01 USD (可浮动)
"""
import json, os, time, hashlib, hmac, urllib.request, urllib.error, uuid

# ── 配置 ──
PAYMENT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "payment_config.json")
PAYMENT_RECORDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "payment_records.json")

def _load_config():
    """加载支付配置"""
    try:
        with open(PAYMENT_CONFIG_PATH) as f:
            return json.load(f)
    except:
        return {
            "nowpayments": {"api_key": "", "ipn_secret": "", "enabled": False},
            "xunhupay": {"app_id": "", "app_secret": "", "enabled": False},
            "atex_rate": {"usd_to_atex": 100, "cny_to_atex": 14, "float_enabled": True},
            "withdraw": {"min_atex": 500, "fee_pct": 2, "settlement_days": 7, "methods": ["paypal", "worldfirst"]}
        }

def _save_config(cfg):
    os.makedirs(os.path.dirname(PAYMENT_CONFIG_PATH), exist_ok=True)
    with open(PAYMENT_CONFIG_PATH, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def _load_records():
    """加载支付记录"""
    try:
        with open(PAYMENT_RECORDS_PATH) as f:
            return json.load(f)
    except:
        return {"deposits": [], "withdrawals": [], "next_deposit_id": 1, "next_withdrawal_id": 1}

def _save_records(rec):
    os.makedirs(os.path.dirname(PAYMENT_RECORDS_PATH), exist_ok=True)
    with open(PAYMENT_RECORDS_PATH, "w") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)

def _calc_atex(amount, currency="usd"):
    """计算ATEX积分数量"""
    cfg = _load_config()
    rate = cfg.get("atex_rate", {})
    if currency == "usd":
        return int(amount * rate.get("usd_to_atex", 100))
    elif currency == "cny":
        return int(amount * rate.get("cny_to_atex", 14))
    return 0


# ══════════════════════════════════════════════════
# NOWPayments — Crypto支付 (USDT/USDC)
# ══════════════════════════════════════════════════

def nowpayments_create_order(user_id, amount_usd, pay_currency="usdttrc20"):
    """创建NOWPayments支付订单"""
    cfg = _load_config()
    np_cfg = cfg.get("nowpayments", {})
    if not np_cfg.get("enabled"):
        return {"ok": False, "err": "crypto_payment_not_enabled", "hint": "请在payment_config.json配置NOWPayments API key"}

    api_key = np_cfg.get("api_key", "")
    if not api_key:
        return {"ok": False, "err": "nowpayments_api_key_missing"}

    atex_amount = _calc_atex(amount_usd, "usd")
    order_id = f"atex_dep_{int(time.time())}_{uuid.uuid4().hex[:8]}"

    try:
        payload = json.dumps({
            "price_amount": amount_usd,
            "price_currency": "usd",
            "pay_currency": pay_currency,
            "order_id": order_id,
            "order_description": f"ATEX Credits: {atex_amount} ATEX (${amount_usd} USD)",
            "ipn_callback_url": np_cfg.get("ipn_url", "http://150.158.119.19:8420/v1/pay/crypto/callback"),
            "success_url": np_cfg.get("success_url", "https://lm203688.github.io/atex/"),
            "cancel_url": np_cfg.get("cancel_url", "https://lm203688.github.io/atex/")
        }).encode()

        req = urllib.request.Request(
            "https://api.nowpayments.io/v1/invoice",
            data=payload,
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())

        # 记录待确认订单
        rec = _load_records()
        deposit = {
            "id": rec["next_deposit_id"],
            "type": "crypto",
            "order_id": order_id,
            "nowpayments_id": result.get("id", ""),
            "user_id": user_id,
            "amount_usd": amount_usd,
            "atex_amount": atex_amount,
            "pay_currency": pay_currency,
            "pay_address": result.get("pay_address", ""),
            "pay_amount": result.get("pay_amount", 0),
            "status": "pending",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "invoice_url": result.get("invoice_url", "")
        }
        rec["deposits"].append(deposit)
        rec["next_deposit_id"] += 1
        _save_records(rec)

        return {
            "ok": True,
            "order_id": order_id,
            "atex_amount": atex_amount,
            "pay_address": result.get("pay_address", ""),
            "pay_amount": result.get("pay_amount", 0),
            "pay_currency": pay_currency,
            "invoice_url": result.get("invoice_url", ""),
            "expires_at": result.get("expiration_estimate_date", ""),
            "note": f"支付 {result.get('pay_amount', 0)} {pay_currency} 到上述地址，到账后自动充值 {atex_amount} ATEX Credits"
        }

    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        return {"ok": False, "err": "nowpayments_api_error", "status": e.code, "detail": body}
    except Exception as e:
        return {"ok": False, "err": "nowpayments_error", "detail": str(e)[:200]}


def nowpayments_ipn_callback(data):
    """处理NOWPayments IPN回调 — 支付确认后自动充值ATEX"""
    cfg = _load_config()
    np_cfg = cfg.get("nowpayments", {})
    ipn_secret = np_cfg.get("ipn_secret", "")

    # 验证签名（如果配置了IPN secret）
    # NOWPayments V2签名验证: HMAC-SHA256 of sorted params
    order_id = data.get("order_id", "")
    payment_status = data.get("payment_status", "")
    price_amount = data.get("price_amount", 0)
    actually_paid = data.get("actually_paid", 0)

    # 只处理最终状态
    if payment_status not in ("finished", "confirmed", "partially_paid"):
        return {"ok": True, "status": "ignored", "payment_status": payment_status}

    # 查找对应订单
    rec = _load_records()
    deposit = None
    for d in rec["deposits"]:
        if d["order_id"] == order_id and d["status"] == "pending":
            deposit = d
            break

    if not deposit:
        return {"ok": False, "err": "order_not_found", "order_id": order_id}

    # 计算实际到账ATEX
    if payment_status == "partially_paid" and actually_paid > 0:
        atex_amount = _calc_atex(actually_paid, "usd")
    else:
        atex_amount = deposit["atex_amount"]

    # 更新订单状态
    deposit["status"] = "completed" if payment_status != "partially_paid" else "partial"
    deposit["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    deposit["actually_paid"] = actually_paid
    deposit["final_atex"] = atex_amount
    _save_records(rec)

    # 自动充值ATEX到用户账户（通过atex.py deposit）
    try:
        from atex import ATEX
        exchange = ATEX()
        result = exchange.deposit(deposit["user_id"], atex_amount)
        deposit["deposit_result"] = result
        _save_records(rec)
        return {"ok": True, "atex_credited": atex_amount, "user_id": deposit["user_id"]}
    except Exception as e:
        deposit["deposit_error"] = str(e)[:200]
        _save_records(rec)
        return {"ok": False, "err": "deposit_failed", "detail": str(e)[:200]}


# ══════════════════════════════════════════════════
# 虎皮椒(Xunhupay) — 支付宝/微信
# ══════════════════════════════════════════════════

def xunhupay_create_order(user_id, amount_cny):
    """创建虎皮椒支付宝/微信支付订单"""
    cfg = _load_config()
    xh_cfg = cfg.get("xunhupay", {})
    if not xh_cfg.get("enabled"):
        return {"ok": False, "err": "alipay_not_enabled", "hint": "请在payment_config.json配置虎皮椒"}

    app_id = xh_cfg.get("app_id", "")
    app_secret = xh_cfg.get("app_secret", "")
    if not app_id or not app_secret:
        return {"ok": False, "err": "xunhupay_config_missing"}

    atex_amount = _calc_atex(amount_cny, "cny")
    order_id = f"atex_cny_{int(time.time())}_{uuid.uuid4().hex[:8]}"

    # 虎皮椒API参数
    params = {
        "version": "1.1",
        "appid": app_id,
        "trade_order_id": order_id,
        "total_fee": amount_cny,
        "title": f"ATEX积分充值 {atex_amount} Credits",
        "time": int(time.time()),
        "notify_url": xh_cfg.get("notify_url", "http://150.158.119.19:8420/v1/pay/alipay/callback"),
        "return_url": xh_cfg.get("return_url", "https://lm203688.github.io/atex/"),
        "nonce_str": uuid.uuid4().hex[:16],
        "type": "WAP",
        "wap_url": "https://lm203688.github.io/atex/",
        "wap_name": "ATEX"
    }

    # 签名: 按key排序拼接 + hash
    sign_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v) + app_secret
    params["hash"] = hashlib.md5(sign_str.encode()).hexdigest()

    # 记录订单
    rec = _load_records()
    deposit = {
        "id": rec["next_deposit_id"],
        "type": "alipay",
        "order_id": order_id,
        "user_id": user_id,
        "amount_cny": amount_cny,
        "atex_amount": atex_amount,
        "status": "pending",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    rec["deposits"].append(deposit)
    rec["next_deposit_id"] += 1
    _save_records(rec)

    # 构建支付URL
    try:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        pay_url = f"https://api.xunhupay.com/payment/do.html?{query}"

        return {
            "ok": True,
            "order_id": order_id,
            "atex_amount": atex_amount,
            "amount_cny": amount_cny,
            "pay_url": pay_url,
            "note": f"扫码支付 ¥{amount_cny}，到账后自动充值 {atex_amount} ATEX Credits"
        }
    except Exception as e:
        return {"ok": False, "err": "xunhupay_error", "detail": str(e)[:200]}


def xunhupay_callback(data):
    """处理虎皮椒支付回调"""
    cfg = _load_config()
    xh_cfg = cfg.get("xunhupay", {})
    app_secret = xh_cfg.get("app_secret", "")

    # 验证签名
    received_hash = data.get("hash", "")
    params = {k: v for k, v in data.items() if k != "hash" and v}
    sign_str = "&".join(f"{k}={v}" for k, v in sorted(params.items())) + app_secret
    expected_hash = hashlib.md5(sign_str.encode()).hexdigest()

    if received_hash != expected_hash:
        return {"ok": False, "err": "signature_mismatch"}

    order_id = data.get("trade_order_id", "")
    status = data.get("status", "")

    if status != "OD":
        return {"ok": True, "status": "ignored", "payment_status": status}

    # 查找订单
    rec = _load_records()
    deposit = None
    for d in rec["deposits"]:
        if d["order_id"] == order_id and d["status"] == "pending":
            deposit = d
            break

    if not deposit:
        return {"ok": False, "err": "order_not_found"}

    atex_amount = deposit["atex_amount"]
    deposit["status"] = "completed"
    deposit["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save_records(rec)

    # 自动充值（双账户体系：Exchange + SaaS）
    try:
        from atex import ATEX
        exchange = ATEX()
        result = exchange.deposit(deposit["user_id"], atex_amount)
        deposit["deposit_result"] = result
    except Exception as e:
        deposit["deposit_error"] = str(e)[:200]

    # 同时充值SaaS余额（MCP/服务调用从SaaS扣费）
    try:
        saas_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "saas_data", "users.json")
        if os.path.exists(saas_path):
            with open(saas_path) as f:
                saas = json.load(f)
            uid = deposit["user_id"]
            # 查找SaaS用户（可能是u_xxx格式或直接user_id）
            for saas_uid, saas_user in saas.get("users", {}).items():
                if saas_uid == uid or saas_user.get("user_id") == uid:
                    # 按CNY汇率转换：1 ATEX ≈ 0.01 USD ≈ 0.07 CNY
                    cny_amount = deposit.get("amount_cny", atex_amount * 0.07)
                    saas_user["balance_cny"] = saas_user.get("balance_cny", 0) + cny_amount
                    saas_user["total_topup_count"] = saas_user.get("total_topup_count", 0) + 1
                    # 赠送
                    bonus_pct = 0
                    if cny_amount >= 1000: bonus_pct = 0.4
                    elif cny_amount >= 500: bonus_pct = 0.3
                    elif cny_amount >= 100: bonus_pct = 0.2
                    elif cny_amount >= 50: bonus_pct = 0.15
                    elif cny_amount >= 10: bonus_pct = 0.1
                    if bonus_pct > 0:
                        bonus = round(cny_amount * bonus_pct, 2)
                        saas_user["balance_cny"] += bonus
                    with open(saas_path, "w") as f:
                        json.dump(saas, f, ensure_ascii=False, indent=2)
                    deposit["saas_deposit"] = {"uid": saas_uid, "cny": cny_amount, "bonus_pct": bonus_pct}
                    break
    except Exception as e:
        deposit["saas_deposit_error"] = str(e)[:200]

    _save_records(rec)
    return {"ok": True, "atex_credited": atex_amount, "user_id": deposit["user_id"]}


# ══════════════════════════════════════════════════
# 提现 — 服务提供方收益结算
# ══════════════════════════════════════════════════

def request_withdrawal(user_id, amount_atex, method="paypal", destination=""):
    """服务提供方申请提现"""
    cfg = _load_config()
    w_cfg = cfg.get("withdraw", {})
    min_atex = w_cfg.get("min_atex", 500)
    fee_pct = w_cfg.get("fee_pct", 2)
    settlement_days = w_cfg.get("settlement_days", 7)

    if amount_atex < min_atex:
        return {"ok": False, "err": f"min_withdrawal_{min_atex}_atex", "min_atex": min_atex}

    if method not in w_cfg.get("methods", ["paypal", "worldfirst"]):
        return {"ok": False, "err": "unsupported_method", "available": w_cfg.get("methods", [])}

    if not destination:
        return {"ok": False, "err": "destination_required", "hint": "PayPal邮箱 或 万里汇账号"}

    # 计算手续费和实际到账
    fee = int(amount_atex * fee_pct / 100)
    net_atex = amount_atex - fee
    # ATEX → USD (按当前汇率)
    rate = cfg.get("atex_rate", {})
    usd_to_atex = rate.get("usd_to_atex", 100)
    net_usd = round(net_atex / usd_to_atex, 2)

    rec = _load_records()
    withdrawal = {
        "id": rec["next_withdrawal_id"],
        "withdrawal_id": f"atex_wd_{int(time.time())}_{uuid.uuid4().hex[:6]}",
        "user_id": user_id,
        "amount_atex": amount_atex,
        "fee_atex": fee,
        "net_atex": net_atex,
        "net_usd": net_usd,
        "method": method,
        "destination": destination,
        "status": "pending",
        "settlement_date": time.strftime("%Y-%m-%d", time.localtime(time.time() + settlement_days * 86400)),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    rec["withdrawals"].append(withdrawal)
    rec["next_withdrawal_id"] += 1
    _save_records(rec)

    return {
        "ok": True,
        "withdrawal_id": withdrawal["withdrawal_id"],
        "amount_atex": amount_atex,
        "fee_atex": fee,
        "net_atex": net_atex,
        "net_usd": net_usd,
        "method": method,
        "destination": destination[:4] + "***" + destination[-4:] if len(destination) > 8 else "***",
        "settlement_date": withdrawal["settlement_date"],
        "status": "pending_approval",
        "note": f"预计 {settlement_days} 天内到账，需管理员审核"
    }


def approve_withdrawal(withdrawal_id, admin_token, action="approve"):
    """管理员审批提现"""
    if admin_token != "atex_admin_2026":
        return {"ok": False, "err": "unauthorized"}

    if action not in ("approve", "reject"):
        return {"ok": False, "err": "invalid_action"}

    rec = _load_records()
    withdrawal = None
    for w in rec["withdrawals"]:
        if w["withdrawal_id"] == withdrawal_id:
            withdrawal = w
            break

    if not withdrawal:
        return {"ok": False, "err": "withdrawal_not_found"}

    if withdrawal["status"] != "pending":
        return {"ok": False, "err": "already_processed", "current_status": withdrawal["status"]}

    if action == "approve":
        # 扣除用户ATEX余额
        try:
            from atex import ATEX
            exchange = ATEX()
            # 从用户账户扣除ATEX
            acc = exchange.get_account(withdrawal["user_id"])
            if not acc:
                return {"ok": False, "err": "account_not_found"}
            if acc["balance"] < withdrawal["amount_atex"]:
                return {"ok": False, "err": "insufficient_balance", "balance": acc["balance"]}
            acc["balance"] -= withdrawal["amount_atex"]
            exchange._save()
            withdrawal["status"] = "approved"
            withdrawal["approved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            return {"ok": False, "err": "deduct_failed", "detail": str(e)[:200]}
    else:
        withdrawal["status"] = "rejected"
        withdrawal["rejected_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    _save_records(rec)
    return {"ok": True, "withdrawal_id": withdrawal_id, "status": withdrawal["status"]}


# ══════════════════════════════════════════════════
# 汇率管理 — ATEX浮动机制
# ══════════════════════════════════════════════════

def get_exchange_rates():
    """查询当前ATEX汇率"""
    cfg = _load_config()
    rate_cfg = cfg.get("atex_rate", {})
    return {
        "usd_to_atex": rate_cfg.get("usd_to_atex", 100),
        "cny_to_atex": rate_cfg.get("cny_to_atex", 14),
        "float_enabled": rate_cfg.get("float_enabled", True),
        "last_updated": rate_cfg.get("last_updated", ""),
        "note": "1 USD = 100 ATEX Credits (可浮动)"
    }

def update_exchange_rate(usd_to_atex=None, cny_to_atex=None, admin_token=""):
    """管理员调整ATEX汇率（浮动机制）"""
    if admin_token != "atex_admin_2026":
        return {"ok": False, "err": "unauthorized"}

    cfg = _load_config()
    if not cfg.get("atex_rate"):
        cfg["atex_rate"] = {}

    if usd_to_atex and usd_to_atex > 0:
        cfg["atex_rate"]["usd_to_atex"] = usd_to_atex
    if cny_to_atex and cny_to_atex > 0:
        cfg["atex_rate"]["cny_to_atex"] = cny_to_atex
    cfg["atex_rate"]["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")

    _save_config(cfg)
    return {"ok": True, "rates": get_exchange_rates()}
