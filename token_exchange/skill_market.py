#!/usr/bin/env python3
"""ATEX Skill File Marketplace — Agent技能文件交易市场
对标Moltplace/ClawMart：Agent发布/购买/交易Skill文件(.md/.json/.yaml)
"""
import json, os, time, threading, hashlib
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TZ = timezone(timedelta(hours=8))
SKILLS_FILE = os.path.join(BASE, "data", "skills.json")
SKILLS_DIR = os.path.join(BASE, "data", "skill_files")
_lock = threading.RLock()

def _now(): return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def _load():
    if os.path.exists(SKILLS_FILE):
        with open(SKILLS_FILE) as f: return json.load(f)
    return {"skills": {}, "next_id": 1, "purchases": []}

def _save(data):
    os.makedirs(os.path.dirname(SKILLS_FILE), exist_ok=True)
    with open(SKILLS_FILE, "w") as f: json.dump(data, f, ensure_ascii=False, indent=2)

# ── Skill CRUD ──

def publish_skill(author_uid, d):
    """Agent发布Skill文件"""
    with _lock:
        data = _load()
    skill_id = f"skill_{data['next_id']:04d}"
    content = d.get("content", "")
    if not content: return {"err": "content_required"}
    if len(content) > 100000: return {"err": "content_too_large", "max": 100000}
    # 内容哈希（防重复）
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
    # 检查重复
    for s in data["skills"].values():
        if s.get("content_hash") == content_hash and s["author"] == author_uid:
            return {"err": "duplicate_skill", "existing_id": s["id"]}
    # 保存文件
    os.makedirs(SKILLS_DIR, exist_ok=True)
    file_path = os.path.join(SKILLS_DIR, f"{skill_id}.md")
    with open(file_path, "w") as f: f.write(content)

    skill = {
        "id": skill_id,
        "author": author_uid,
        "name": d.get("name", skill_id),
        "description": d.get("description", ""),
        "category": d.get("category", "general"),  # coding/research/writing/data/automation/agent/general
        "tags": d.get("tags", []),
        "format": d.get("format", "markdown"),  # markdown/json/yaml
        "price_cny": d.get("price_cny", 0),     # 0=免费
        "price_atex": d.get("price_atex", 0),   # ATEX定价
        "content_hash": content_hash,
        "file_path": file_path,
        "version": d.get("version", "1.0"),
        "compatibility": d.get("compatibility", []),  # openclaw/crewai/langchain/autogen/generic
        "downloads": 0,
        "rating": None,
        "ratings": [],
        "status": "active",  # active/hidden/removed
        "created_at": _now(),
        "updated_at": _now()
    }
    data["skills"][skill_id] = skill
    data["next_id"] += 1
    with _lock:
        _save(data)
    return {"ok": True, "skill_id": skill_id, "content_hash": content_hash}

def list_skills(filters=None):
    """列出Skills"""
    with _lock:
        data = _load()
    skills = list(data["skills"].values())
    if filters:
        category = filters.get("category")
        if category: skills = [s for s in skills if s["category"] == category]
        author = filters.get("author")
        if author: skills = [s for s in skills if s["author"] == author]
        tag = filters.get("tag")
        if tag: skills = [s for s in skills if tag in s.get("tags", [])]
        compat = filters.get("compatibility")
        if compat: skills = [s for s in skills if compat in s.get("compatibility", [])]
        free_only = filters.get("free_only")
        if free_only: skills = [s for s in skills if s["price_cny"] == 0 and s["price_atex"] == 0]
        status = filters.get("status", "active")
        if status: skills = [s for s in skills if s["status"] == status]
    else:
        skills = [s for s in skills if s["status"] == "active"]
    skills.sort(key=lambda s: s.get("downloads", 0), reverse=True)
    # 不返回file_path和content_hash
    return {"ok": True, "total": len(skills), "skills": [_sanitize_skill(s) for s in skills]}

def get_skill(skill_id):
    """获取Skill详情"""
    with _lock:
        data = _load()
    skill = data["skills"].get(skill_id)
    if not skill: return {"err": "skill_not_found"}
    return {"ok": True, "skill": _sanitize_skill(skill)}

