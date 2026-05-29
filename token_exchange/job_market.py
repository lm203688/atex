#!/usr/bin/env python3
"""ATEX Job Market — AI Agent自主投标竞标的劳动力市场
对标Workfoz：雇主发Job → Agent自主投标 → 雇主选择 → Agent执行 → 结算
"""
import json, os, time, threading
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TZ = timezone(timedelta(hours=8))
JOBS_FILE = os.path.join(BASE, "data", "jobs.json")
_lock = threading.RLock()

def _now(): return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
def _today(): return datetime.now(TZ).strftime("%Y-%m-%d")

def _load():
    if os.path.exists(JOBS_FILE):
        with open(JOBS_FILE) as f: return json.load(f)
    return {"jobs": {}, "next_id": 1}

def _save(data):
    os.makedirs(os.path.dirname(JOBS_FILE), exist_ok=True)
    with open(JOBS_FILE, "w") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def _auth_user(auth_header, saas_data):
    """从Bearer token获取用户信息"""
    if not auth_header: return None, None
    token = auth_header.replace("Bearer ", "")
    uid = saas_data.get("api_keys", {}).get(token)
    if not uid: return None, None
    user = saas_data.get("users", {}).get(uid)
    return uid, user

# ── Job CRUD ──

def create_job(employer_uid, d):
    """雇主发布Job"""
    with _lock:
        data = _load()
    job_id = f"job_{data['next_id']:04d}"
    job = {
        "id": job_id,
        "employer": employer_uid,
        "title": d.get("title", ""),
        "description": d.get("description", ""),
        "category": d.get("category", "general"),  # coding/research/writing/data/translation/design/general
        "budget_min": d.get("budget_min"),      # CNY, 可选
        "budget_max": d.get("budget_max"),      # CNY, 可选
        "budget_currency": d.get("budget_currency", "CNY"),
        "deadline": d.get("deadline"),           # ISO date, 可选
        "skills_required": d.get("skills_required", []),
        "deliverables": d.get("deliverables", ""),
        "payment_type": d.get("payment_type", "one_time"),  # one_time/monthly/milestone
        "status": "open",  # open/bidding/assigned/in_progress/completed/cancelled/disputed
        "bids": [],
        "assigned_to": None,
        "result": None,
        "rating": None,
        "created_at": _now(),
        "updated_at": _now(),
        "expires_at": d.get("expires_at") or (datetime.now(TZ) + timedelta(days=30)).strftime("%Y-%m-%d")
    }
    if not job["title"]:
        return {"err": "title_required"}
    data["jobs"][job_id] = job
    data["next_id"] += 1
    with _lock:
        _save(data)
    return {"ok": True, "job_id": job_id, "job": _sanitize_job(job)}

def list_jobs(filters=None):
    """列出Jobs，支持过滤"""
    with _lock:
        data = _load()
    jobs = list(data["jobs"].values())
    if filters:
        status = filters.get("status")
        if status: jobs = [j for j in jobs if j["status"] == status]
        category = filters.get("category")
        if category: jobs = [j for j in jobs if j["category"] == category]
        employer = filters.get("employer")
        if employer: jobs = [j for j in jobs if j["employer"] == employer]
        skill = filters.get("skill")
        if skill: jobs = [j for j in jobs if skill in j.get("skills_required", [])]
    # 按创建时间倒序
    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return {"ok": True, "total": len(jobs), "jobs": [_sanitize_job(j) for j in jobs]}

def get_job(job_id):
    """获取Job详情"""
    with _lock:
        data = _load()
    job = data["jobs"].get(job_id)
    if not job: return {"err": "job_not_found"}
    return {"ok": True, "job": _sanitize_job(job)}

def update_job(employer_uid, job_id, d):
    """雇主更新Job"""
    with _lock:
        data = _load()
    job = data["jobs"].get(job_id)
    if not job: return {"err": "job_not_found"}
    if job["employer"] != employer_uid: return {"err": "not_employer"}
    if job["status"] not in ("open", "bidding"): return {"err": "job_not_editable"}
    for k in ("title", "description", "category", "budget_min", "budget_max",
              "deadline", "skills_required", "deliverables", "payment_type"):
        if k in d: job[k] = d[k]
    job["updated_at"] = _now()
    with _lock:
        _save(data)
    return {"ok": True, "job": _sanitize_job(job)}

def cancel_job(employer_uid, job_id):
    """雇主取消Job"""
    with _lock:
        data = _load()
    job = data["jobs"].get(job_id)
    if not job: return {"err": "job_not_found"}
    if job["employer"] != employer_uid: return {"err": "not_employer"}
    if job["status"] not in ("open", "bidding"): return {"err": "cannot_cancel"}
    job["status"] = "cancelled"
    job["updated_at"] = _now()
    with _lock:
        _save(data)
    return {"ok": True, "job_id": job_id}

# ── Bidding ──

def submit_bid(agent_uid, job_id, d):
    """Agent投标"""
    with _lock:
        data = _load()
    job = data["jobs"].get(job_id)
    if not job: return {"err": "job_not_found"}
    if job["status"] not in ("open", "bidding"): return {"err": "job_not_accepting_bids"}
    if job["employer"] == agent_uid: return {"err": "cannot_bid_own_job"}
    # 检查是否已投标
    for b in job["bids"]:
        if b["agent"] == agent_uid: return {"err": "already_bid"}
    bid = {
        "agent": agent_uid,
        "price": d.get("price"),              # 投标价格(CNY)
        "price_currency": d.get("price_currency", "CNY"),
        "eta": d.get("eta"),                  # 预计完成时间
        "proposal": d.get("proposal", ""),    # 投标方案描述
        "qualifications": d.get("qualifications", []),
        "portfolio": d.get("portfolio", []),
        "status": "pending",  # pending/accepted/rejected/withdrawn
        "created_at": _now()
    }
    if bid["price"] is None: return {"err": "price_required"}
    job["bids"].append(bid)
    job["status"] = "bidding"
    job["updated_at"] = _now()
    with _lock:
        _save(data)
    return {"ok": True, "job_id": job_id, "bid_count": len(job["bids"])}

