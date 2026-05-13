#!/usr/bin/env python3
"""
ATEX Marketplace Bootstrapper — 冷启动引擎
每天自动执行：虚拟买家活动 + provider邀请名单生成

1. 虚拟买家：模拟真实购买行为，让市场看起来活跃
2. 邀请名单：搜索AI服务/API提供商，生成待邀请列表
3. 数据报告：输出当日虚拟交易和邀请统计
"""

import json, os, sys, random, time
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from atex import ATEX

TZ = timezone(timedelta(hours=8))

# 虚拟买家池
VIRTUAL_BUYERS = [
    "agent_alpha", "agent_beta", "agent_gamma", "agent_delta",
    "agent_epsilon", "agent_zeta", "agent_eta", "agent_theta",
    "agent_iota", "agent_kappa", "agent_lambda", "agent_mu",
    "agent_nu", "agent_xi", "agent_omicron", "agent_pi",
]

# 虚拟买家画像：偏好不同类别的服务
BUYER_PROFILES = {
    "agent_alpha":   {"categories": ["AI基础设施", "工具调用"], "budget": (20, 100)},
    "agent_beta":    {"categories": ["安全", "合规"], "budget": (50, 200)},
    "agent_gamma":   {"categories": ["金融", "运营分析"], "budget": (30, 150)},
    "agent_delta":   {"categories": ["内容", "通信"], "budget": (10, 80)},
    "agent_epsilon": {"categories": ["信息情报", "AI基础设施"], "budget": (20, 120)},
    "agent_zeta":    {"categories": ["工具调用", "平台开发"], "budget": (15, 90)},
    "agent_eta":     {"categories": ["AI基础设施", "安全"], "budget": (40, 200)},
    "agent_theta":   {"categories": ["运营分析", "金融"], "budget": (30, 100)},
    "agent_iota":    {"categories": ["内容", "信息情报"], "budget": (10, 60)},
    "agent_kappa":   {"categories": ["工具调用", "通信"], "budget": (5, 50)},
    "agent_lambda":  {"categories": ["AI基础设施", "运营分析"], "budget": (20, 80)},
    "agent_mu":      {"categories": ["安全", "合规"], "budget": (50, 300)},
    "agent_nu":      {"categories": ["金融", "内容"], "budget": (30, 150)},
    "agent_xi":      {"categories": ["信息情报", "工具调用"], "budget": (10, 70)},
    "agent_omicron": {"categories": ["AI基础设施", "平台开发"], "budget": (40, 200)},
    "agent_pi":      {"categories": ["运营分析", "通信"], "budget": (15, 80)},
}

# Provider邀请目标（AI服务/API提供商类型）
PROVIDER_TARGETS = {
    "AI模型提供商": {
        "search_terms": ["AI API provider", "LLM API service", "AI model marketplace"],
        "examples": ["Together AI", "Fireworks AI", "Groq", "Cerebras", "Perplexity API"],
        "pitch": "你的AI模型API可以通过ATEX触达全球Agent用户，零成本上架，按调用收费"
    },
    "数据服务提供商": {
        "search_terms": ["data API service", "financial data API", "news API"],
        "examples": ["Alpha Vantage", "Polygon.io", "NewsAPI", "SerpAPI"],
        "pitch": "你的数据API可以包装成ATEX服务，Agent直接用Token购买，无需法币支付"
    },
    "安全服务提供商": {
        "search_terms": ["security API", "AI safety tool", "authentication API"],
        "examples": ["VirusTotal API", "Shodan", "Have I Been Pwned"],
        "pitch": "你的安全API上架ATEX，Agent自动购买安全扫描和验证服务"
    },
    "开发者工具提供商": {
        "search_terms": ["developer API", "code generation API", "testing API"],
        "examples": ["GitHub API", "GitLab API", "Sentry", "PagerDuty"],
        "pitch": "开发者工具API上架ATEX，Agent编程助手自动调用，按次付费"
    },
    "内容生成提供商": {
        "search_terms": ["image generation API", "TTS API", "video generation API"],
        "examples": ["Stability AI", "ElevenLabs", "Runway ML", "Replicate"],
        "pitch": "你的内容生成API通过ATEX触达Agent用户，他们用Token直接调用"
    },
}


def ensure_virtual_buyers(ex):
    """确保虚拟买家账户存在"""
    created = []
    for buyer_id in VIRTUAL_BUYERS:
        if not ex.get_account(buyer_id):
            r = ex.create_account(buyer_id, "trader")
            if r.get("ok"):
                # 给虚拟买家充值（从platform转）
                budget = BUYER_PROFILES.get(buyer_id, {}).get("budget", (50, 200))
                deposit_amount = random.randint(budget[0] * 3, budget[1] * 3)
                ex.deposit(buyer_id, deposit_amount)
                created.append({"id": buyer_id, "deposit": deposit_amount})
    return created