def buy_skill(buyer_uid, skill_id):
    """购买Skill文件"""
    with _lock:
        data = _load()
    skill = data["skills"].get(skill_id)
    if not skill: return {"err": "skill_not_found"}
    if skill["status"] != "active": return {"err": "skill_not_available"}
    if skill["author"] == buyer_uid: return {"err": "cannot_buy_own_skill"}
    # 检查是否已购买
    for p in data["purchases"]:
        if p["buyer"] == buyer_uid and p["skill_id"] == skill_id:
            # 已购买，返回内容
            content = _read_skill_file(skill_id)
            return {"ok": True, "skill_id": skill_id, "content": content, "already_purchased": True}
    # 读取内容
    content = _read_skill_file(skill_id)
    if content is None: return {"err": "skill_file_not_found"}
    # 记录购买
    purchase = {
        "buyer": buyer_uid,
        "skill_id": skill_id,
        "price_cny": skill["price_cny"],
        "price_atex": skill["price_atex"],
        "author": skill["author"],
        "purchased_at": _now()
    }
    data["purchases"].append(purchase)
    skill["downloads"] = skill.get("downloads", 0) + 1
    with _lock:
        _save(data)
    return {"ok": True, "skill_id": skill_id, "content": content, "price_cny": skill["price_cny"], "price_atex": skill["price_atex"]}

def rate_skill(rater_uid, skill_id, d):
    """评价Skill"""
    with _lock:
        data = _load()
    skill = data["skills"].get(skill_id)
    if not skill: return {"err": "skill_not_found"}
    # 检查是否购买过
    purchased = any(p["buyer"] == rater_uid and p["skill_id"] == skill_id for p in data["purchases"])
    if not purchased: return {"err": "must_purchase_to_rate"}
    score = d.get("score")
    if not score or score < 1 or score > 5: return {"err": "score_must_be_1_to_5"}
    # 检查是否已评价
    for r in skill.get("ratings", []):
        if r["rater"] == rater_uid: return {"err": "already_rated"}
    rating = {"rater": rater_uid, "score": score, "review": d.get("review", ""), "rated_at": _now()}
    skill.setdefault("ratings", []).append(rating)
    scores = [r["score"] for r in skill["ratings"]]
    skill["rating"] = round(sum(scores) / len(scores), 1)
    skill["updated_at"] = _now()
    with _lock:
        _save(data)
    return {"ok": True, "skill_id": skill_id, "avg_rating": skill["rating"]}

def update_skill(author_uid, skill_id, d):
    """更新Skill"""
    with _lock:
        data = _load()
    skill = data["skills"].get(skill_id)
    if not skill: return {"err": "skill_not_found"}
    if skill["author"] != author_uid: return {"err": "not_author"}
    if "content" in d:
        content = d["content"]
        if len(content) > 100000: return {"err": "content_too_large"}
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        file_path = os.path.join(SKILLS_DIR, f"{skill_id}.md")
        with open(file_path, "w") as f: f.write(content)
        skill["content_hash"] = content_hash
    for k in ("name", "description", "category", "tags", "price_cny", "price_atex", "version", "compatibility"):
        if k in d: skill[k] = d[k]
    skill["updated_at"] = _now()
    with _lock:
        _save(data)
    return {"ok": True, "skill_id": skill_id}

def remove_skill(author_uid, skill_id):
    """下架Skill"""
    with _lock:
        data = _load()
    skill = data["skills"].get(skill_id)
    if not skill: return {"err": "skill_not_found"}
    if skill["author"] != author_uid: return {"err": "not_author"}
    skill["status"] = "removed"
    skill["updated_at"] = _now()
    with _lock:
        _save(data)
    return {"ok": True, "skill_id": skill_id}

def _read_skill_file(skill_id):
    """读取Skill文件内容"""
    file_path = os.path.join(SKILLS_DIR, f"{skill_id}.md")
    if not os.path.exists(file_path): return None
    with open(file_path) as f: return f.read()

def _sanitize_skill(skill):
    """清理Skill数据"""
    s = dict(skill)
    s.pop("file_path", None)
    s.pop("content_hash", None)
    return s
