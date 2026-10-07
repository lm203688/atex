"""FastAPI 主程序：注册/市场/参与/结算/排行榜/自动化/UGC/双 Agent/数据/合规 全部接口。

成熟度加固：
- 鉴权：参与/兑换/UGC/签到等写操作校验用户 token；admin 端点校验 x-admin-token。
- 安全响应头：CSP（script-src 用每请求 nonce，已去 unsafe-inline）/ nosniff / DENY。
- 限流与实时广播走 core.backplane：单实例进程内，配 REDIS_URL 则外置 Redis（可多实例）。
- 结算走 Oracle（core.oracle），数据导出匿名化（core.data_export）。
前端演示页见 static/index.html。启动：uvicorn main:app --port 8000
"""
import os
import json
import time
import secrets
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from db import init_db, get_conn
import db
from core import backplane
from core import points, markets
from core import settlement
from core import mall
from core import oracle
from core import data_export
from core import oracle_sources
from core import achievements
from core import tournaments
from core import tasks
from core import metrics
from core import comments
from core import notifications
from core import tiers
from core import rating
from core import external_ref
from core import forecast
from core import daily, seasons, cosmetics, adwatch, sponsors, vip, social, events
from mcp import server as mcp
from automation import scout, publish, moderation
from agents import support, ads, devboard
from agents import orchestrator as agent_orchestrator

DESCRIPTION = (
    "真测 Realcast —— 合规的积分制真实预测游戏社区（类 Polymarket，但不涉现金/加密货币）。\n\n"
    "合规四红线：积分只送不卖、不可用户间流通、不可回兑现金、彻底去加密货币。\n"
    "结算走平台奖励池（非赢家通吃），Oracle 权威源可插拔对接。"
)
def _seed_if_empty():
    """库为空时播种演示数据（仅在 SEED_ON_BOOT=1 时由 lifespan 调用）。

    seed.py 是模块级脚本，本身不具备幂等性——重复执行会撞用户名唯一约束。
    所以这里先判断库里是否已有用户，已有则直接跳过，保证重启/重部署不会
    重复播种。播种失败只打日志，绝不让应用起不来。
    """
    try:
        with get_conn() as conn:
            n = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        if n:
            return
        import seed  # noqa: F401  —— 模块级代码执行即完成播种
        print("[info] 演示数据已播种（SEED_ON_BOOT=1，库为空）")
    except Exception as e:
        print(f"[warn] 演示数据播种失败，不影响启动：{type(e).__name__}: {e}")


@asynccontextmanager
async def lifespan(app):
    """应用生命周期：启动时建库并拉起实时广播任务，关闭时收尾。

    正规通过 FastAPI(lifespan=...) 传入（v0.7.1），不再用
    `app.router.lifespan_context = ...` 猴子补丁——那种写法依赖 Starlette
    内部结构，升级即碎。
    """
    init_db()
    # 部署到演示/联调环境时（SEED_ON_BOOT=1）自动播种演示数据。
    # 正式运营环境不要开：演示市场与演示用户会混进真实数据。
    if os.environ.get("SEED_ON_BOOT") == "1":
        _seed_if_empty()
    tasks = [asyncio.create_task(_realtime_loop())]
    # 多实例时消费其它实例的广播；单实例（memory backplane）下本任务空转，开销可忽略
    tasks.append(asyncio.create_task(_listen_backplane()))
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


app = FastAPI(
    title="真测 Realcast (Points-based Prediction Community)",
    version="0.7.15",
    description=DESCRIPTION,
    docs_url="/docs",
    lifespan=lifespan,
)
STATIC = os.path.join(os.path.dirname(__file__), "static", "index.html")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "dev-admin-token")  # 演示用，生产请环境变量注入
# 生产门禁：生产环境(REALTCAST_PROD=1)若仍用默认弱口令，直接拒绝启动，避免全网可管理
if os.environ.get("REALTCAST_PROD") == "1" and ADMIN_TOKEN == "dev-admin-token":
    raise RuntimeError(
        "生产环境必须注入 ADMIN_TOKEN 环境变量，禁止以默认弱口令(dev-admin-token)启动")
# 生产部署允许的跨域来源（默认仅本地，生产请注入 CORS_ORIGINS）
_CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if o.strip()]
MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", "1048576"))  # 默认 1MB
# 数据产品访问钥匙（§5.3 P1 变现单点验证）：留空=演示开放；设了则需
# 请求头 x-data-key 匹配，便于后续接付费 B 端客户（无需改代码）。
DATA_API_KEY = os.environ.get("DATA_API_KEY", "")

# ---------------- 安全响应头 ----------------
@app.middleware("http")
async def security_headers(request: Request, call_next):
    # 每请求生成一次性 nonce：页面里的 <script> 带 nonce 才能执行，
    # 注入进来的 <script> 没有 nonce，浏览器直接拒执行。
    nonce = secrets.token_urlsafe(16)
    request.state.csp_nonce = nonce
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    # v0.7.5：style-src 也去掉 'unsafe-inline'，改为 nonce 白名单。
    # 199 处内联样式已收拢：191 静态→CSS 类（在 <style nonce> 块内），
    # 8 动态（含 ${...}）→ data-rs-style，由前端 MutationObserver 运行时
    # 应用到 element.style（运行时赋值不受 style-src 限制）。
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; "
        f"style-src 'self' 'nonce-{nonce}'; "
        f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'"
    )
    return resp

# ---------------- 轻量限流 ----------------
# v0.7.1：计数外置到 backplane。单实例走进程内 dict（行为不变）；
# 配了 REDIS_URL 就走 Redis，多实例时限额不会被实例数放大。
RATE_LIMIT = int(os.environ.get("RATE_LIMIT", "120"))  # 每 IP 每路径每 60s 上限

@app.middleware("http")
async def rate_limit(request: Request, call_next):
    ip = request.client.host if request.client else "x"
    key = f"{ip}:{request.url.path}"
    allowed, _remaining = await backplane.get_backplane().rate_check(
        key, RATE_LIMIT, window=60)
    if not allowed:
        return Response("Too Many Requests", status_code=429)
    return await call_next(request)

# ---------------- CORS（仅允许配置来源，默认 localhost） ----------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "x-admin-token", "Content-Type"],
)

# ---------------- 请求体大小限制 ----------------
@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    # GET/HEAD 无体；仅限制有 content-length 的写请求
    cl = request.headers.get("content-length")
    if cl and int(cl) > MAX_BODY_BYTES:
        return Response("Payload Too Large", status_code=413)
    return await call_next(request)

# ---------------- 健康检查 ----------------
@app.get("/api/health")
def api_health():
    with get_conn() as conn:
        counts = {}
        for t in ("users", "markets", "positions", "oracle_log", "disputes", "publish_queue"):
            try:
                counts[t] = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
            except Exception:
                counts[t] = None
    return {
        "status": "ok",
        "version": app.version,
        # memory=单实例进程内；redis=已外置，可多实例水平扩展
        "backplane": backplane.get_backplane().name,
        "oracle_sources": oracle_sources.list_sources(),
        "counts": counts,
    }

