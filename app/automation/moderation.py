"""UGC 审核管线（PRD 增订：UGC 发起与 AI 分级审核）。

四道闸：
  ① 白名单分类（复用 core.whitelist）
  ② 主权红线·敏感词（硬 reject）
  ③ 诱导赌博话术（硬 reject）
  ④ 可结算性 + 质量初筛（一票否决自动上线 → review）
分级路由：auto / review / reject。
"""
from core import whitelist

# 闸③：诱导赌博话术（命中即硬 reject）
GAMBLING_TERMS = [
    "押注", "盘口", "赔率", "对赌", "下注", "稳赚", "包赢",
    "跟单", "翻本", "杠杆", "倍投", "彩票", "赌", "抽水", "返水",
]

# 闸④：引流 / 广告 / 侵权嫌疑（命中进 review）
SPAM_SIGNALS = [
    "加微信", "扫码", "私聊", "加我", "+v", "代购", "兼职", "返利",
    "点击链接", "http://", "https://", "www.", "二维码", "招代理",
]

# 闸④：可信 Oracle 关键词（用于可结算性初判，非穷举）
ORACLE_HINTS = [
    "官网", "官方", "协会", "统计局", "央视", "新华", "腾讯", "新浪",
    "微博", "票房", "财报", "发布", "直播", "新闻", "公报", "通报",
]


def detect_gambling_terms(text):
    t = text or ""
    return [w for w in GAMBLING_TERMS if w in t]


def quality_screen(title, description, oracle_source):
    """返回 (reasons, settlement_ok)。可结算性缺失只进 review，不直接 reject。"""
    reasons = []
    full = (title or "") + " " + (description or "")
    spam = [s for s in SPAM_SIGNALS if s in full]
    if spam:
        reasons.append("引流/广告嫌疑:" + ",".join(spam[:3]))
    settlement_ok = bool(oracle_source)
    if not settlement_ok:
        reasons.append("缺结算数据源(Oracle)")
    return reasons, settlement_ok


def moderate_submission(sub):
    """sub: dict(title, description, category, options, oracle_source,
                 settlement_criteria, creator)
    返回 dict(route, reasons, category, whitelist_tag)。
    route: auto / review / reject
    """
    title = sub.get("title", "") or ""
    desc = sub.get("description", "") or ""
    criteria = sub.get("settlement_criteria", "") or ""
    full = " ".join([title, desc, criteria])
    reasons = []

    # 闸①+②：白名单 + 主权/敏感
    cls = whitelist.classify(full)
    category = sub.get("category") or cls.get("category") or "未分类"
    if cls["forbidden"] or cls["sovereignty_risk"]:
        return {"route": "reject", "reasons": ["硬违规: 白名单/主权红线"],
                "category": category, "whitelist_tag": "禁止"}
    if cls["safe"]:
        whitelist_tag = "安全"
    elif cls["conditional"]:
        whitelist_tag = "条件"
        reasons.append("条件品类需复核")
    else:
        whitelist_tag = "未知"
        reasons.append("未命中白名单类别")

    # 闸③：诱导赌博话术（硬 reject）
    gterms = detect_gambling_terms(full)
    if gterms:
        return {"route": "reject",
                "reasons": ["诱导赌博话术:" + ",".join(gterms[:3])],
                "category": category, "whitelist_tag": whitelist_tag}

    # 闸④：质量 + 可结算性
    qreasons, settlement_ok = quality_screen(title, desc, sub.get("oracle_source", ""))
    reasons += qreasons

    # 路由判定
    if (not settlement_ok) or whitelist_tag in ("未知", "条件") or qreasons:
        return {"route": "review", "reasons": reasons,
                "category": category, "whitelist_tag": whitelist_tag}
    # 全过
    return {"route": "auto", "reasons": reasons,
            "category": category, "whitelist_tag": whitelist_tag}