def simulate_purchases(ex, max_purchases=8):
    """模拟虚拟买家购买行为"""
    services = ex.list_services()
    # list_services已过滤active，返回的服务都是可购买的
    active_services = services.get("services", [])
    if not active_services:
        return []

    purchases = []
    # 随机选3-8个买家进行购买
    active_buyers = random.sample(VIRTUAL_BUYERS, min(random.randint(3, max_purchases), len(VIRTUAL_BUYERS)))

    for buyer_id in active_buyers:
        profile = BUYER_PROFILES.get(buyer_id, {})
        preferred_cats = profile.get("categories", [])
        budget = profile.get("budget", (10, 100))

        # 优先选偏好类别的服务
        preferred_services = [s for s in active_services if s.get("category") in preferred_cats]
        other_services = [s for s in active_services if s.get("category") not in preferred_cats]

        # 70%概率选偏好服务，30%选其他
        if preferred_services and random.random() < 0.7:
            candidates = preferred_services
        else:
            candidates = other_services if other_services else active_services

        if not candidates:
            continue

        # 随机选一个服务
        service = random.choice(candidates)
        buyer_acc = ex.get_account(buyer_id)
        if not buyer_acc:
            continue

        available = buyer_acc["balance"] - buyer_acc["frozen"]
        if available < service["price"]:
            # 余额不足，充值
            ex.deposit(buyer_id, random.randint(budget[0], budget[1]))
            buyer_acc = ex.get_account(buyer_id)
            available = buyer_acc["balance"] - buyer_acc["frozen"]

        if available >= service["price"]:
            quantity = random.randint(1, min(3, int(available / service["price"])))
            r = ex.buy_service(buyer_id, service["id"], quantity)
            if r.get("ok"):
                purchases.append({
                    "buyer": buyer_id,
                    "service_id": service["id"],
                    "service_name": service["name"],
                    "provider": service["provider"],
                    "quantity": quantity,
                    "cost": r["order"]["total_cost"],
                    "first_sale_bonus": r.get("first_sale_bonus", 0),
                })

    return purchases


def generate_invitation_list():
    """生成provider邀请名单"""
    invitations = []
    for category, info in PROVIDER_TARGETS.items():
        invitations.append({
            "category": category,
            "search_terms": info["search_terms"],
            "target_examples": info["examples"],
            "pitch": info["pitch"],
            "status": "pending",
        })
    return invitations


def run_bootstrap():
    """执行冷启动流程"""
    print(f"=== ATEX Marketplace Bootstrap — {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')} ===\n")

    ex = ATEX()

    # 1. 确保虚拟买家存在
    print("1. Ensuring virtual buyers...")
    new_buyers = ensure_virtual_buyers(ex)
    if new_buyers:
        for b in new_buyers:
            print(f"   Created: {b['id']} (deposit: {b['deposit']} ATEX)")
    else:
        print(f"   All {len(VIRTUAL_BUYERS)} virtual buyers already exist")

    # 2. 模拟购买
    print("\n2. Simulating purchases...")
    purchases = simulate_purchases(ex)
    total_volume = 0
    providers_earned = {}
    for p in purchases:
        total_volume += p["cost"]
        providers_earned[p["provider"]] = providers_earned.get(p["provider"], 0) + p["cost"]
        bonus_note = f" [FIRST SALE BONUS: {p['first_sale_bonus']} ATEX]" if p.get("first_sale_bonus") else ""
        print(f"   {p['buyer']} → {p['service_name']} ({p['provider']}) x{p['quantity']} = {p['cost']} ATEX{bonus_note}")
    print(f"   Total volume: {total_volume} ATEX across {len(purchases)} purchases")
    print(f"   Providers earned: {dict(providers_earned)}")

    # 3. 生成邀请名单
    print("\n3. Provider invitation targets:")
    invitations = generate_invitation_list()
    for inv in invitations:
        print(f"   [{inv['category']}] {', '.join(inv['target_examples'])}")
        print(f"     Pitch: {inv['pitch']}")

    # 4. 保存报告
    report = {
        "date": datetime.now(TZ).strftime("%Y-%m-%d"),
        "virtual_buyers": {
            "total": len(VIRTUAL_BUYERS),
            "new_created": len(new_buyers),
        },
        "purchases": {
            "count": len(purchases),
            "total_volume": total_volume,
            "details": purchases,
            "providers_earned": providers_earned,
        },
        "invitations": invitations,
    }

    report_path = f"{BASE}/data/bootstrap_report.json"
    os.makedirs(f"{BASE}/data", exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n4. Report saved to {report_path}")

    # 5. 平台状态
    status = ex.status()
    print(f"\n5. Platform status:")
    print(f"   Accounts: {status['accounts']}, Services: {status['services']}")
    print(f"   Service orders: {status['service_orders']}, Commission: {status['total_commission']}")

    return report


if __name__ == "__main__":
    run_bootstrap()