# ---------------- 鉴权助手 ----------------
def _auth_user(user_id: int, request: Request):
    """校验写操作的用户 token；不匹配即 401。"""
    auth = request.headers.get("Authorization", "")
    tok = auth.replace("Bearer ", "").strip()
    with get_conn() as conn:
        row = conn.execute("SELECT token FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or not row["token"] or not secrets.compare_digest(row["token"] or "", tok):
        raise HTTPException(401, "未授权：token 无效")
    return True

def _auth_admin(request: Request):
    if not secrets.compare_digest(request.headers.get("x-admin-token") or "", ADMIN_TOKEN):
        raise HTTPException(401, "未授权：需要 admin token")
    return True

def _require_data_key(request: Request):
    """数据产品访问门：DATA_API_KEY 留空则开放（演示/透明度），
    设了则要求请求头 x-data-key 恒定时间匹配，便于接付费 B 端。"""
    if not DATA_API_KEY:
        return True
    if not secrets.compare_digest(request.headers.get("x-data-key") or "", DATA_API_KEY):
        raise HTTPException(401, "未授权：需要有效的 data-api-key")
    return True



# ---------- 用户 / 积分 ----------
class RegReq(BaseModel):
    username: str
    phone: str = None
    invite_code: str = None
    age_confirmed: bool = False
    password: str = None

@app.post("/api/register")
def api_register(req: RegReq):
    if not req.age_confirmed:
        raise HTTPException(400, "需确认已满18周岁并同意用户协议")
    if not req.password or len(req.password) < 6:
        raise HTTPException(400, "请设置至少 6 位登录密码（账户安全必填）")
    try:
        res = points.register(req.username, req.phone, req.invite_code, req.password)
    except Exception as e:
        raise HTTPException(400, str(e))
    uid = res["user_id"]
    # 回访：通知邀请人「好友通过你的邀请注册」
    if res.get("inviter_id"):
        inv = points.profile(res["inviter_id"])
        if inv:
            notifications.notify(
                res["inviter_id"], "invite",
                "好友通过你的邀请注册 🎉",
                f"用户「{req.username}」使用你的邀请码完成注册，奖励已发放。",
                ref_type="user", ref_id=uid)
    return {"user_id": uid, "balance": points.balance(uid)}

@app.post("/api/signin")
def api_signin(user_id: int, request: Request):
    _auth_user(user_id, request)
    r = points.daily_signin(user_id)
    return {"reward": r, "balance": points.balance(user_id)}

class LoginReq(BaseModel):
    username: str
    password: str = None

@app.post("/api/login")
def api_login(req: LoginReq):
    p = points.login(req.username, req.password)
    if not p:
        raise HTTPException(401, "用户名或密码错误")
    p["token"] = points.ensure_token(p["id"])
    p["balance"] = points.balance(p["id"])
    return p

@app.get("/api/users/{user_id}")
def api_profile(user_id: int, request: Request):
    _auth_user(user_id, request)
    p = points.profile(user_id)
    if not p:
        raise HTTPException(404, "用户不存在")
    p["balance"] = points.balance(user_id)
    return p

@app.get("/api/users/{user_id}/points")
def api_points(user_id: int):
    return {"user_id": user_id, "balance": points.balance(user_id)}

@app.get("/api/users/{user_id}/predictions")
def api_predictions(user_id: int, request: Request):
    _auth_user(user_id, request)
    return markets.my_predictions(user_id)

@app.get("/api/users/{user_id}/accuracy")
def api_accuracy(user_id: int, request: Request):
    _auth_user(user_id, request)
    return markets.user_accuracy(user_id)


@app.get("/api/users/{user_id}/calibration")
def api_calibration(user_id: int, request: Request):
    """可靠性图数据：下注概率 vs 实际命中率 + 过度自信/保守标签（P4-A1）。"""
    _auth_user(user_id, request)
    return markets.user_calibration(user_id)

@app.get("/api/users/{user_id}/badges")
def api_badges(user_id: int, request: Request):
    _auth_user(user_id, request)
    return achievements.list_badges(user_id)

@app.post("/api/users/{user_id}/badges/evaluate")
def api_badges_eval(user_id: int, request: Request):
    _auth_user(user_id, request)
    return achievements.evaluate(user_id)

@app.get("/api/users/{user_id}/streak")
def api_prediction_streak(user_id: int, request: Request):
    """每日预测连胜与今日奖励（Manifold 范式）。"""
    _auth_user(user_id, request)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT predict_streak, last_predict_date FROM users WHERE id=?", (user_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "用户不存在")
    # 计算今日是否已领取（用于前端提示）
    from db import today_str
    today = today_str()
    claimed_today = (row["last_predict_date"] == today)
    return {
        "predict_streak": row["predict_streak"] or 0,
        "last_predict_date": row["last_predict_date"],
        "claimed_today": claimed_today,
        "next_reward": min(5 + (max(row["predict_streak"] or 0, 0)) * 2, 25),
    }


@app.get("/api/users/{user_id}/comments")
def api_my_comments(user_id: int, request: Request, limit: int = 50):
    """我的评论（含审核中状态，消除误伤导致的「发不出」困惑）。"""
    _auth_user(user_id, request)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT c.id, c.market_id, c.body, c.status, c.audit_note, c.created_at, "
            "m.title AS market_title FROM comments c JOIN markets m ON m.id=c.market_id "
            "WHERE c.user_id=? ORDER BY c.id DESC LIMIT ?", (user_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/users/{user_id}/notifications")
def api_notifications(user_id: int, request: Request, limit: int = 30, only_unread: bool = False):
    """站内通知列表（回访系统）。"""
    _auth_user(user_id, request)
    return notifications.list_for(user_id, limit=limit, only_unread=only_unread)


@app.get("/api/users/{user_id}/notif_unread")
def api_notif_unread(user_id: int, request: Request):
    _auth_user(user_id, request)
    return {"unread": notifications.unread_count(user_id)}


@app.get("/api/users/{user_id}/tier")
def api_tier(user_id: int, request: Request):
    """声誉即特权：当前等级、已解锁特权、下一级进度。

    P4-A3：把「高且自信的技能分」折算进声誉（skill_bonus_reputation），
    让「准」的用户更快解锁 UGC 发起权等特权——声誉由「活跃/投入」与「技能」共同决定。
    同时返回技能认证状态供前端展示。
    """
    _auth_user(user_id, request)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT reputation, skill_rating, skill_rd FROM users WHERE id=?", (user_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "用户不存在")
    eff = tiers.skill_bonus_reputation(row["reputation"], row["skill_rating"], row["skill_rd"])
    tier = tiers.rep_tier(eff)
    tier["mall_quota_multiplier"] = tiers.mall_quota_multiplier(eff)
    tier["can_create_market"] = tiers.can_create_market(eff)
    tier["skill_rating"] = round(float(row["skill_rating"] or 1500.0), 1)
    tier["skill_confidence"] = round(rating.confidence(row["skill_rd"] or 350.0), 3)
    tier["skill_certified"] = tiers.skill_certified(row["skill_rating"], row["skill_rd"])
    return tier


# ---------------- P5 游戏化：每日习惯 / 转盘 / 赛季 / 外观 ----------------
@app.get("/api/daily/status")
def api_daily_status(user_id: int, request: Request):
    """游戏中心一屏状态：签到连胜、转盘余次、库存、每日一猜进度。"""
    _auth_user(user_id, request)
    return daily.daily_status(user_id)


@app.post("/api/daily/checkin")
def api_daily_checkin(user_id: int, request: Request):
    """每日签到（连续递增、断签重置）。"""
    _auth_user(user_id, request)
    return daily.checkin(user_id)


@app.get("/api/daily/picks")
def api_daily_picks(user_id: int, request: Request, limit: int = 3):
    """每日一猜：今日精选市场 + 是否已完成。"""
    _auth_user(user_id, request)
    picks = daily.get_daily_picks(limit)
    st = daily.daily_status(user_id)
    return {"picks": picks, "claimed": st["daily_picks_claimed"] if st else False}


@app.post("/api/daily/picks/claim")
def api_daily_picks_claim(user_id: int, request: Request):
    """完成每日一猜领奖（今日已下注任意市场则发奖，每日限一次）。"""
    _auth_user(user_id, request)
    return daily.claim_daily_picks(user_id)


@app.post("/api/lucky/spin")
def api_lucky_spin(user_id: int, request: Request):
    """幸运转盘：每日免费 1 次，固定概率可变奖励。"""
    _auth_user(user_id, request)
    return daily.spin(user_id)


@app.get("/api/season")
def api_season(user_id: int, request: Request):
    """赛季通行证进度与免费轨奖励状态。"""
    _auth_user(user_id, request)
    return seasons.season_state(user_id)


@app.post("/api/season/claim")
def api_season_claim(user_id: int, request: Request, level: int):
    """领取赛季通行证某级免费轨奖励。"""
    _auth_user(user_id, request)
    return seasons.claim(user_id, level)


@app.get("/api/lootbox/config")
def api_lootbox_config():
    """外观抽卡概率公示（无需鉴权，合规透明度）。默认关闭返回 enabled=False。"""
    return cosmetics.lootbox_config()


@app.post("/api/lootbox/pull")
def api_lootbox_pull(user_id: int, request: Request):
    """外观抽卡（消耗抽奖券）。默认 LOOTBOX_ENABLED=0 时不开放。"""
    _auth_user(user_id, request)
    return cosmetics.pull(user_id)


# ---------------- P5-δ 变现层：L1 激励视频 / L2 品牌众测 / L3 VIP 订阅（不卖分） ----------------
@app.get("/api/ads/offer")
def api_ads_offer(user_id: int, request: Request):
    """当前可看的激励视频广告（占位，真实接入时替换为广告 SDK）。"""
    _auth_user(user_id, request)
    return adwatch.ad_offer()


@app.get("/api/ads/status")
def api_ads_status(user_id: int, request: Request):
    _auth_user(user_id, request)
    return adwatch.ad_status(user_id)


@app.post("/api/ads/watch")
def api_ads_watch(user_id: int, request: Request, ad_id: str = None):
    """观看激励视频，领平台积分（每日上限）。"""
    _auth_user(user_id, request)
    return adwatch.watch(user_id, ad_id)


@app.get("/api/sponsors")
def api_sponsors(limit: int = 30):
    """品牌众测市场列表（含赞助商）。"""
    return sponsors.list_sponsored(limit)


@app.post("/api/markets/{market_id}/sponsor")
def api_market_sponsor(market_id: int, brand_name: str, brand_note: str = None,
                       request: Request = None):
    """运营/品牌：赞助一个市场（admin）。"""
    _auth_admin(request)
    return sponsors.sponsor_market(market_id, brand_name, brand_note)


@app.get("/api/sponsors/{market_id}/report")
def api_sponsor_report(market_id: int, request: Request = None):
    """赞助商品牌侧匿名情绪报告（admin）。无 PII，纯聚合。"""
    _auth_admin(request)
    rep = sponsors.brand_report(market_id)
    if rep is None:
        raise HTTPException(404, "市场不存在")
    return rep


@app.get("/api/vip/tiers")
def api_vip_tiers():
    """VIP 订阅档位与权益说明（无需鉴权，透明度）。默认关闭返回 enabled=False。"""
    return vip.tiers()


@app.post("/api/vip/subscribe")
def api_vip_subscribe(user_id: int, tier_key: str, request: Request):
    """订阅某档 VIP。默认 VIP_PAYMENTS_ENABLED=0 关闭，仅暴露模型不接入支付。"""
    _auth_user(user_id, request)
    return vip.subscribe(user_id, tier_key)


# ---------------- P5-E Live-Ops 限时活动（默认开放，纯内容+积分激励，无合规风险） ----------------
class EventCreate(BaseModel):
    title: str
    theme: str = "custom"
    description: str = ""
    start_at: str          # 'YYYY-MM-DD HH:MM:SS'（UTC，按库内字符串约定）
    end_at: str
    reward_points: int = 0
    linked_markets: str = ""   # 逗号分隔的市场 id
    banner: str = "🎉"


@app.get("/api/events")
def api_events():
    """限时活动列表（公开），状态由时间推导。"""
    return events.list_events()


@app.get("/api/events/{event_id}")
def api_event_detail(event_id: int):
    """活动详情 + 关联市场（只读，不参与结算）。"""
    d = events.detail(event_id)
    if d is None:
        raise HTTPException(404, "活动不存在")
    return d


@app.post("/api/events/{event_id}/participate")
def api_event_participate(event_id: int, user_id: int, request: Request):
    """参与活动领平台积分（每用户每活动限一次，积分只送不卖）。"""
    _auth_user(user_id, request)
    return events.participate(user_id, event_id)


@app.post("/api/admin/events")
def api_admin_event_create(req: EventCreate, request: Request):
    """运营：创建限时活动（admin）。"""
    _auth_admin(request)
    return events.create_event(req.title, req.theme, req.description,
                               req.start_at, req.end_at, req.reward_points,
                               req.linked_markets, req.banner)


@app.post("/api/admin/events/{event_id}/close")
def api_admin_event_close(event_id: int, request: Request):
    """运营：关闭（结束）一个活动（admin）。"""
    _auth_admin(request)
    return events.close_event(event_id)


# ---------------- P5-γ 社交：战队 + 好友 PK（积分内闭环，零合规风险） ----------------
class SquadCreate(BaseModel):
    user_id: int
    name: str
    tag: str = None

@app.post("/api/squads")
def api_squad_create(req: SquadCreate, request: Request):
    """创建战队（创建者自动成为 owner，获「战队队长」勋章）。"""
    _auth_user(req.user_id, request)
    return social.create_squad(req.user_id, req.name, req.tag)

@app.post("/api/squads/{squad_id}/join")
def api_squad_join(squad_id: int, user_id: int, request: Request):
    _auth_user(user_id, request)
    return social.join_squad(user_id, squad_id)

@app.post("/api/squads/{squad_id}/leave")
def api_squad_leave(squad_id: int, user_id: int, request: Request):
    _auth_user(user_id, request)
    return social.leave_squad(user_id, squad_id)

@app.get("/api/squads/mine")
def api_squad_mine(user_id: int, request: Request):
    _auth_user(user_id, request)
    return social.my_squad(user_id) or {"joined": False}

@app.get("/api/squads")
def api_squad_list(limit: int = 30):
    """全部战队（含成员数、荣誉分）。"""
    return social.list_squads(limit)

@app.get("/api/squads/leaderboard")
def api_squad_lb(limit: int = 20):
    """战队荣誉榜。"""
    return social.squad_leaderboard(limit)

class PkReq(BaseModel):
    challenger_id: int
    opponent_id: int = None
    opponent_name: str = None
    market_id: int

@app.post("/api/pk/challenge")
def api_pk_challenge(req: PkReq, request: Request):
    """发起好友 PK：比同一市场的预测准度。opponent 可传 id 或 username。"""
    _auth_user(req.challenger_id, request)
    opp = req.opponent_id
    if opp is None and req.opponent_name:
        opp = social._user_id_by_name(req.opponent_name)
        if opp is None:
            raise HTTPException(404, "对手用户不存在")
    if opp is None:
        raise HTTPException(400, "需提供 opponent_id 或 opponent_name")
    return social.pk_challenge(req.challenger_id, opp, req.market_id)

@app.get("/api/pk/mine")
def api_pk_mine(user_id: int, request: Request):
    _auth_user(user_id, request)
    return social.pk_challenges_for(user_id)

@app.get("/api/pk/leaderboard")
def api_pk_lb(limit: int = 20):
    """PK 胜场榜（纯荣誉）。"""
    return social.pk_leaderboard(limit)


@app.post("/api/users/{user_id}/notifications/read")
def api_notif_read(user_id: int, request: Request, notif_id: int = None):
    """标记通知已读（指定 notif_id 单条，否则全部）。"""
    _auth_user(user_id, request)
    return notifications.mark_read(user_id, notif_id)

@app.get("/api/feed")
def api_feed(user_id: int = None):
    """首页聚合：未登录/无偏好→热门；登录→个性化 for-you + 热门 + 即将截止。"""
    if user_id:
        foryou = markets.for_you(user_id, limit=6)
        ad_market = foryou
    else:
        foryou = []
        ad_market = markets.trending(6)
    with get_conn() as conn:
        ad = conn.execute(
            "SELECT advertiser, position, industry FROM ad_orders "
            "WHERE status='confirmed' ORDER BY confirmed_at DESC LIMIT 1"
        ).fetchone()
    banner = dict(ad) if ad else {"advertiser": "品牌合作招募", "position": "首页banner", "industry": "招商中"}
    return {
        "for_you": ad_market,
        "hot_markets": markets.trending(6),
        "ending_soon": markets.ending_soon(6),
        "banner": banner,
    }

# ---------- 市场 ----------
@app.get("/api/markets")
def api_markets(status: str = None, category: str = None, q: str = None,
                sort: str = "newest", limit: int = 50):
    return markets.query_markets(status, category, q, sort, limit)


@app.get("/api/categories")
def api_categories():
    return markets.categories()

@app.get("/api/markets/trending")
def api_trending(limit: int = 8):
    """热门市场（参与人数最多，对标 Polymarket/Kalshi 热门榜）。"""
    return markets.trending(limit)

@app.get("/api/markets/ending")
def api_ending(limit: int = 8):
    """即将截止（制造每日回访紧迫感）。"""
    return markets.ending_soon(limit)

@app.get("/api/markets/{market_id}")
def api_market(market_id: int):
    m = markets.get_market(market_id)
    if not m:
        raise HTTPException(404, "市场不存在")
    return m

@app.get("/api/markets/{market_id}/external_ref")
def api_market_external_ref(market_id: int):
    """外部公开概率参考（只读）。

    停用或未配置 EXTERNAL_REF_ENDPOINT 时返回 enabled=false，前端不应展示该模块。
    返回带明确声明：外部概率**不具结算效力**。
    """
    return {
        "market_id": market_id,
        "enabled": external_ref.enabled(),
        "reference": external_ref.get(market_id),
        "bias": external_ref.bias(market_id),
        "disclaimer": "外部概率仅供参考与内容分析，不参与本平台任何结算",
    }


@app.post("/api/admin/markets/{market_id}/external_ref/refresh")
def api_admin_refresh_external_ref(market_id: int, request: Request):
    """手动触发一次外部参考概率拉取（管理员）。"""
    _auth_admin(request)
    ref = external_ref.refresh(market_id)
    if not ref:
        raise HTTPException(400, "外部参考源未配置或拉取失败")
    return {"market_id": market_id, "reference": ref}


@app.get("/api/markets/{market_id}/history")
def api_market_history(market_id: int):
    """概率时间序列（驱动趋势图 / sparkline）。"""
    return markets.probability_history(market_id)

@app.get("/api/markets/{market_id}/comments")
def api_market_comments(market_id: int):
    return comments.list_for(market_id)


@app.get("/api/markets/{market_id}/resolution")
def api_market_resolution(market_id: int):
    """公开结算依据（透明化，无需登录）：Oracle 来源 + 结算标准 + 争议与社区票型。"""
    res = oracle.public_resolution(market_id)
    if not res:
        raise HTTPException(404, "市场不存在")
    return res


@app.get("/api/markets/{market_id}/forecast")
def api_market_forecast(market_id: int, weighted: int = 1):
    """聚合预测层（P4-A2）：统一返回分类/数值共识，并附群体 vs 外部参考的偏差分析。

    外部概率仅作参考与偏差研究，绝不参与结算（合规隔离由 core/external_ref.py 守护）。
    """
    fc = forecast.aggregate(market_id, weighted=bool(weighted))
    if not fc:
        raise HTTPException(404, "市场不存在")
    fc["deviation_vs_external"] = forecast.deviation_vs_external(market_id)
    return fc


@app.get("/api/markets/{market_id}/disputes")
def api_market_disputes(market_id: int):
    """某市场的争议列表（含社区票型）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, user_id, status, reason, created_at FROM disputes "
            "WHERE market_id=? ORDER BY id DESC", (market_id,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d.update(oracle.dispute_vote_summary(r["id"]))
        out.append(d)
    return out


class DisputeVoteReq(BaseModel):
    user_id: int
    vote: str


@app.post("/api/disputes/{dispute_id}/vote")
def api_dispute_vote(dispute_id: int, req: DisputeVoteReq, request: Request):
    """社区对争议投票（声誉越高权重越大：铂金×2）。"""
    _auth_user(req.user_id, request)
    try:
        weight = tiers.dispute_vote_weight(
            (points.profile(req.user_id) or {}).get("reputation", 0) or 0)
        return oracle.vote_dispute(dispute_id, req.user_id, req.vote, weight)
    except Exception as e:
        raise HTTPException(400, str(e))

class CommentReq(BaseModel):
    user_id: int
    body: str
    parent_id: int = None

@app.post("/api/markets/{market_id}/comments")
def api_market_comment_post(market_id: int, req: CommentReq, request: Request):
    _auth_user(req.user_id, request)
    try:
        res = comments.add(market_id, req.user_id, req.body, req.parent_id)
    except Exception as e:
        raise HTTPException(400, str(e))
    # 回访：回复他人评论时通知原评论作者（不通知自己）
    if req.parent_id:
        try:
            with get_conn() as conn:
                prow = conn.execute(
                    "SELECT user_id FROM comments WHERE id=?", (req.parent_id,)
                ).fetchone()
            if prow and prow["user_id"] != req.user_id:
                author = points.profile(req.user_id) or {}
                notifications.notify(
                    prow["user_id"], "reply",
                    "有人回复了你的评论 💬",
                    f"用户「{author.get('username','某人')}」在你的评论下发表了看法。",
                    ref_type="comment", ref_id=req.parent_id)
        except Exception:
            pass
    return {"comment_id": res["id"], "status": res["status"],
            "pending_review": res["status"] == "review", "ok": True}

class ReportReq(BaseModel):
    user_id: int = None

@app.post("/api/comments/{comment_id}/report")
def api_comment_report(comment_id: int, req: ReportReq):
    """用户举报评论（合规兜底：达阈值自动下线并生成人工工单）。"""
    try:
        return comments.report(comment_id, req.user_id)
    except Exception as e:
        raise HTTPException(400, str(e))

@app.get("/api/admin/comments/pending")
def api_admin_comments_pending(request: Request, limit: int = 50):
    _auth_admin(request)
    return comments.pending(limit)

class CommentReviewReq(BaseModel):
    approve: bool
    note: str = None

@app.post("/api/admin/comments/{comment_id}/review")
def api_admin_comment_review(comment_id: int, req: CommentReviewReq, request: Request):
    """人工兜底：放行或下架待审评论。"""
    _auth_admin(request)
    try:
        res = comments.review(comment_id, req.approve, req.note)
    except Exception as e:
        raise HTTPException(400, str(e))
    # 回访：把处理结果通知评论作者
    try:
        with get_conn() as conn:
            crow = conn.execute(
                "SELECT user_id, body FROM comments WHERE id=?", (comment_id,)
            ).fetchone()
        if crow:
            title = "你的评论已通过审核 ✅" if req.approve else "你的评论未通过审核"
            body = (f"「{(crow['body'] or '')[:40]}…」"
                    f"{'已通过审核并公开展示。' if req.approve else '因内容合规原因未能展示。'}")
            notifications.notify(crow["user_id"], "comment_review", title, body,
                                 ref_type="comment", ref_id=comment_id)
    except Exception:
        pass
    return res

class PartReq(BaseModel):
    user_id: int
    option: int
    stake: int

@app.post("/api/markets/{market_id}/participate")
def api_participate(market_id: int, req: PartReq, request: Request):
    _auth_user(req.user_id, request)
    try:
        m = markets.participate(req.user_id, market_id, req.option, req.stake)
    except Exception as e:
        raise HTTPException(400, str(e))
    mark_markets_dirty()  # 概率变化，触发实时广播
    return m

class NumPartReq(BaseModel):
    user_id: int
    value: float
    stake: int
    sigma: float = None

@app.post("/api/markets/{market_id}/participate_numeric")
def api_participate_numeric(market_id: int, req: NumPartReq, request: Request):
    """数值市场参与（v0.7.8）：预测一个数值，可声明不确定性 sigma。

    sigma 越小=越自信。CRPS 是严格适当评分规则，所以"自认很准就把 sigma 报小"
    只有在真的准时才划算——瞎报窄区间会被重罚。
    """
    _auth_user(req.user_id, request)
    try:
        m = markets.participate_numeric(req.user_id, market_id, req.value,
                                        req.stake, req.sigma)
    except Exception as e:
        raise HTTPException(400, str(e))
    mark_markets_dirty()
    return m

@app.post("/api/markets/{market_id}/dispute")
def api_dispute(market_id: int, user_id: int, reason: str, request: Request):
    _auth_user(user_id, request)
    try:
        did = oracle.create_dispute(market_id, user_id, reason)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"dispute_id": did, "status": "open"}

@app.get("/api/leaderboard")
def api_leaderboard(limit: int = 20, sort: str = "points", ranked_only: int = 0):
    """排行榜。

    sort=points（默认，向后兼容）：按积分降序——榜是「活跃度/资产」榜。
    sort=skill：按**保守下界** (skill_rating - 1.96·skill_rd) 降序——技能榜。
       用区间下沿而非单点分，是因为新用户 RD 极大，2 注全中的单点分虚高，
       下沿会把他自然排到后面；随样本积累 RD 收窄，真实水平才浮上来。
    ranked_only=1：过滤掉 provisional（样本不足/久未参与）用户，只要正式榜位。

    每个条目附带 skill_low/skill_high（95% 区间）与 skill_confidence，
    前端可据此把低置信度用户渲染为「待定」，避免用户被噪声排名误导。
    """
    z = rating.CONSERVATIVE_Z
    order = ("(u.skill_rating - ? * u.skill_rd) DESC"
             if sort == "skill" else "u.points_balance DESC")
    # 候选集取 3 倍，过滤 provisional 后再截断，避免 limit 被临时分占满
    fetch = limit * 3 if (ranked_only or sort == "skill") else limit
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT u.id, u.username, u.points_balance, u.reputation, u.streak, "
            "u.skill_rating, u.skill_rd, u.skill_sigma, u.skill_updated_at, "
            "(SELECT COUNT(*) FROM positions p JOIN markets m ON m.id=p.market_id "
            " WHERE p.user_id=u.id AND m.status='settled' AND m.resolution IS NOT NULL) AS resolved, "
            "(SELECT COUNT(*) FROM positions p JOIN markets m ON m.id=p.market_id "
            " WHERE p.user_id=u.id AND m.status='settled' AND m.resolution IS NOT NULL "
            " AND p.option_index=m.resolution) AS correct "
            f"FROM users u ORDER BY {order} LIMIT ?", (z, fetch) if sort == "skill" else (fetch,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        resolved = int(d.pop("resolved", 0) or 0)
        correct = int(d.pop("correct", 0) or 0)
        rating_v = d.pop("skill_rating", None)
        rd_v = d.pop("skill_rd", None)
        sigma_v = d.pop("skill_sigma", None)
        updated = d.pop("skill_updated_at", None)
        # 久未参与 → RD 回升（不确定性回到真实水平）
        if updated:
            try:
                days = (db.utc_now() - db.parse_iso(updated)).days
            except Exception:
                days = 0
            rd_v = rating.rd_decay(rd_v or rating.DEFAULT_RD,
                                   sigma_v or rating.DEFAULT_SIGMA, days)
        rating_v = float(rating_v or rating.DEFAULT_RATING)
        rd_v = float(rd_v or rating.DEFAULT_RD)
        low, high = rating.rating_interval(rating_v, rd_v, z)
        d["pro"] = (resolved >= 20 and (correct / resolved) >= 0.70) if resolved else False
        d["accuracy"] = round(correct / resolved, 3) if resolved else None
        # 保守命中率：小样本下比裸命中率稳健（2/2 的下界远低于 100%）
        d["accuracy_conservative"] = (round(rating.wilson_lower_bound(correct, resolved, z), 3)
                                      if resolved else None)
        d["skill_rating"] = round(rating_v, 1)
        d["skill_rd"] = round(rd_v, 1)
        d["skill_low"] = round(low, 1)
        d["skill_high"] = round(high, 1)
        d["skill_confidence"] = round(rating.confidence(rd_v), 3)
        d["provisional"] = rating.is_provisional(resolved, rd_v)
        d["resolved_count"] = resolved
        # 声誉等级标识（公开排行榜展示地位，替代金钱排名）
        d["tier"] = tiers.rep_tier(d.get("reputation", 0))["tier_name"]
        out.append(d)
    if ranked_only:
        out = [x for x in out if not x["provisional"]]
    return out[:limit]

# ---------- 竞猜联赛（Tournaments，Metaculus 式组合智力赛）----------
@app.get("/api/tournaments")
def api_tournaments(status: str = None):
    return tournaments.list_tournaments(status)


@app.get("/api/tournaments/{tid}")
def api_tournament(tid: int):
    t = tournaments.get_tournament(tid)
    if not t:
        raise HTTPException(404, "联赛不存在")
    return t


@app.get("/api/tournaments/{tid}/leaderboard")
def api_tournament_lb(tid: int):
    return tournaments.leaderboard(tid)


@app.post("/api/tournaments/{tid}/join")
def api_tournament_join(tid: int, user_id: int, request: Request):
    _auth_user(user_id, request)
    try:
        return tournaments.join(tid, user_id)
    except Exception as e:
        raise HTTPException(400, str(e))


class TournamentCreate(BaseModel):
    title: str
    description: str = ""
    category: str = "综合"
    entry_fee: int = 0
    prize_pool: int = 0
    ends_at: str = None


@app.post("/api/admin/tournaments")
def api_admin_tournament(req: TournamentCreate, request: Request):
    _auth_admin(request)
    tid = tournaments.create_tournament(req.title, req.description, req.category,
                                         req.entry_fee, req.prize_pool, req.ends_at)
    return {"tournament_id": tid}


@app.post("/api/admin/tournaments/{tid}/markets/{mid}")
def api_admin_tournament_add(tid: int, mid: int, request: Request):
    _auth_admin(request)
    tournaments.add_market(tid, mid)
    return {"ok": True}


@app.post("/api/admin/tournaments/{tid}/close")
def api_admin_tournament_close(tid: int, request: Request):
    _auth_admin(request)
    try:
        return tournaments.close_tournament(tid)
    except Exception as e:
        raise HTTPException(400, str(e))


# ---------- 每日任务中心（留存）----------
@app.get("/api/users/{user_id}/tasks")
def api_tasks(user_id: int, request: Request):
    _auth_user(user_id, request)
    return tasks.get_tasks(user_id)


@app.post("/api/users/{user_id}/tasks/claim")
def api_task_claim(user_id: int, task_id: str, request: Request):
    _auth_user(user_id, request)
    try:
        return tasks.claim(user_id, task_id)
    except Exception as e:
        raise HTTPException(400, str(e))


# ---------- 投资人 KPI（单位经济 + 数据资产真实性）----------
@app.get("/api/admin/metrics")
def api_metrics(request: Request):
    _auth_admin(request)
    return metrics.kpi()


@app.get("/api/admin/metrics/retention")
def api_metrics_retention(request: Request):
    _auth_admin(request)
    return metrics.retention()


# ---------- 结算（Oracle）----------
class SettleReq(BaseModel):
    market_id: int
    winning_option: int
    source: str = None
    note: str = None

@app.post("/api/admin/settle")
def api_settle(req: SettleReq, request: Request):
    _auth_admin(request)
    try:
        res = oracle.set_result(req.market_id, req.winning_option, req.source, req.note)
    except Exception as e:
        raise HTTPException(400, str(e))
    social.pk_resolve(req.market_id)  # P5-γ：结算连带结算该市场的好友 PK
    # 回访：市场结算后通知所有参与者（提升回访率，纯站内）
    try:
        with get_conn() as conn:
            mkt = conn.execute(
                "SELECT title, resolution FROM markets WHERE id=?", (req.market_id,)
            ).fetchone()
            parts = conn.execute(
                "SELECT DISTINCT user_id FROM positions WHERE market_id=? AND user_id IS NOT NULL",
                (req.market_id,)).fetchall()
        if mkt:
            for p in parts:
                uname = points.profile(p["user_id"]) or {}
                notifications.notify(
                    p["user_id"], "market_resolved",
                    "你参与的市场已结算 🏁",
                    f"「{mkt['title']}」结果已公布，快去看看你的战绩吧。",
                    ref_type="market", ref_id=req.market_id)
    except Exception:
        pass
    mark_markets_dirty()  # 市场状态变化，触发实时广播
    return res

class SettleNumericReq(BaseModel):
    market_id: int
    value: float              # 真实数值结果（如票房 44.2 亿）
    source: str = None
    note: str = None

@app.post("/api/admin/settle_numeric")
def api_settle_numeric(req: SettleNumericReq, request: Request):
    """数值市场结算（v0.7.8）：写入真实数值并按 CRPS 技能分给付。"""
    _auth_admin(request)
    try:
        res = oracle.set_numeric_result(req.market_id, req.value, req.source, req.note)
    except Exception as e:
        raise HTTPException(400, str(e))
    social.pk_resolve(req.market_id)  # P5-γ：结算连带结算该市场的好友 PK
    try:
        with get_conn() as conn:
            mkt = conn.execute(
                "SELECT title, resolution_value, numeric_unit FROM markets WHERE id=?",
                (req.market_id,)).fetchone()
            parts = conn.execute(
                "SELECT DISTINCT user_id FROM positions WHERE market_id=? "
                "AND user_id IS NOT NULL", (req.market_id,)).fetchall()
        if mkt:
            unit = mkt["numeric_unit"] or ""
            for p in parts:
                notifications.notify(
                    p["user_id"], "market_resolved",
                    "你参与的市场已结算 🏁",
                    f"「{mkt['title']}」结果为 {mkt['resolution_value']}{unit}，"
                    f"快去看看你的预测有多准。",
                    ref_type="market", ref_id=req.market_id)
    except Exception:
        pass
    mark_markets_dirty()
    return res

@app.post("/api/oracle/auto")
def api_oracle_auto(request: Request):
    _auth_admin(request)
    return {"settled": oracle.auto_settle_due()}

@app.post("/api/oracle/resolve-due")
def api_oracle_resolve_due(request: Request):
    """带人工兜底路由的自动结算（P4-B2）。默认 ORACLE_AUTO_SETTLE 关闭时，
    任何有 Oracle 结果的市场都转人工复核，不会自动结算（守住人工兜底红线）。"""
    _auth_admin(request)
    res = oracle.resolve_due_with_human_fallback()
    if res.get("resolved"):
        mark_markets_dirty()
    return res

@app.post("/api/oracle/resolve-from-sources")
def api_oracle_resolve_from_sources(request: Request):
    """直接咨询已接入 Oracle 权威源（manifest/ESPN/HTTP）结算到期市场（无人工兜底，运维手动触发）。

    与 /api/oracle/resolve-due（带人工兜底、默认不自动结算）互补：本端点适用于已确认源
    可靠、需立即落地的场景（如演示、单源确定性结果）。返回 {resolved, tried}。
    """
    _auth_admin(request)
    resolved, tried = oracle.resolve_due_from_sources()
    return {"resolved": resolved, "tried": tried}

@app.get("/api/admin/disputes")
def api_disputes(request: Request, status: str = None):
    _auth_admin(request)
    return oracle.list_disputes(status)


@app.post("/api/automation/ai_predict")
def api_ai_predict(request: Request, limit: int = 20):
    """受控 AI 预测一轮（P4-B1）。默认 AI_PREDICT_ENABLED 未开启时返回 ran=False 安全 no-op。

    合规：AI 预测走与普通用户完全相同的 participate 路径，仅作参与者，全部标注 is_ai=1。
    """
    _auth_admin(request)
    from automation import ai_predictor
    return ai_predictor.predict_once(limit=limit)


@app.post("/mcp")
async def api_mcp(request: Request):
    """MCP 兼容 JSON-RPC 2.0 端点（P4-B3）：initialize / tools/list / tools/call。

    只读工具免鉴权；submit_prediction 需在 Authorization: Bearer <user_token> 中携带有效 token。
    纯标准库实现，无外部依赖；外部概率仅只读返回，绝不写入结算。
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
            status_code=400,
        )
    if not isinstance(payload, (dict, list)):
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}},
            status_code=400,
        )
    auth = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    return mcp.handle(payload, auth_token=auth or None)