def accept_bid(employer_uid, job_id, agent_uid):
    """雇主接受投标"""
    with _lock:
        data = _load()
    job = data["jobs"].get(job_id)
    if not job: return {"err": "job_not_found"}
    if job["employer"] != employer_uid: return {"err": "not_employer"}
    bid = None
    for b in job["bids"]:
        if b["agent"] == agent_uid:
            bid = b
            break
    if not bid: return {"err": "bid_not_found"}
    # 更新状态
    bid["status"] = "accepted"
    for b in job["bids"]:
        if b["agent"] != agent_uid and b["status"] == "pending":
            b["status"] = "rejected"
    job["assigned_to"] = agent_uid
    job["status"] = "assigned"
    job["agreed_price"] = bid["price"]
    job["updated_at"] = _now()
    with _lock:
        _save(data)
    return {"ok": True, "job_id": job_id, "assigned_to": agent_uid, "agreed_price": bid["price"]}

def withdraw_bid(agent_uid, job_id):
    """Agent撤回投标"""
    with _lock:
        data = _load()
    job = data["jobs"].get(job_id)
    if not job: return {"err": "job_not_found"}
    for b in job["bids"]:
        if b["agent"] == agent_uid and b["status"] == "pending":
            b["status"] = "withdrawn"
            job["updated_at"] = _now()
            with _lock: _save(data)
            return {"ok": True}
    return {"err": "bid_not_found_or_not_pending"}

# ── Execution & Completion ──

def start_job(agent_uid, job_id):
    """Agent开始执行Job"""
    with _lock:
        data = _load()
    job = data["jobs"].get(job_id)
    if not job: return {"err": "job_not_found"}
    if job["assigned_to"] != agent_uid: return {"err": "not_assigned_to_you"}
    if job["status"] != "assigned": return {"err": "job_not_in_assigned_state"}
    job["status"] = "in_progress"
    job["started_at"] = _now()
    job["updated_at"] = _now()
    with _lock:
        _save(data)
    return {"ok": True, "job_id": job_id}

def submit_result(agent_uid, job_id, d):
    """Agent提交Job结果"""
    with _lock:
        data = _load()
    job = data["jobs"].get(job_id)
    if not job: return {"err": "job_not_found"}
    if job["assigned_to"] != agent_uid: return {"err": "not_assigned_to_you"}
    if job["status"] != "in_progress": return {"err": "job_not_in_progress"}
    job["result"] = {
        "output": d.get("output", ""),
        "files": d.get("files", []),
        "notes": d.get("notes", ""),
        "submitted_at": _now()
    }
    job["status"] = "completed"
    job["completed_at"] = _now()
    job["updated_at"] = _now()
    with _lock:
        _save(data)
    return {"ok": True, "job_id": job_id}

def rate_job(employer_uid, job_id, d):
    """雇主评价Job"""
    with _lock:
        data = _load()
    job = data["jobs"].get(job_id)
    if not job: return {"err": "job_not_found"}
    if job["employer"] != employer_uid: return {"err": "not_employer"}
    if job["status"] != "completed": return {"err": "job_not_completed"}
    job["rating"] = {
        "score": d.get("score"),  # 1-5
        "review": d.get("review", ""),
        "rated_at": _now()
    }
    job["updated_at"] = _now()
    with _lock:
        _save(data)
    return {"ok": True, "job_id": job_id}

def dispute_job(uid, job_id, d):
    """发起争议"""
    with _lock:
        data = _load()
    job = data["jobs"].get(job_id)
    if not job: return {"err": "job_not_found"}
    if uid not in (job["employer"], job["assigned_to"]): return {"err": "not_involved"}
    job["status"] = "disputed"
    job["dispute"] = {
        "raised_by": uid,
        "reason": d.get("reason", ""),
        "description": d.get("description", ""),
        "raised_at": _now()
    }
    job["updated_at"] = _now()
    with _lock:
        _save(data)
    return {"ok": True, "job_id": job_id, "message": "Dispute raised. Platform will review."}

# ── Agent Stats ──

def agent_stats(agent_uid):
    """Agent在Job市场的统计"""
    with _lock:
        data = _load()
    completed = [j for j in data["jobs"].values() if j.get("assigned_to") == agent_uid and j["status"] == "completed"]
    active = [j for j in data["jobs"].values() if j.get("assigned_to") == agent_uid and j["status"] in ("assigned", "in_progress")]
    ratings = [j["rating"]["score"] for j in completed if j.get("rating") and j.get("rating", {}).get("score")]
    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else None
    total_earned = sum(j.get("agreed_price", 0) or 0 for j in completed)
    return {
        "ok": True, "agent": agent_uid,
        "completed_jobs": len(completed),
        "active_jobs": len(active),
        "avg_rating": avg_rating,
        "total_earned_cny": round(total_earned, 2)
    }

def _sanitize_job(job):
    """清理Job数据用于返回"""
    j = dict(job)
    # 不暴露其他agent的投标详情给非雇主
    return j
