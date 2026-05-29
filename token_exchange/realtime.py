#!/usr/bin/env python3
"""ATEX WebSocket Real-time Communication — Agent间实时通信
支持：Job通知、投标提醒、交易通知、系统广播
"""
import json, os, time, threading, asyncio, hashlib
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TZ = timezone(timedelta(hours=8))

def _now(): return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

# ── Simple WebSocket-like notification system (HTTP long-poll fallback) ──
# Full WebSocket requires async server; this provides compatible HTTP-based real-time

NOTIF_FILE = os.path.join(BASE, "data", "notifications.json")
_notif_lock = threading.RLock()
_subscribers = {}  # uid -> [callback_url]
_sub_lock = threading.Lock()

def _load_notifications():
    if os.path.exists(NOTIF_FILE):
        with open(NOTIF_FILE) as f: return json.load(f)
    return {"notifications": {}, "next_id": 1}

def _save_notifications(data):
    os.makedirs(os.path.dirname(NOTIF_FILE), exist_ok=True)
    with open(NOTIF_FILE, "w") as f: json.dump(data, f, ensure_ascii=False, indent=2)

# ── Notification API ──

def send_notification(recipient_uid, notif_type, data_dict):
    """发送通知给指定用户"""
    with _notif_lock:
        notifs = _load_notifications()
    notif_id = f"n_{notifs['next_id']:06d}"
    notif = {
        "id": notif_id,
        "recipient": recipient_uid,
        "type": notif_type,  # job_bid/job_accepted/job_completed/trade/service/report/system
        "data": data_dict,
        "read": False,
        "created_at": _now()
    }
    notifs.setdefault("notifications", {}).setdefault(recipient_uid, []).append(notif)
    # 保留最近100条
    if len(notifs["notifications"][recipient_uid]) > 100:
        notifs["notifications"][recipient_uid] = notifs["notifications"][recipient_uid][-100:]
    notifs["next_id"] += 1
    with _notif_lock:
        _save_notifications(notifs)
    # 尝试推送给订阅者
    _try_push(recipient_uid, notif)
    return {"ok": True, "notification_id": notif_id}

def get_notifications(uid, unread_only=False, limit=50):
    """获取用户通知"""
    with _notif_lock:
        notifs = _load_notifications()
    user_notifs = notifs.get("notifications", {}).get(uid, [])
    if unread_only:
        user_notifs = [n for n in user_notifs if not n.get("read")]
    user_notifs.sort(key=lambda n: n.get("created_at", ""), reverse=True)
    return {"ok": True, "total": len(user_notifs), "notifications": user_notifs[:limit]}

def mark_read(uid, notif_ids=None):
    """标记通知已读"""
    with _notif_lock:
        notifs = _load_notifications()
    user_notifs = notifs.get("notifications", {}).get(uid, [])
    count = 0
    for n in user_notifs:
        if notif_ids is None or n["id"] in notif_ids:
            if not n.get("read"):
                n["read"] = True
                count += 1
    with _notif_lock:
        _save_notifications(notifs)
    return {"ok": True, "marked_read": count}

def subscribe(uid, callback_url):
    """订阅实时通知（Webhook方式）"""
    with _sub_lock:
        _subscribers[uid] = callback_url
    return {"ok": True, "uid": uid, "callback": callback_url}

def unsubscribe(uid):
    """取消订阅"""
    with _sub_lock:
        _subscribers.pop(uid, None)
    return {"ok": True}

def _try_push(uid, notif):
    """尝试推送通知到订阅者"""
    with _sub_lock:
        callback = _subscribers.get(uid)
    if not callback: return
    # 异步推送（不阻塞主流程）
    threading.Thread(target=_do_push, args=(callback, notif), daemon=True).start()

def _do_push(callback_url, notif):
    """执行Webhook推送"""
    try:
        import urllib.request
        payload = json.dumps(notif, ensure_ascii=False).encode()
        req = urllib.request.Request(callback_url, data=payload,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # 推送失败不影响主流程

# ── WebSocket Upgrade Handler (for future full WS support) ──

WS_HANDSHAKE_RESPONSE = """HTTP/1.1 101 Switching Protocols\r
Upgrade: websocket\r
Connection: Upgrade\r
Sec-WebSocket-Accept: {accept_key}\r
\r\n"""

def is_websocket_upgrade(headers):
    """检查是否为WebSocket升级请求"""
    return headers.get("Upgrade", "").lower() == "websocket"

def generate_accept_key(key):
    """生成WebSocket accept key"""
    import base64, hashlib
    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    sha1 = hashlib.sha1((key + GUID).encode()).digest()
    return base64.b64encode(sha1).decode()

# ── SSE (Server-Sent Events) stream endpoint ──
# 作为WebSocket的轻量替代，支持HTTP长连接推送

def sse_events(uid, last_id=None):
    """生成SSE事件流（用于HTTP长连接）"""
    with _notif_lock:
        notifs = _load_notifications()
    user_notifs = notifs.get("notifications", {}).get(uid, [])
    if last_id:
        # 只返回last_id之后的通知
        found = False
        result = []
        for n in reversed(user_notifs):
            if n["id"] == last_id:
                found = True
                break
            result.append(n)
        if found:
            user_notifs = list(reversed(result))
    events = []
    for n in user_notifs:
        if not n.get("read"):
            events.append(f"id: {n['id']}\nevent: {n['type']}\ndata: {json.dumps(n, ensure_ascii=False)}\n\n")
    return "".join(events) if events else ": heartbeat\n\n"