@app.post("/api/admin/disputes/{dispute_id}/resolve")
def api_resolve(dispute_id: int, request: Request, action: str, note: str = None):
    _auth_admin(request)
    try:
        return oracle.resolve_dispute(dispute_id, action, note)
    except Exception as e:
        raise HTTPException(400, str(e))

# ---------- 自动化选题 / 发布 ----------
@app.post("/api/automation/scout")
def api_scout(request: Request):
    _auth_admin(request)
    return scout.run_scout()

@app.post("/api/automation/publish")
def api_publish(request: Request):
    _auth_admin(request)
    return {"published": publish.publish_auto()}

@app.get("/api/publish_queue")
def api_queue(route: str = None, status: str = None):
    return publish.list_queue(route, status)

@app.post("/api/admin/approve")
def api_approve(queue_id: int, request: Request):
    _auth_admin(request)
    mid = publish.admin_approve(queue_id)
    if mid is None:
        raise HTTPException(400, "该选题不可放行（非 review 或已处理）")
    return {"market_id": mid}

class AdminCreateMarket(BaseModel):
    title: str
    description: str = ""
    category: str = "未分类"
    options: list = []
    oracle_source: str = ""
    closes_at: str = None
    # v0.7.8 连续型市场：market_type='numeric' 时用区间预测，options 留空
    market_type: str = "categorical"
    numeric_lower: float = None
    numeric_upper: float = None
    numeric_unit: str = None

