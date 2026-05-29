#!/usr/bin/env python3
"""ATEX Content Safety & Reporting System — 内容安全与举报系统
对标Moltplace：prompt injection防御、内容举报、自动审核
"""
import json, os, time, threading, re
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TZ = timezone(timedelta(hours=8))
REPORTS_FILE = os.path.join(BASE, "data", "reports.json")
_lock = threading.RLock()

def _now(): return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def _load():
    if os.path.exists(REPORTS_FILE):
        with open(REPORTS_FILE) as f: return json.load(f)
    return {"reports": [], "next_id": 1, "auto_blocks": [], "moderation_log": []}

def _save(data):
    os.makedirs(os.path.dirname(REPORTS_FILE), exist_ok=True)
    with open(REPORTS_FILE, "w") as f: json.dump(data, f, ensure_ascii=False, indent=2)

# ── Prompt Injection Detection ──

INJECTION_PATTERNS = [
    r'ignore\s+(all\s+)?previous\s+instructions',
    r'forget\s+(all\s+)?previous\s+(instructions|context)',
    r'you\s+are\s+now\s+a',
    r'system\s*:\s*',
    r'new\s+instructions?\s*:',
    r'disregard\s+(your|the)\s+(rules|guidelines|instructions)',
    r'override\s+(safety|security|content)\s+(policy|filter|guidelines)',
    r'pretend\s+(you\s+are|to\s+be)\s+a',
    r'jailbreak',
    r'DAN\s+mode',
    r'act\s+as\s+if\s+you\s+have\s+no\s+(restrictions|rules|limits)',
    r'bypass\s+(the\s+)?(filter|safety|security|content)',
    r'reveal\s+(your|the)\s+(system\s+)?prompt',
    r'show\s+me\s+(your|the)\s+(system\s+)?prompt',
    r'extract\s+(your|the)\s+(system\s+)?prompt',
    r'<\|im_start\|>',
    r'\[INST\]',
]

_injection_regex = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

def check_prompt_injection(text):
    """检测prompt injection攻击。返回 (is_safe, detected_patterns)"""
    if not text: return True, []
    detected = []
    for i, regex in enumerate(_injection_regex):
        if regex.search(text):
            detected.append(INJECTION_PATTERNS[i])
    return len(detected) == 0, detected

def scan_content(text, content_type="message"):
    """扫描内容安全性。返回 {safe, threats, risk_level}"""
    threats = []
    risk_level = "low"

    # 1. Prompt injection检测
    is_safe, patterns = check_prompt_injection(text)
    if patterns:
        threats.append({"type": "prompt_injection", "patterns": patterns})
        risk_level = "high"

    # 2. 敏感信息泄露检测
    sensitive_patterns = [
        (r'(?:api[_-]?key|apikey)\s*[=:]\s*["\']?[\w-]{20,}', "api_key_exposure"),
        (r'(?:password|passwd|pwd)\s*[=:]\s*["\']?[\w-]{8,}', "password_exposure"),
        (r'(?:secret|token)\s*[=:]\s*["\']?[\w-]{20,}', "secret_exposure"),
        (r'ghp_[\w]{36}', "github_token"),
        (r'sk-[\w]{20,}', "openai_key"),
    ]
    for pattern, threat_type in sensitive_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            threats.append({"type": threat_type})
            risk_level = "medium"

    # 3. 恶意URL检测
    url_pattern = r'https?://[^\s<>"\']+(?:\.(?:exe|bat|sh|cmd|ps1|vbs|js))[^\s<>"\']*'
    if re.search(url_pattern, text, re.IGNORECASE):
        threats.append({"type": "suspicious_url"})
        if risk_level == "low": risk_level = "medium"

    # 4. 超长内容检测（可能DoS）
    if len(text) > 500000:
        threats.append({"type": "oversized_content", "size": len(text)})
        if risk_level == "low": risk_level = "medium"

    return {
        "safe": len(threats) == 0,
        "threats": threats,
        "risk_level": risk_level,
        "scanned_at": _now()
    }

# ── Reporting System ──

