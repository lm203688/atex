#!/usr/bin/env python3
"""
ATEX v5.6 — Agent服务交易市场 + 通用API信用Token
Agent间Token结算 + 服务市场，统一平台。

两层功能:
  1. Token交易: 订单簿撮合（价格优先+时间优先）
  2. 服务市场: 固定价格服务买卖（直接Token转账 + 服务交付）

Token经济:
  - ATEX = 通用API信用Token，外部可自由交易
  - Agent花自己持有的ATEX消费服务和调API，不是从平台购买token
  - ATEX价格由市场供需决定（订单簿撮合，非固定汇率）
  - Agent间纯Token交易，无需法币
  - 平台纯撮合，从每笔交易收佣金，佣金结算给owner

Agent交互:
  echo '{"action":"..."}' | python3 atex.py
  POST https://150.158.119.19:8420/api/v1/...

安全: 输入校验 / 限流 / 自交易拦截 / 价格偏离熔断 / 日交易限额
"""

import json, os, sys, re, time, uuid, threading
from datetime import datetime, timezone, timedelta
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
TZ = timezone(timedelta(hours=8))

MAX_ACCOUNT_ID_LEN = 64
ACCOUNT_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_\-\.]+$')
MAX_ORDER_AMOUNT = 1000000
MAX_PRICE = 100000.0
MIN_PRICE = 0.01
MAX_INPUT_SIZE = 65536
MAX_SERVICE_NAME_LEN = 128
MAX_DESC_LEN = 2000


class RateLimiter:
    def __init__(self, max_requests=60, window_seconds=60):
        self.max_requests = max_requests
        self.window = window_seconds
        self.requests = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, key):
        now = time.time()
        with self._lock:
            self.requests[key] = [t for t in self.requests[key] if now - t < self.window]
            if len(self.requests[key]) >= self.max_requests:
                return False
            self.requests[key].append(now)
            return True

rate_limiter = RateLimiter(max_requests=60, window_seconds=60)


def load_json(path):
    with open(path) as f:
        return json.load(f)

def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def gen_id():
    return uuid.uuid4().hex[:12]

def validate_account_id(acc_id):
    if not acc_id or not isinstance(acc_id, str):
        return False, "account_id must be non-empty string"
    if len(acc_id) > MAX_ACCOUNT_ID_LEN:
        return False, f"account_id too long (max {MAX_ACCOUNT_ID_LEN})"
    if not ACCOUNT_ID_PATTERN.match(acc_id):
        return False, "account_id contains invalid characters"
    return True, None

def validate_price(price):
    if not isinstance(price, (int, float)):
        return False, "price must be number"
    if price < MIN_PRICE or price > MAX_PRICE:
        return False, f"price out of range [{MIN_PRICE}, {MAX_PRICE}]"
    return True, None

def validate_amount(amount):
    if not isinstance(amount, (int, float)):
        return False, "amount must be number"
    amount = int(amount)
    if amount < 1 or amount > MAX_ORDER_AMOUNT:
        return False, f"amount out of range [1, {MAX_ORDER_AMOUNT}]"
    return True, None

def safe_json_loads(raw):
    if isinstance(raw, str):
        if len(raw) > MAX_INPUT_SIZE:
            raise ValueError(f"input too large (max {MAX_INPUT_SIZE} bytes)")
        return json.loads(raw)
    elif isinstance(raw, dict):
        return raw
    else:
        raise ValueError("input must be JSON string or dict")