@app.post("/api/admin/markets")
def api_admin_create_market(req: AdminCreateMarket, request: Request):
    _auth_admin(request)
    if req.market_type == "categorical" and (not req.options or len(req.options) < 2):
        raise HTTPException(400, "分类市场至少需要两个选项")
    try:
        mid = markets.create_market(
            req.title, req.description, req.category, req.category,
            req.options or [], req.oracle_source, req.closes_at,
            market_type=req.market_type, numeric_lower=req.numeric_lower,
            numeric_upper=req.numeric_upper, numeric_unit=req.numeric_unit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"market_id": mid}

# ---------- UGC：用户发起事件 ----------
class UgcSubmit(BaseModel):
    creator: int
    title: str
    description: str = ""
    category: str = None
    options: list
    oracle_source: str = ""
    settlement_criteria: str = ""
    closes_at: str = None

@app.post("/api/ugc/submit")
def api_ugc_submit(req: UgcSubmit, request: Request):
    _auth_user(req.creator, request)
    if not req.options or len(req.options) < 2:
        raise HTTPException(400, "至少需要两个选项")
    # 声誉门槛（白银 50）：tiers.can_create_market 早已有，但端点没调用，
    # 等于「文档说有门槛、代码没门槛」。v0.7.1 补上——供给放开靠降门槛，
    # 不靠取消门槛，否则垃圾市场会淹没四道闸审核。
    with get_conn() as conn:
        row = conn.execute(
            "SELECT reputation FROM users WHERE id=?", (req.creator,)).fetchone()
    rep = (row["reputation"] if row else 0) or 0
    if not tiers.can_create_market(rep):
        need = tiers.TIERS[1]["min_rep"]
        raise HTTPException(
            403, f"发起事件需达到白银预测者（声誉 {need}，当前 {round(rep, 1)}）："
                 f"先多做几次准确预测攒声誉，既是防垃圾，也让发起的事件更有分量")
    sub = {
        "title": req.title, "description": req.description,
        "category": req.category, "options": req.options,
        "oracle_source": req.oracle_source,
        "settlement_criteria": req.settlement_criteria,
        "creator": req.creator,
    }
    verdict = moderation.moderate_submission(sub)
    draft = {
        "title": req.title, "description": req.description,
        "category": verdict["category"], "whitelist_tag": verdict["whitelist_tag"],
        "options": req.options, "oracle_source": req.oracle_source,
        "settlement_criteria": req.settlement_criteria, "closes_at": req.closes_at,
        "creator": req.creator, "source": "user",
    }
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO publish_queue (title, draft_json, sensitivity, route, status) "
            "VALUES (?,?,?,?,?)",
            (req.title, json.dumps(draft, ensure_ascii=False),
             verdict["whitelist_tag"], verdict["route"], "pending"),
        )
        conn.commit()
        qid = cur.lastrowid
    return {"queue_id": qid, "route": verdict["route"], "reasons": verdict["reasons"]}