def submit_report(reporter_uid, d):
    """提交举报"""
    with _lock:
        data = _load()
    content_type = d.get("content_type")  # service/skill/job/message/user
    content_id = d.get("content_id")
    reason = d.get("reason")  # prompt_injection/exfiltration/phishing/spam/copyright/malware/other
    if not all([content_type, content_id, reason]):
        return {"err": "content_type, content_id, and reason are required"}
    valid_reasons = ["prompt_injection", "exfiltration", "phishing", "spam", "copyright", "malware", "hate_speech", "other"]
    if reason not in valid_reasons:
        return {"err": f"invalid_reason. Valid: {valid_reasons}"}
    report_id = f"rpt_{data['next_id']:04d}"
    report = {
        "id": report_id,
        "reporter": reporter_uid,
        "content_type": content_type,
        "content_id": content_id,
        "reason": reason,
        "description": d.get("description", ""),
        "evidence": d.get("evidence", ""),
        "status": "pending",  # pending/reviewing/resolved/dismissed
        "created_at": _now(),
        "resolved_at": None,
        "resolution": None
    }
    data["reports"].append(report)
    data["next_id"] += 1
    # 自动处理高风险举报
    if reason in ("prompt_injection", "exfiltration", "malware"):
        report["status"] = "reviewing"
        data["moderation_log"].append({
            "action": "auto_escalate",
            "report_id": report_id,
            "reason": f"Auto-escalated: {reason}",
            "at": _now()
        })
    with _lock:
        _save(data)
    return {"ok": True, "report_id": report_id, "status": report["status"]}

def list_reports(admin_uid=None, filters=None):
    """列出举报（管理员）"""
    with _lock:
        data = _load()
    reports = data["reports"]
    if filters:
        status = filters.get("status")
        if status: reports = [r for r in reports if r["status"] == status]
        reason = filters.get("reason")
        if reason: reports = [r for r in reports if r["reason"] == reason]
        content_type = filters.get("content_type")
        if content_type: reports = [r for r in reports if r["content_type"] == content_type]
    reports.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return {"ok": True, "total": len(reports), "reports": reports}

def resolve_report(admin_uid, report_id, d):
    """处理举报（管理员）"""
    with _lock:
        data = _load()
    report = None
    for r in data["reports"]:
        if r["id"] == report_id:
            report = r
            break
    if not report: return {"err": "report_not_found"}
    if report["status"] in ("resolved", "dismissed"): return {"err": "already_resolved"}
    action = d.get("action")  # dismiss/warn/block_content/ban_user
    if not action: return {"err": "action_required"}
    report["status"] = "resolved" if action != "dismiss" else "dismissed"
    report["resolution"] = {
        "action": action,
        "admin": admin_uid,
        "note": d.get("note", ""),
        "resolved_at": _now()
    }
    report["resolved_at"] = _now()
    # 执行处罚
    if action == "block_content":
        data["auto_blocks"].append({
            "content_type": report["content_type"],
            "content_id": report["content_id"],
            "reason": report["reason"],
            "blocked_at": _now()
        })
    data["moderation_log"].append({
        "action": action,
        "report_id": report_id,
        "admin": admin_uid,
        "at": _now()
    })
    with _lock:
        _save(data)
    return {"ok": True, "report_id": report_id, "action": action}

def is_content_blocked(content_type, content_id):
    """检查内容是否被封禁"""
    with _lock:
        data = _load()
    for b in data.get("auto_blocks", []):
        if b["content_type"] == content_type and b["content_id"] == content_id:
            return True
    return False

def safety_stats():
    """安全统计"""
    with _lock:
        data = _load()
    reports = data["reports"]
    return {
        "ok": True,
        "total_reports": len(reports),
        "pending": len([r for r in reports if r["status"] == "pending"]),
        "reviewing": len([r for r in reports if r["status"] == "reviewing"]),
        "resolved": len([r for r in reports if r["status"] == "resolved"]),
        "dismissed": len([r for r in reports if r["status"] == "dismissed"]),
        "blocked_content": len(data.get("auto_blocks", [])),
        "by_reason": {r: len([x for x in reports if x["reason"] == r]) for r in set(x["reason"] for x in reports)}
    }