class ATEX:
    def __init__(self):
        self.config = load_json(f"{BASE}/config.json")
        self.accounts = load_json(f"{BASE}/accounts/accounts.json")
        self.ob = load_json(f"{BASE}/orders/orderbook.json")
        # Ensure orderbook has required fields
        for key, default in [("bids", []), ("asks", []), ("trades", []),
                             ("total_commission_earned", 0), ("last_price", None),
                             ("daily_volume", 0), ("daily_high", None), ("daily_low", None),
                             ("last_trade_time", "")]:
            if key not in self.ob:
                self.ob[key] = default
        self.svc = load_json(f"{BASE}/services/services.json")
        self.fob = load_json(f"{BASE}/orders/fiat_orderbook.json") if os.path.exists(f"{BASE}/orders/fiat_orderbook.json") else {"trades": []}
        self.commission_maker = self.config["commission_rate"]["maker"]
        self.commission_taker = self.config["commission_rate"]["taker"]
        self._lock = threading.Lock()
        self._check_daily_reset()

    def _check_daily_reset(self):
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        last_trade = self.ob.get("last_trade_time", "")
        if last_trade:
            last_day = last_trade[:10]
            if today != last_day:
                self.ob["daily_volume"] = 0
                self.ob["daily_high"] = None
                self.ob["daily_low"] = None
                for acc in self.accounts["accounts"].values():
                    acc["daily_traded"] = 0
                self._save()
                self._log(f"daily_reset:{last_day}->{today}")

    def _save(self):
        save_json(f"{BASE}/accounts/accounts.json", self.accounts)
        save_json(f"{BASE}/orders/orderbook.json", self.ob)
        save_json(f"{BASE}/orders/fiat_orderbook.json", self.fob)
        save_json(f"{BASE}/services/services.json", self.svc)

    def _log(self, msg):
        log_path = f"{BASE}/logs/{datetime.now(TZ).strftime('%Y-%m-%d')}.jsonl"
        entry = {"ts": now_str(), "msg": msg}
        with open(log_path, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _check_risk(self, acc_id, side, price, amount):
        acc = self.accounts["accounts"].get(acc_id)
        if not acc:
            return True, None
        daily_limit = self.config["risk_control"].get("daily_trade_limit", 1000000)
        daily_traded = acc.get("daily_traded", 0)
        if daily_traded + amount > daily_limit:
            return False, f"daily_limit_exceeded:traded={daily_traded},limit={daily_limit}"
        max_single = self.config["risk_control"].get("max_single_order", 100000)
        if amount > max_single:
            return False, f"single_order_limit:{max_single}"
        last_price = self.ob.get("last_price")
        if last_price and price > 0:
            deviation = abs(price - last_price) / last_price
            max_deviation = self.config["risk_control"].get("max_price_deviation", 5.0)
            if deviation > max_deviation:
                return False, f"price_deviation:{deviation:.1%} exceeds {max_deviation:.1%}"
        return True, None

    # ── 账户 ──

    def get_account(self, acc_id):
        return self.accounts["accounts"].get(acc_id)

    def create_account(self, acc_id, role="trader"):
        valid, err = validate_account_id(acc_id)
        if not valid:
            return {"ok": False, "err": err}
        if acc_id in self.accounts["accounts"]:
            return {"ok": False, "err": "account_exists"}
        credit = self.config.get("token", {}).get("registration_credit", 0)
        # Provider Early Bird: 注册为provider额外奖励
        provider_bonus = 0
        if role == "provider":
            provider_bonus = self.config.get("provider_incentive", {}).get("register_bonus", 50)
        total_credit = credit + provider_bonus
        # 试用额度从platform账户扣除（非凭空铸造）
        if total_credit > 0:
            platform = self.get_account("platform")
            if platform and platform["balance"] >= total_credit:
                platform["balance"] -= total_credit
            else:
                total_credit = 0
        self.accounts["accounts"][acc_id] = {
            "balance": total_credit,
            "frozen": 0,
            "role": role,
            "created": now_str(),
            "daily_traded": 0,
        }
        self._save()
        msg = f"account_created:{acc_id},role:{role},credit:{credit}"
        if provider_bonus > 0:
            msg += f",provider_bonus:{provider_bonus}"
        self._log(msg)
        result = {"ok": True, "account": acc_id, "balance": total_credit, "note": "Registration credit is a trial bonus. ATEX is a freely tradable API credit token — acquire more via external trading or providing services."}
        if provider_bonus > 0:
            result["provider_bonus"] = provider_bonus
            result["note"] += f" Provider Early Bird bonus: {provider_bonus} ATEX!"
        return result

    def deposit(self, acc_id, amount):
        """存入Token：从platform账户转入（非凭空创造）"""
        acc = self.get_account(acc_id)
        if not acc:
            return {"ok": False, "err": "account_not_found"}
        if amount <= 0:
            return {"ok": False, "err": "amount_must_be_positive"}
        if amount > MAX_ORDER_AMOUNT:
            return {"ok": False, "err": f"max_deposit:{MAX_ORDER_AMOUNT}"}
        # 从platform账户扣除
        platform = self.get_account("platform")
        if not platform:
            return {"ok": False, "err": "platform_account_not_found"}
        if platform["balance"] < amount:
            return {"ok": False, "err": f"platform_insufficient_balance:available={platform['balance']}"}
        platform["balance"] -= amount
        acc["balance"] += amount
        self._save()
        self._log(f"deposit:{acc_id},amount:{amount},from:platform,new_balance:{acc['balance']}")
        return {"ok": True, "balance": acc["balance"], "source": "platform", "platform_remaining": platform["balance"]}

    # ── Token交易（订单簿撮合）──

    def place_order(self, acc_id, side, price, amount):
        valid, err = validate_account_id(acc_id)
        if not valid:
            return {"ok": False, "err": err}
        valid, err = validate_price(price)
        if not valid:
            return {"ok": False, "err": err}
        valid, err = validate_amount(amount)
        if not valid:
            return {"ok": False, "err": err}
        if side not in ("buy", "sell"):
            return {"ok": False, "err": "side must be buy or sell"}
        acc = self.get_account(acc_id)
        if not acc:
            return {"ok": False, "err": "account_not_found"}

        price = round(price, 2)
        amount = int(amount)

        if amount < self.config["trading_rules"]["min_order_size"]:
            return {"ok": False, "err": f"min_order_size:{self.config['trading_rules']['min_order_size']}"}

        risk_ok, risk_err = self._check_risk(acc_id, side, price, amount)
        if not risk_ok:
            self._log(f"risk_blocked:{acc_id},{risk_err}")
            return {"ok": False, "err": risk_err}

        if side == "buy":
            cost = price * amount
            available = acc["balance"] - acc["frozen"]
            if available < cost:
                return {"ok": False, "err": f"insufficient_balance:need={cost},available={available}"}
            acc["frozen"] += cost
        else:
            if acc["balance"] - acc["frozen"] < amount:
                return {"ok": False, "err": "insufficient_tokens"}
            acc["frozen"] += amount

        order = {
            "id": gen_id(), "account": acc_id, "side": side,
            "price": price, "amount": amount, "filled": 0,
            "status": "open", "created": now_str()
        }
        book_side = "bids" if side == "buy" else "asks"
        self.ob[book_side].append(order)
        self._save()
        self._log(f"order_placed:{order['id']},{side},{price}x{amount},{acc_id}")

        with self._lock:
            trades = self._match()
        return {"ok": True, "order": order, "trades": trades}

    def _match(self):
        trades = []
        self.ob["bids"].sort(key=lambda o: (-o["price"], o["created"]))
        self.ob["asks"].sort(key=lambda o: (o["price"], o["created"]))
        new_bids, new_asks = [], []

        for bid in self.ob["bids"]:
            if bid["status"] not in ("open", "partial") or bid["filled"] >= bid["amount"]:
                continue
            for ask in self.ob["asks"]:
                if ask["status"] not in ("open", "partial") or ask["filled"] >= ask["amount"]:
                    continue
                if bid["price"] < ask["price"]:
                    break
                if bid["account"] == ask["account"]:
                    self._log(f"self_trade_blocked:{bid['account']}")
                    continue

                trade_amount = min(bid["amount"] - bid["filled"], ask["amount"] - ask["filled"])
                trade_price = ask["price"]
                commission = self._calc_commission(trade_price * trade_amount, "taker")
                maker_commission = self._calc_commission(trade_price * trade_amount, "maker")

                buyer = self.accounts["accounts"][bid["account"]]
                cost = trade_price * trade_amount
                buyer["frozen"] -= cost
                buyer["balance"] -= cost
                buyer["balance"] -= commission
                buyer["balance"] += trade_amount
                buyer["daily_traded"] = buyer.get("daily_traded", 0) + trade_amount
                bid["filled"] += trade_amount
                bid["status"] = "filled" if bid["filled"] >= bid["amount"] else "partial"

                seller = self.accounts["accounts"][ask["account"]]
                seller["frozen"] -= trade_amount
                seller["balance"] -= trade_amount
                seller["balance"] += cost
                seller["balance"] -= maker_commission
                seller["daily_traded"] = seller.get("daily_traded", 0) + trade_amount
                ask["filled"] += trade_amount
                ask["status"] = "filled" if ask["filled"] >= ask["amount"] else "partial"

                self.ob["total_commission_earned"] += commission + maker_commission
                trade = {
                    "id": gen_id(), "bid_id": bid["id"], "ask_id": ask["id"],
                    "buyer": bid["account"], "seller": ask["account"],
                    "price": trade_price, "amount": trade_amount,
                    "commission_taker": commission, "commission_maker": maker_commission,
                    "time": now_str()
                }
                trades.append(trade)
                self.ob["trades"].append(trade)
                self.ob["last_price"] = trade_price
                self.ob["last_trade_time"] = trade["time"]
                self.ob["daily_volume"] += trade_amount
                if self.ob["daily_high"] is None or trade_price > self.ob["daily_high"]:
                    self.ob["daily_high"] = trade_price
                if self.ob["daily_low"] is None or trade_price < self.ob["daily_low"]:
                    self.ob["daily_low"] = trade_price
                self._log(f"trade:{trade['id']},{bid['account']}->ASK<-{ask['account']},{trade_price}x{trade_amount}")
                if bid["filled"] >= bid["amount"]:
                    break
            if bid["status"] in ("open", "partial"):
                new_bids.append(bid)
        for ask in self.ob["asks"]:
            if ask["status"] in ("open", "partial"):
                new_asks.append(ask)
        self.ob["bids"] = new_bids
        self.ob["asks"] = new_asks
        self._save()
        return trades

    def _calc_commission(self, notional, role):
        tiers = self.config["commission_rate"].get("tiers", [])
        if not tiers:
            rate = self.commission_maker if role == "maker" else self.commission_taker
            return round(notional * rate, 2)
        monthly_est = self.ob.get("daily_volume", 0) * 30
        applicable_rate = self.commission_taker if role == "taker" else self.commission_maker
        for tier in sorted(tiers, key=lambda t: t["min_volume"]):
            if monthly_est >= tier["min_volume"]:
                applicable_rate = tier.get(f"{role}_rate", applicable_rate)
        return round(notional * applicable_rate, 2)

    def cancel_order(self, acc_id, order_id):
        valid, err = validate_account_id(acc_id)
        if not valid:
            return {"ok": False, "err": err}
        for book_side in ["bids", "asks"]:
            for order in self.ob[book_side]:
                if order["id"] == order_id and order["account"] == acc_id:
                    order["status"] = "cancelled"
                    acc = self.accounts["accounts"][acc_id]
                    if order["side"] == "buy":
                        unfreeze = order["price"] * (order["amount"] - order["filled"])
                        acc["frozen"] -= unfreeze
                    else:
                        acc["frozen"] -= (order["amount"] - order["filled"])
                    self._save()
                    self._log(f"order_cancelled:{order_id},{acc_id}")
                    return {"ok": True, "order": order}
        return {"ok": False, "err": "order_not_found"}

    def query_orderbook(self):
        bids = sorted(self.ob["bids"], key=lambda o: (-o["price"], o["created"]))[:20]
        asks = sorted(self.ob["asks"], key=lambda o: (o["price"], o["created"]))[:20]
        return {
            "last_price": self.ob["last_price"],
            "daily_volume": self.ob["daily_volume"],
            "daily_high": self.ob["daily_high"],
            "daily_low": self.ob["daily_low"],
            "total_commission": self.ob["total_commission_earned"],
            "open_orders": len(self.ob["bids"]) + len(self.ob["asks"]),
            "bids": [{"price": b["price"], "amount": b["amount"]-b["filled"], "orders": 1} for b in bids],
            "asks": [{"price": a["price"], "amount": a["amount"]-a["filled"], "orders": 1} for a in asks]
        }

    def trade_history(self, limit=20):
        limit = min(int(limit), 100)
        return self.ob["trades"][-limit:]

    # ── 服务市场 ──

    def register_service(self, provider_id, name, description, price, unit, category, service_type="llm"):
        """Agent注册服务到市场"""
        valid, err = validate_account_id(provider_id)
        if not valid:
            return {"ok": False, "err": err}
        acc = self.get_account(provider_id)
        if not acc:
            return {"ok": False, "err": "account_not_found"}
        if not name or len(name) > MAX_SERVICE_NAME_LEN:
            return {"ok": False, "err": f"name required, max {MAX_SERVICE_NAME_LEN} chars"}
        if not description or len(description) > MAX_DESC_LEN:
            return {"ok": False, "err": f"description required, max {MAX_DESC_LEN} chars"}
        valid, err = validate_price(price)
        if not valid:
            return {"ok": False, "err": f"price: {err}"}
        if not unit:
            return {"ok": False, "err": "unit required (e.g. 次/小时/月)"}

        svc_id = f"svc_{self.svc['next_service_id']:03d}"
        self.svc["next_service_id"] += 1
        service = {
            "id": svc_id, "name": name, "provider": provider_id,
            "description": description, "price": round(float(price), 2),
            "unit": unit, "category": category or "其他",
            "service_type": service_type,  # v5.16: llm | rule | hybrid
            "status": "active", "created": now_str(),
            "total_sold": 0, "total_revenue": 0
        }
        self.svc["services"].append(service)

        # Provider Early Bird: 首次上架奖励
        listing_bonus = 0
        existing = [s for s in self.svc["services"] if s["provider"] == provider_id and s["id"] != svc_id]
        if len(existing) == 0:  # 第一个服务
            listing_bonus = self.config.get("provider_incentive", {}).get("first_listing_bonus", 100)
            platform = self.get_account("platform")
            if platform and platform["balance"] >= listing_bonus:
                platform["balance"] -= listing_bonus
                acc["balance"] += listing_bonus
            else:
                listing_bonus = 0

        self._save()
        self._log(f"service_registered:{svc_id},{name},{provider_id},{price}/{unit},listing_bonus:{listing_bonus}")
        result = {"ok": True, "service": service}
        if listing_bonus > 0:
            result["listing_bonus"] = listing_bonus
            result["new_balance"] = acc["balance"]
            result["note"] = f"First listing bonus: {listing_bonus} ATEX!"
        return result

    def list_services(self, category=None, provider=None):
        """浏览服务市场"""
        result = []
        for s in self.svc["services"]:
            if s.get("status", "active") != "active":
                continue
            if category and s.get("category") != category:
                continue
            if provider and s.get("provider") != provider:
                continue
            result.append({
                "id": s["id"], "name": s["name"], "provider": s["provider"],
                "description": s.get("description", ""), "price": s.get("price", 0),
                "unit": s.get("unit", "次"), "category": s.get("category"),
                "total_sold": s.get("total_sold", 0)
            })
        return {"ok": True, "count": len(result), "services": result}

    def buy_service(self, buyer_id, service_id, quantity):
        """购买服务：直接Token转账，固定价格"""
        valid, err = validate_account_id(buyer_id)
        if not valid:
            return {"ok": False, "err": err}
        buyer = self.get_account(buyer_id)
        if not buyer:
            return {"ok": False, "err": "account_not_found"}
        if not service_id or not isinstance(service_id, str):
            return {"ok": False, "err": "service_id required"}
        if not isinstance(quantity, (int, float)) or int(quantity) < 1:
            return {"ok": False, "err": "quantity must be positive integer"}
        quantity = int(quantity)

        # 找服务
        service = None
        for s in self.svc["services"]:
            if s["id"] == service_id and s.get("status", "active") == "active":
                service = s
                break
        if not service:
            return {"ok": False, "err": "service_not_found"}

        # 不能买自己的服务
        if service["provider"] == buyer_id:
            return {"ok": False, "err": "cannot_buy_own_service"}

        # 计算费用
        price = service.get("price", 0)
        if price <= 0:
            return {"ok": False, "err": "invalid_service_price"}
        total_cost = price * quantity
        available = buyer["balance"] - buyer["frozen"]
        if available < total_cost:
            return {"ok": False, "err": f"insufficient_balance:need={total_cost},available={available}"}

        # 计算佣金
        taker_comm = self._calc_commission(total_cost, "taker")
        maker_comm = self._calc_commission(total_cost, "maker")

        # 转账
        seller = self.get_account(service["provider"])
        buyer["balance"] -= total_cost
        buyer["balance"] -= taker_comm
        seller["balance"] += total_cost
        seller["balance"] -= maker_comm
        self.ob["total_commission_earned"] += taker_comm + maker_comm

        # 更新服务统计（在first_sale_bonus检查之后）
        service["total_sold"] = service.get("total_sold", 0) + quantity
        service["total_revenue"] = service.get("total_revenue", 0) + total_cost

        # 记录订单
        order = {
            "id": gen_id(), "service_id": service_id,
            "service_name": service["name"],
            "buyer": buyer_id, "provider": service["provider"],
            "quantity": quantity, "price_per_unit": price,
            "total_cost": total_cost,
            "commission_taker": taker_comm, "commission_maker": maker_comm,
            "time": now_str()
        }
        self.svc["orders"].append(order)

        # Provider Early Bird: 首笔成交奖励（必须在total_sold递增之前检查）
        first_sale_bonus = 0
        if service["total_sold"] == 0:  # 这是第一笔成交
            first_sale_bonus = self.config.get("provider_incentive", {}).get("first_sale_bonus", 200)
            platform = self.get_account("platform")
            if platform and platform["balance"] >= first_sale_bonus:
                platform["balance"] -= first_sale_bonus
                seller["balance"] += first_sale_bonus
            else:
                first_sale_bonus = 0

        self._save()
        self._log(f"service_bought:{service_id},{service['name']},{buyer_id}->{service['provider']},{quantity}x{service['price']},comm:{taker_comm+maker_comm},first_sale_bonus:{first_sale_bonus}")
        result = {
            "ok": True, "order": order,
            "buyer_balance": buyer["balance"],
            "seller_balance": seller["balance"]
        }
        if first_sale_bonus > 0:
            result["first_sale_bonus"] = first_sale_bonus
            result["note"] = f"Provider first sale bonus: {first_sale_bonus} ATEX!"
        return result

    def update_service(self, provider_id, service_id, **kwargs):
        """更新服务信息（仅提供者可操作）"""
        valid, _ = validate_account_id(provider_id)
        if not valid:
            return {"ok": False, "err": "invalid_account_id"}
        for s in self.svc["services"]:
            if s["id"] == service_id and s["provider"] == provider_id:
                if "name" in kwargs and kwargs["name"]:
                    s["name"] = kwargs["name"][:MAX_SERVICE_NAME_LEN]
                if "description" in kwargs and kwargs["description"]:
                    s["description"] = kwargs["description"][:MAX_DESC_LEN]
                if "price" in kwargs:
                    valid, err = validate_price(kwargs["price"])
                    if not valid:
                        return {"ok": False, "err": f"price: {err}"}
                    s["price"] = round(float(kwargs["price"]), 2)
                if "unit" in kwargs and kwargs["unit"]:
                    s["unit"] = kwargs["unit"]
                if "category" in kwargs and kwargs["category"]:
                    s["category"] = kwargs["category"]
                if "status" in kwargs and kwargs["status"] in ("active", "paused"):
                    s["status"] = kwargs["status"]
                self._save()
                self._log(f"service_updated:{service_id},{provider_id}")
                return {"ok": True, "service": s}
        return {"ok": False, "err": "service_not_found_or_not_owner"}

    def remove_service(self, provider_id, service_id):
        """下架服务"""
        for i, s in enumerate(self.svc["services"]):
            if s["id"] == service_id and s["provider"] == provider_id:
                self.svc["services"].pop(i)
                self._save()
                self._log(f"service_removed:{service_id},{provider_id}")
                return {"ok": True}
        return {"ok": False, "err": "service_not_found_or_not_owner"}

    def my_services(self, provider_id):
        """查看我注册的服务"""
        result = []
        for s in self.svc["services"]:
            if s["provider"] == provider_id:
                result.append(s)
        return {"ok": True, "count": len(result), "services": result}

    def service_orders(self, acc_id=None, limit=20):
        """查看服务订单历史"""
        orders = self.svc.get("orders", [])
        if acc_id:
            orders = [o for o in orders if o.get("buyer") == acc_id or o.get("provider") == acc_id]
        return {"ok": True, "count": len(orders), "orders": orders[-limit:]}

    # ── API代理（通用API信用Token）──

    def list_apis(self):
        """列出可用API及定价"""
        api_pricing = self.config.get("api_pricing", {})
        apis = []
        for api_name, info in api_pricing.items():
            apis.append({
                "id": api_name,
                "name": info.get("description", api_name),
                "cost": info.get("cost", 0),
                "unit": info.get("unit", ""),
                "models": info.get("models", []),
                "backend": info.get("backend", ""),
            })
        return {"ok": True, "count": len(apis), "apis": apis}

    def api_proxy(self, acc_id, api_name, params=None):
        """花ATEX调底层API：扣费+执行"""
        valid, err = validate_account_id(acc_id)
        if not valid:
            return {"ok": False, "err": err}
        acc = self.get_account(acc_id)
        if not acc:
            return {"ok": False, "err": "account_not_found"}
        api_pricing = self.config.get("api_pricing", {})
        api_info = api_pricing.get(api_name)
        if not api_info:
            return {"ok": False, "err": f"unknown_api:{api_name}", "available": list(api_pricing.keys())}
        # Check if API is live
        if api_info.get("status") == "coming_soon":
            return {"ok": False, "err": f"api_coming_soon:{api_name}", "note": api_info.get("note", "This API requires a provider with the corresponding API key. Register as a provider to offer this API.")}
        cost = api_info.get("cost", 0)
        available = acc["balance"] - acc["frozen"]
        if available < cost:
            return {"ok": False, "err": f"insufficient_balance:need={cost},available={available}"}
        # 扣费
        acc["balance"] -= cost
        # 佣金
        commission = self._calc_commission(cost, "taker")
        acc["balance"] -= commission
        self.ob["total_commission_earned"] += commission
        self._save()
        self._log(f"api_proxy:{acc_id},{api_name},cost:{cost},commission:{commission}")
        return {
            "ok": True, "api": api_name, "cost": cost, "commission": commission,
            "remaining_balance": acc["balance"],
            "note": "API execution happens via REST endpoint /api/v1/services/execute"
        }

    # ── 查询 ──

    def status(self):
        active_services = sum(1 for s in self.svc["services"] if s.get("status", "active") == "active")
        total_service_orders = len(self.svc.get("orders", []))
        return {
            "exchange": self.config["name"],
            "version": self.config["version"],
            "accounts": len(self.accounts["accounts"]),
            "open_orders": len(self.ob["bids"]) + len(self.ob["asks"]),
            "total_trades": len(self.ob["trades"]),
            "total_commission": self.ob["total_commission_earned"],
            "last_price": self.ob["last_price"],
            "daily_volume": self.ob["daily_volume"],
            "commission_rates": f"maker {self.commission_maker*100}%, taker {self.commission_taker*100}%",
            "services": active_services,
            "service_orders": total_service_orders,
        }

    def settle(self, acc_id, amount):
        """结算：仅owner可用，将平台佣金ATEX转至owner账户"""
        acc = self.get_account(acc_id)
        if not acc:
            return {"ok": False, "err": "account_not_found"}
        if acc.get("role") != "owner":
            return {"ok": False, "err": "owner_only"}
        if amount <= 0:
            return {"ok": False, "err": "amount_must_be_positive"}
        commission = self.ob["total_commission_earned"]
        if amount > commission:
            return {"ok": False, "err": f"insufficient_commission:available={commission},requested={amount}"}
        self.ob["total_commission_earned"] -= amount
        acc["balance"] += amount
        self._save()
        self._log(f"settle:{acc_id},atex:{amount},remaining_commission:{self.ob['total_commission_earned']}")
        return {
            "ok": True, "amount": amount, "currency": "ATEX",
            "new_balance": acc["balance"],
            "remaining_commission": self.ob["total_commission_earned"]
        }


def main():
    if not sys.stdin.isatty():
        raw = sys.stdin.read(MAX_INPUT_SIZE)
    elif len(sys.argv) >= 2:
        raw = sys.argv[1]
        if len(raw) > MAX_INPUT_SIZE:
            print(json.dumps({"err": "input_too_large"}, ensure_ascii=False))
            return
    else:
        print(json.dumps(ATEX().status(), ensure_ascii=False, indent=2))
        return

    try:
        cmd = safe_json_loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        print(json.dumps({"err": f"invalid_json:{e}"}, ensure_ascii=False))
        return

    if not isinstance(cmd, dict):
        print(json.dumps({"err": "input_must_be_object"}, ensure_ascii=False))
        return

    ex = ATEX()
    action = cmd.get("action")

    if not action or not isinstance(action, str):
        print(json.dumps({"err": "missing_action"}, ensure_ascii=False))
        return

    caller = cmd.get("account", cmd.get("account_id", "anonymous"))
    if not rate_limiter.check(caller):
        print(json.dumps({"err": "rate_limited"}, ensure_ascii=False))
        return

    if action == "status":
        print(json.dumps(ex.status(), ensure_ascii=False, indent=2))
    elif action == "account":
        acc_id = cmd.get("account", "")
        valid, err = validate_account_id(acc_id)
        if not valid:
            print(json.dumps({"err": err}, ensure_ascii=False))
            return
        acc = ex.get_account(acc_id)
        print(json.dumps(acc if acc else {"err": "not_found"}, ensure_ascii=False, indent=2))
    elif action == "create_account":
        r = ex.create_account(cmd.get("account_id", ""), cmd.get("role", "trader"))
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif action == "deposit":
        r = ex.deposit(cmd.get("account", ""), cmd.get("amount", 0))
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif action == "order":
        o = cmd.get("order", {})
        if not o:
            print(json.dumps({"err": "missing_order"}, ensure_ascii=False))
            return
        r = ex.place_order(o.get("account", ""), o.get("side", ""), o.get("price", 0), o.get("amount", 0))
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif action == "cancel":
        c = cmd.get("cancel", {})
        if not c:
            print(json.dumps({"err": "missing_cancel"}, ensure_ascii=False))
            return
        r = ex.cancel_order(c.get("account", ""), c.get("order_id", ""))
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif action == "query":
        print(json.dumps(ex.query_orderbook(), ensure_ascii=False, indent=2))
    elif action == "history":
        print(json.dumps(ex.trade_history(cmd.get("limit", 20)), ensure_ascii=False, indent=2))
    elif action == "settle":
        r = ex.settle(cmd.get("account", ""), cmd.get("amount", 0))
        print(json.dumps(r, ensure_ascii=False, indent=2))
    # ── 服务市场 ──
    elif action == "register_service":
        r = ex.register_service(
            cmd.get("provider", ""), cmd.get("name", ""),
            cmd.get("description", ""), cmd.get("price", 0),
            cmd.get("unit", ""), cmd.get("category", ""))
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif action == "list_services":
        r = ex.list_services(cmd.get("category"), cmd.get("provider"))
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif action == "buy_service":
        r = ex.buy_service(cmd.get("buyer", ""), cmd.get("service_id", ""), cmd.get("quantity", 1))
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif action == "update_service":
        r = ex.update_service(cmd.get("provider", ""), cmd.get("service_id", ""),
            name=cmd.get("name"), description=cmd.get("description"),
            price=cmd.get("price"), unit=cmd.get("unit"),
            category=cmd.get("category"), status=cmd.get("status"))
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif action == "remove_service":
        r = ex.remove_service(cmd.get("provider", ""), cmd.get("service_id", ""))
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif action == "my_services":
        r = ex.my_services(cmd.get("provider", ""))
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif action == "service_orders":
        r = ex.service_orders(cmd.get("account"), cmd.get("limit", 20))
        print(json.dumps(r, ensure_ascii=False, indent=2))
    # ── API代理 ──
    elif action == "list_apis":
        print(json.dumps(ex.list_apis(), ensure_ascii=False, indent=2))
    elif action == "api_proxy":
        r = ex.api_proxy(cmd.get("account", ""), cmd.get("api", ""), cmd.get("params"))
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"err": f"unknown_action:{action}"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