# ---------- Agent: 客服 ----------
class SupportReq(BaseModel):
    message: str
    user: str = None

@app.post("/api/agents/support")
def api_support(req: SupportReq):
    return support.handle(req.message, req.user)

# ---------- Agent: 广告 ----------
class AdInquire(BaseModel):
    advertiser: str
    industry: str
    ad_format: str
    position: str
    budget: int = None

@app.post("/api/agents/ads/inquire")
def api_ad_inquire(req: AdInquire):
    return ads.inquire(req.advertiser, req.industry, req.ad_format, req.position, req.budget)

class AdConfirm(BaseModel):
    order_id: int
    method: str = "预付"

@app.post("/api/agents/ads/confirm")
def api_ad_confirm(req: AdConfirm):
    return ads.confirm(req.order_id, req.method)

@app.get("/api/agents/ads/statement/{order_id}")
def api_ad_statement(order_id: int):
    s = ads.statement(order_id)
    if not s:
        raise HTTPException(404, "订单不存在")
    return s

@app.get("/api/agents/ads/orders")
def api_ad_orders(status: str = None):
    return ads.list_orders(status)

# ---------- dev 看板 ----------
@app.get("/api/dev/tickets")
def api_tickets(status: str = None, source: str = None):
    return devboard.list_tickets(status, source)

