"""自然语言规则引擎（参考 AutoAgent 零代码思想）。

运营人员用大白话描述规则，系统解析为结构化条件 + 动作。
当前为规则化解析（LLM 接入点已留：parse_with_llm）。
"""
from __future__ import annotations
import json
import re
from typing import Dict, List
from db import get_conn, now_iso


SAMPLE_PATTERNS = {
    "评论审核": {
        "keywords": ["评论", "回复", "留言"],
        "actions": ["review", "auto_approve", "notify"],
    },
    "广告接单": {
        "keywords": ["广告", "广告主", "投放", "预算"],
        "actions": ["quote", "escalate", "create_ticket"],
    },
    "UGC审核": {
        "keywords": ["题目", "市场", "UGC", "发起", "预测题"],
        "actions": ["moderate", "review", "reject"],
    },
    "客服": {
        "keywords": ["投诉", "bug", "建议", "账号", "登录"],
        "actions": ["reply", "create_ticket", "escalate"],
    },
    "结算": {
        "keywords": ["结算", "开奖", "结果", "Oracle"],
        "actions": ["resolve", "dispute", "notify"],
    },
}


def _detect_domain(text: str) -> str:
    t = text.lower()
    scores = {}
    for domain, meta in SAMPLE_PATTERNS.items():
        scores[domain] = sum(1 for k in meta["keywords"] if k in t)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "通用"


def parse_rule(natural_text: str) -> Dict[str, any]:
    """把自然语言规则解析为结构化规则。"""
    domain = _detect_domain(natural_text)
    # 简单提取阈值
    threshold_match = re.search(r'(\d+)%?', natural_text)
    threshold = int(threshold_match.group(1)) if threshold_match else None

    # 动作推断
    actions = []
    lower = natural_text.lower()
    if any(w in lower for w in ["自动通过", "自动批准", "直接上线"]):
        actions.append("auto_approve")
    if any(w in lower for w in ["人工", "复核", "审核"]):
        actions.append("review")
    if any(w in lower for w in ["拒绝", "拦截", "下架"]):
        actions.append("reject")
    if any(w in lower for w in ["通知", "提醒", "回访"]):
        actions.append("notify")
    if any(w in lower for w in ["升级", "工单", "转人工"]):
        actions.append("create_ticket")
    if not actions:
        actions = ["review"]

    return {
        "domain": domain,
        "raw": natural_text,
        "actions": actions,
        "threshold": threshold,
        "conditions": [],
    }


def save_rule(name: str, rule: Dict, created_by: str = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO agent_rules (name, domain, rule_json, created_by, created_at) "
            "VALUES (?,?,?,?,?)",
            (name, rule.get("domain"), json.dumps(rule, ensure_ascii=False),
             created_by, now_iso()),
        )
        conn.commit()
        return cur.lastrowid


def list_rules(domain: str = None) -> List[Dict]:
    with get_conn() as conn:
        sql = "SELECT * FROM agent_rules"
        params = []
        if domain:
            sql += " WHERE domain=?"
            params.append(domain)
        sql += " ORDER BY created_at DESC LIMIT 200"
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["rule"] = json.loads(d["rule_json"] or "{}")
            except Exception:
                d["rule"] = {}
            del d["rule_json"]
            out.append(d)
        return out