@app.post("/api/dev/tickets/{ticket_id}/close")
def api_close(ticket_id: int, note: str = None):
    return {"ok": devboard.close_ticket(ticket_id, note)}

# ---------- Agent Orchestrator（v0.5.0 能力提升）----------
class OrchestrateReq(BaseModel):
    goal: str
    input: dict = {}
    agent: str = None

@app.post("/api/agents/orchestrate")
def api_orchestrate(req: OrchestrateReq):
    """自然语言提交 Agent 任务，返回 task_id。参考 AutoAgent 零代码编排。"""
    task_id = agent_orchestrator.submit(req.goal, req.input, req.agent)
    return {"task_id": task_id, "status": "submitted"}

@app.get("/api/agents/tasks/{task_id}")
def api_task_status(task_id: str):
    return agent_orchestrator.status(task_id)

@app.get("/api/admin/agents/dashboard")
def api_agent_dashboard(request: Request):
    _auth_admin(request)
    return agent_orchestrator.dashboard()

@app.post("/api/admin/agents/recover")
def api_agent_recover(request: Request):
    _auth_admin(request)
    recovered = agent_orchestrator.recover()
    return {"recovered": recovered}

class AgentRuleReq(BaseModel):
    name: str
    text: str

@app.get("/api/admin/agents/rules")
def api_list_rules(request: Request, domain: str = None):
    _auth_admin(request)
    return agent_orchestrator.rules.list_rules(domain)

@app.post("/api/admin/agents/rules")
def api_create_rule(req: AgentRuleReq, request: Request):
    _auth_admin(request)
    rule = agent_orchestrator.rules.parse_rule(req.text)
    rid = agent_orchestrator.rules.save_rule(req.name, rule)
    return {"rule_id": rid, "rule": rule}

# ---------- 数据产品（匿名化 B2B）----------
@app.get("/api/data/sentiment")
def api_sentiment(request: Request):
    """匿名群体情绪指数（无 PII，可公开展示透明度，供 B 端订阅）。
    §5.3 P1 变现单点验证：DATA_API_KEY 留空则开放，设了需 x-data-key。"""
    _require_data_key(request)
    return data_export.sentiment_index()

@app.get("/api/data/export")
def api_export(request: Request, kind: str = "category"):
    """匿名逐市场 / 品类聚合 CSV（无 PII），供 B 端数据产品分发。
    与 sentiment 端点一致：DATA_API_KEY 留空则开放，设了需 x-data-key。"""
    _require_data_key(request)
    if kind == "markets":
        return PlainTextResponse(data_export.export_markets_csv(),
                                 media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=markets.csv"})
    return PlainTextResponse(data_export.export_category_csv(),
                             media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=category.csv"})

# ---------- 合规页 ----------
@app.get("/api/compliance/tos")
def api_tos():
    return PlainTextResponse(COMPLIANCE_TOS, media_type="text/plain; charset=utf-8")

@app.get("/api/compliance/privacy")
def api_privacy():
    return PlainTextResponse(COMPLIANCE_PRIVACY, media_type="text/plain; charset=utf-8")

# ---------- 积分商城（单向兑换，红线：只送不卖/不可流通/不可回兑）----------
class MallItem(BaseModel):
    name: str
    cost: int
    description: str = ""
    category: str = "虚拟权益"
    stock: int = 9999
    item_type: str = "virtual"

@app.post("/api/mall/items")
def api_mall_add(item: MallItem, request: Request):
    _auth_admin(request)
    return {"item_id": mall.add_item(item.name, item.cost, item.description,
                                     item.category, item.stock, item.item_type)}

@app.get("/api/mall/items")
def api_mall_list():
    return mall.list_items()

@app.post("/api/mall/redeem")
def api_mall_redeem(user_id: int, item_id: int, request: Request):
    _auth_user(user_id, request)
    try:
        return mall.redeem(user_id, item_id)
    except Exception as e:
        raise HTTPException(400, str(e))

@app.get("/api/mall/redeem/{user_id}")
def api_mall_my(user_id: int):
    return mall.my_redemptions(user_id)

# ---------- 实时概率推送（WebSocket，让价格发现"活"起来）----------
class RealtimeManager:
    def __init__(self):
        self.connections = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.add(ws)

    def disconnect(self, ws: WebSocket):
        self.connections.discard(ws)

    async def broadcast(self, payload: dict, _echo: bool = False):
        """向本实例连接推送；并（可选）投递到 backplane 供其它实例转发。

        `_echo=True` 表示这条消息来自别的实例，只在本实例落地，不再回灌
        backplane，否则会形成实例间无限回声。
        """
        dead = set()
        for ws in list(self.connections):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.connections.discard(ws)
        if not _echo:
            await backplane.get_backplane().publish(
                MARKETS_CHANNEL, {"origin": backplane.INSTANCE_ID, "payload": payload})


async def _listen_backplane():
    """消费其它实例广播的市场快照，转发给本实例的连接。"""
    bp = backplane.get_backplane()
    try:
        async for msg in bp.subscribe(MARKETS_CHANNEL):
            if not isinstance(msg, dict):
                continue
            if msg.get("origin") == backplane.INSTANCE_ID:
                continue  # 自己发的，本实例已推过
            payload = msg.get("payload")
            if isinstance(payload, dict):
                await manager.broadcast(payload, _echo=True)
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


MARKETS_CHANNEL = "realcast:markets"
manager = RealtimeManager()

# 脏标记：仅在有下注/结算等市场状态变化时重算并广播（v0.7.0，避免无变化也每 5s 全量重算）
_MARKET_DIRTY = True
_LAST_SNAPSHOT = {}


def mark_markets_dirty():
    global _MARKET_DIRTY
    _MARKET_DIRTY = True


def _market_snapshot():
    """开放市场的声誉加权概率快照（供 WS 广播，无 PII）。去掉 50 上限，覆盖全部进行中市场。"""
    ms = markets.list_markets("open", limit=100000)
    return {m["id"]: m["probabilities"] for m in ms}


def _diff_snapshot(new):
    """与上一帧比较，只返回变化的市场，降低 payload。首帧返回全部。"""
    global _LAST_SNAPSHOT
    if not _LAST_SNAPSHOT:
        _LAST_SNAPSHOT = new
        return new
    changed = {mid: probs for mid, probs in new.items() if _LAST_SNAPSHOT.get(mid) != probs}
    _LAST_SNAPSHOT = new
    return changed


async def broadcast_snapshot():
    global _MARKET_DIRTY
    if not _MARKET_DIRTY:
        return
    try:
        new = _market_snapshot()
        changed = _diff_snapshot(new)
        if changed:
            await manager.broadcast({
                "type": "probabilities",
                "ts": int(time.time()),
                "markets": changed,
            })
        _MARKET_DIRTY = False
    except Exception:
        pass


@app.websocket("/ws/markets")
async def ws_markets(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_json({
            "type": "probabilities",
            "ts": int(time.time()),
            "markets": _market_snapshot(),
        })  # 连接即推一帧（全量）
        while True:
            await ws.receive_text()  # 维持连接；真实推送由后台任务驱动
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)


async def _realtime_loop():
    while True:
        try:
            await broadcast_snapshot()
        except Exception:
            pass
        await asyncio.sleep(5)


# ---------- 前端 ----------
_INDEX_CACHE = {"mtime": 0.0, "body": ""}


def _render_index(nonce: str):
    """读取 index.html 并把一次性 nonce 注入 <style> 与 <script> 标签。

    CSP 已去掉 script-src/style-src 'unsafe-inline'：不带 nonce 的内联脚本/样式
    浏览器不会执行/生效。按 mtime 缓存，避免每个请求都读盘。
    """
    try:
        mt = os.path.getmtime(STATIC)
    except OSError:
        return None
    if _INDEX_CACHE["mtime"] != mt or not _INDEX_CACHE["body"]:
        with open(STATIC, encoding="utf-8") as f:
            _INDEX_CACHE["body"] = f.read()
        _INDEX_CACHE["mtime"] = mt
    body = _INDEX_CACHE["body"]
    body = body.replace("<style>", f'<style nonce="{nonce}">', 1)
    body = body.replace("<script>", f'<script nonce="{nonce}">', 1)
    return body


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    nonce = getattr(request.state, "csp_nonce", None) or secrets.token_urlsafe(16)
    html = _render_index(nonce)
    if html is None:
        raise HTTPException(404, "index.html not found")
    return HTMLResponse(html)


COMPLIANCE_TOS = """《用户协议》（摘要）
1. 真测 Realcast 为「积分制真实预测游戏」，积分仅限平台内参与预测与兑换，不可交易、不可回兑现金、不可转让。
2. 用户须已满 18 周岁，承诺不参与任何政治选举、敏感公共事件及违法违规内容的预测。
3. 预测结果以平台接入的权威 Oracle 来源为准；对结算有异议可通过争议通道申请复核。
4. 用户发起事件须经 AI 分级审核；含诱导赌博话术、主权红线或不可结算内容将被驳回。
5. 平台有权对刷量、作弊、欺诈行为封禁账号并回收积分。
6. 本平台不涉及任何形式的加密货币、虚拟货币交易与现金赌博。
（完整条款以正式发布版本为准，上线前须经律所合规审查。）"""

COMPLIANCE_PRIVACY = """《隐私政策》（摘要）
1. 我们收集手机号（可选）、登录态 token、预测行为与积分账本，用于提供服务与防刷。
2. 我们绝不出售个人身份信息。对外提供的是**聚合、匿名**的群体情绪数据（PIPL 合规）。
3. 你有权查询、更正自己的账户信息，并注销账号。
4. 数据存储于本地关系库，遵循最小必要原则。
（完整政策以正式发布版本为准。）"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
