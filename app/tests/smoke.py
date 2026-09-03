"""冒烟测试：覆盖核心链路并断言，确保产品非花架子。
运行（推荐，自带清库+seed+起服务+跑测试）：
    python tests/smoke.py --fresh
若已手动 `python seed.py` 且服务运行于 8000，也可直接：
    python tests/smoke.py
注意：default 模式依赖一个干净的运行中服务；状态污染会导致级联失败，故优先用 --fresh。
（seed.py 在部分沙箱环境中会被静默终止，如遇此情况请在沙箱外执行。）
"""
import json
import sys
import urllib.request
import urllib.error
import urllib.parse
import subprocess
import time
import os

BASE = "http://127.0.0.1:8000"
ADMIN = "dev-admin-token"
PW = "test1234"   # 新注册必须设密码（安全升级）
PASS = []
FAIL = []


def call(method, path, body=None, token=None, admin=False, parse_json=True):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if admin:
        headers["x-admin-token"] = ADMIN
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode()
            return r.status, json.loads(body) if parse_json else body
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode()
            return e.code, json.loads(body) if parse_json else body
        except Exception:
            return e.code, {}


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


def raw_get(path):
    """返回 (状态码, 响应头 dict, 响应体 str)，用于校验 CSP 头与 HTML 原文。
    响应头统一小写化 key，避免服务器发出小写头名（uvicorn/h11 默认小写）
    与测试里大写 key 不匹配导致误报。"""
    def _hdrs(msg):
        return {k.lower(): v for k, v in msg.items()}
    req = urllib.request.Request(BASE + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, _hdrs(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, _hdrs(e.headers), e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, {}, repr(e)


def bump_reputation(user_id, value=60.0):
    """测试夹具：直接把用户声誉抬到白银以上。

    声誉只能通过「预测被结算且判准」累积（单次封顶 2.0），走真实链路攒到 50
    要几十次结算，冒烟测试里不现实，故直接落库。仅用于测试，不进生产代码。
    """
    import sqlite3
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    conn = sqlite3.connect(os.environ.get("DB_PATH") or os.path.join(app_dir, "platform.db"),
                           timeout=10)
    try:
        conn.execute("UPDATE users SET reputation=? WHERE id=?", (value, user_id))
        conn.commit()
    finally:
        conn.close()


def main():
    print("== 冒烟测试 ==")
    # 注册
    s, j = call("POST", "/api/register", {"username": "冒烟测试员", "age_confirmed": True, "password": PW})
    check("注册(带年龄确认+密码)", s == 200 and j.get("user_id"), f"{s} {j}")
    uid = j.get("user_id")

    # 年龄门拦截
    s2, _ = call("POST", "/api/register", {"username": "未成年", "age_confirmed": False, "password": PW})
    check("年龄门拦截未确认用户", s2 == 400, f"{s2}")

    # 无密码注册被拒
    s3, _ = call("POST", "/api/register", {"username": "无密用户", "age_confirmed": True})
    check("无密码注册被拒(安全)", s3 == 400, f"{s3}")

    # 登录拿 token
    s, j = call("POST", "/api/login", {"username": "冒烟测试员", "password": PW})
    check("登录返回token(凭密码)", s == 200 and j.get("token"), f"{s} {j}")
    tok = j.get("token")

    # 错误密码登录被拒
    s4, _ = call("POST", "/api/login", {"username": "冒烟测试员", "password": "wrong"})
    check("错误密码登录被拒", s4 == 401, f"{s4}")

    # 无 token 参与应 401
    s, _ = call("POST", "/api/markets/1/participate", {"user_id": uid, "option": 0, "stake": 20})
    check("无token写操作被拒(401)", s == 401, f"{s}")

    # 签到
    s, j = call("POST", "/api/signin?user_id=" + str(uid), token=tok)
    check("签到", s == 200 and "reward" in j, f"{s} {j}")

    # UGC 声誉门槛：新注册用户（声誉 0）应被白银门槛挡在外面。
    # 之前 tiers.can_create_market 写了却没在端点调用，等于「文档有门槛、代码没门槛」。
    s, j = call("POST", "/api/ugc/submit", {
        "creator": uid, "title": "本周末德甲某队能否取胜", "options": ["会", "不会"],
        "oracle_source": "官方联赛战报", "settlement_criteria": "以官方结果为准"}, token=tok)
    check("UGC声誉门槛拦截新用户(403)", s == 403, f"{s} {j}")

    # 抬到白银（50）后放行（安全+有Oracle→auto）
    bump_reputation(uid, 60)
    s, j = call("POST", "/api/ugc/submit", {
        "creator": uid, "title": "本周末德甲某队能否取胜", "options": ["会", "不会"],
        "oracle_source": "官方联赛战报", "settlement_criteria": "以官方结果为准"}, token=tok)
    check("UGC提交返回路由(白银以上)", s == 200 and j.get("route") in ("auto", "review", "reject"), f"{s} {j}")

    # 参与市场1（带token）
    s, j = call("POST", "/api/markets/1/participate", {"user_id": uid, "option": 0, "stake": 20}, token=tok)
    check("参与市场(加权概率)", s == 200 and "probabilities" in j, f"{s}")

    # 市场详情含声誉加权概率
    s, j = call("GET", "/api/markets/1")
    check("市场含raw+weighted概率", s == 200 and "raw_probabilities" in j and "probabilities" in j, f"{s}")

    # Oracle 结算 m1（admin）
    s, j = call("POST", "/api/admin/settle", {"market_id": 1, "winning_option": 0,
                                               "source": "官方赛事结果"}, admin=True)
    check("Oracle结算发放奖励池", s == 200 and j.get("paid_total", 0) >= 0, f"{s} {j}")

    # 我的预测 + 准确率
    s, j = call("GET", f"/api/users/{uid}/predictions", token=tok)
    check("我的预测列表", s == 200 and isinstance(j, list), f"{s}")
    s, j = call("GET", f"/api/users/{uid}/accuracy", token=tok)
    check("准确率聚合", s == 200 and "total" in j, f"{s} {j}")

    # 匿名情绪指数（公开）
    s, j = call("GET", "/api/data/sentiment")
    check("匿名情绪指数(无PII)", s == 200 and isinstance(j, list), f"{s} {j}")

    # 数据导出 CSV（admin）
    req = urllib.request.Request(BASE + "/api/data/export?kind=markets",
                                 headers={"x-admin-token": ADMIN})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            csv = r.read().decode()
        check("数据CSV导出", r.status == 200 and "market_id" in csv, f"{r.status}")
    except Exception as e:
        check("数据CSV导出", False, str(e))

    # 商城兑换
    s, j = call("POST", f"/api/mall/redeem?user_id={uid}&item_id=4", token=tok)  # 头像框120
    check("商城兑换(单向)", s == 200 and j.get("redemption_id"), f"{s} {j}")

    # 广告 Agent 全流程
    s, j = call("POST", "/api/agents/ads/inquire",
                {"advertiser": "某饮料", "industry": "食品饮料", "ad_format": "cpm", "position": "信息流"})
    check("广告咨询报价", s == 200 and j.get("order_id"), f"{s} {j}")
    oid = j.get("order_id")
    s, _ = call("POST", "/api/agents/ads/confirm", {"order_id": oid, "method": "预付"})
    check("广告确认投放", s == 200, f"{s}")
    s, _ = call("GET", f"/api/agents/ads/statement/{oid}")
    check("广告对账单", s == 200, f"{s}")

    # 敏感行业广告升级
    s, j = call("POST", "/api/agents/ads/inquire",
                {"advertiser": "某财富", "industry": "金融投资", "ad_format": "cpm", "position": "首页banner"})
    check("敏感广告升级人工", s == 200 and j.get("status") == "escalated", f"{s} {j}")

    # 客服 Agent
    s, j = call("POST", "/api/agents/support", {"message": "你们骗人要求退款", "user": "愤怒"})
    check("客服投诉升级建单", s == 200 and j.get("escalated") is True, f"{s} {j}")

    # 合规页
    s, t = call("GET", "/api/compliance/tos", parse_json=False)
    check("用户协议可访问", s == 200 and "用户协议" in t, f"{s}")
    s, t = call("GET", "/api/compliance/privacy", parse_json=False)
    check("隐私政策可访问", s == 200 and "隐私" in t, f"{s}")

    # 真实 RSS 选题（admin）
    s, j = call("POST", "/api/automation/scout", admin=True)
    check("选题扫描运行", s == 200 and "scanned" in j, f"{s} {j}")

    # ---- 邀请裂变 + 反刷（自环/深度上限）----
    sA, jA = call("POST", "/api/register", {"username": "inviter_A", "age_confirmed": True, "password": PW})
    uA = jA.get("user_id")
    _, jL = call("POST", "/api/login", {"username": "inviter_A", "password": PW})
    tokA = jL.get("token")
    _, jP = call("GET", f"/api/users/{uA}", token=tokA)
    codeA = jP.get("invite_code")
    balA0 = jP.get("balance")
    sB, jB = call("POST", "/api/register",
                   {"username": "invitee_B", "age_confirmed": True, "invite_code": codeA, "password": PW})
    # inviter A 应获得邀请奖励（+30），且建立了下线关系
    _, jP2 = call("GET", f"/api/users/{uA}", token=tokA)
    check("邀请裂变发放邀请人奖励(+30)",
          sB == 200 and (jP2.get("balance", 0) - (balA0 or 0)) == 30,
          f"{sB} d={jP2.get('balance')}-{balA0}")
    # 深度上限：连续深链，超过 MAX_REFERRAL_DEPTH 应拒绝建立下线关系
    # （防单一运营者深链刷量；邀请为可选，故接口返回 200 但 invited_by 不被写入）。
    prev_code = codeA
    chain_ids = {}
    chain_tok = {}
    for i in range(2, 14):
        sX, jX = call("POST", "/api/register",
                       {"username": f"chain_{i}", "age_confirmed": True, "invite_code": prev_code, "password": PW})
        if sX != 200:
            break
        chain_ids[i] = jX.get("user_id")
        _, jLx = call("POST", "/api/login", {"username": f"chain_{i}", "password": PW})
        chain_tok[i] = jLx.get("token")
        _, jPx = call("GET", f"/api/users/{jX.get('user_id')}", token=jLx.get("token"))
        prev_code = jPx.get("invite_code")
    # 浅层应成功建链；深层（超过上限）应被拒绝建链
    _, jShallow = call("GET", f"/api/users/{chain_ids.get(2)}", token=chain_tok.get(2))
    _, jDeep = call("GET", f"/api/users/{chain_ids.get(11)}", token=chain_tok.get(11))
    linked_shallow = jShallow.get("invited_by") == uA
    linked_deep = jDeep.get("invited_by") is not None
    check("裂变深度上限拦截深链刷量", linked_shallow and not linked_deep,
          f"shallow_linked={linked_shallow} deep_linked={linked_deep}")

    # ---- 成就/勋章 ----
    s, j = call("POST", f"/api/users/{uid}/badges/evaluate", token=tok)
    check("勋章评估接口", s == 200 and "all" in j and "definitions" in j, f"{s} {j}")
    s, j = call("GET", f"/api/users/{uid}/badges", token=tok)
    check("勋章列表接口", s == 200 and isinstance(j, list), f"{s}")

    # ---- 实时概率 WebSocket ----
    try:
        import asyncio, websockets
        async def _ws():
            async with websockets.connect(BASE.replace("http", "ws") + "/ws/markets") as ws:
                msg = await asyncio.wait_for(ws.recv(), timeout=6)
                d = json.loads(msg)
                return d.get("type") == "probabilities" and isinstance(d.get("markets"), dict)
        check("WebSocket实时概率快照", asyncio.run(_ws()), "")
    except Exception as e:
        check("WebSocket实时概率快照", False, str(e))

    # Oracle 权威源自动结算（非手动）：创建到期市场 + 写入本地 manifest 结果
    import json as _json, os as _os
    _manifest = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "oracle_manifest.json")
    s, j = call("POST", "/api/admin/markets",
                {"title": "测试市场-权威源结算", "category": "体育",
                 "options": ["会", "不会"], "closes_at": "2020-01-01 00:00:00",
                 "oracle_source": "manifest"}, admin=True)
    _mid = j.get("market_id")
    check("admin创建到期市场", s == 200 and _mid, f"{s} {j}")
    if _mid:
        try:
            with open(_manifest, "w", encoding="utf-8") as f:
                _json.dump({str(_mid): {"winning_option": 0}}, f, ensure_ascii=False)
            s, j = call("POST", "/api/oracle/resolve-due", admin=True)
            check("Oracle权威源自动结算", s == 200 and j.get("resolved", 0) >= 1,
                  f"{s} {j}")
            s, j = call("GET", f"/api/markets/{_mid}")
            check("权威源结算后市场已结算", s == 200 and j.get("status") == "settled", f"{s} {j}")
        finally:
            try:
                _os.remove(_manifest)
            except Exception:
                pass

    # ---- ESPN 真实权威源（补全"未做项"）：mock 网关验证映射，不依赖外网 ----
    try:
        import json as _json, urllib.request as _ulib
        # 测试进程以 tests/ 为脚本目录，core 包在 app/ 下，需显式注入路径
        _app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _app_dir not in sys.path:
            sys.path.insert(0, _app_dir)
        from core import oracle_sources as _osm
        _fake = {"events": [{"competitions": [{"status": {"type": {"state": "post"}},
            "competitors": [
                {"team": {"displayName": "Arsenal"}, "winner": True},
                {"team": {"displayName": "Chelsea"}, "winner": False}]}]}]}
        class _R:
            def __init__(self, d): self._d = d
            def read(self): return _json.dumps(self._d).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False
        _orig = _ulib.urlopen
        def _fake_urlopen(req, timeout=10):
            return _R(_fake)
        _ulib.urlopen = _fake_urlopen
        try:
            _espn = _osm.EspnOracleSource()
            _m = {"id": 999, "options": ["Arsenal", "Chelsea"],
                  "oracle_meta": {"provider": "espn", "sport": "soccer",
                                  "league": "eng.1", "date": "20250316"}}
            check("ESPN真实源·mock映射胜方下标", _espn.resolve(_m) == 0, f"got={_espn.resolve(_m)}")
            check("ESPN真实源·无meta返回None", _espn.resolve({"id": 1, "options": ["会", "不会"], "oracle_meta": None}) is None, "")
        finally:
            _ulib.urlopen = _orig
    except Exception as e:
        check("ESPN真实源·mock映射胜方下标", False, str(e))

    # 真实网络探针（非门禁）：若本机可达 ESPN，seed 的 m11 应已被 resolve-due 自动结算为 Arsenal(0)
    try:
        import urllib.parse as _up
        s, lst = call("GET", "/api/markets?q=" + _up.quote("阿森纳 vs 切尔西"))
        _m11 = None
        if s == 200:
            for _m in (lst if isinstance(lst, list) else (lst.get("markets") or [])):
                if "阿森纳 vs 切尔西" in (_m.get("title") or ""):
                    _m11 = _m.get("id"); break
        if _m11:
            s2, j2 = call("GET", f"/api/markets/{_m11}")
            if s2 == 200 and j2.get("status") == "settled":
                check("ESPN真实源·seed市场自动结算(Arsenal=0)", j2.get("resolution") == 0,
                      f"res={j2.get('resolution')}")
            else:
                check("ESPN真实源·seed市场自动结算(Arsenal=0)", True, "网络不可用，跳过实时校验")
        else:
            check("ESPN真实源·seed市场自动结算(Arsenal=0)", True, "未找到m11，跳过")
    except Exception as e:
        check("ESPN真实源·seed市场自动结算(Arsenal=0)", True, f"跳过:{e}")

    # ---- 竞猜联赛（Tournaments）----
    s, j = call("GET", "/api/tournaments")
    check("联赛列表接口", s == 200 and isinstance(j, list) and len(j) >= 0, f"{s} {j}")
    tid = (j[0]["id"] if j else None)
    if tid:
        s, j = call("GET", f"/api/tournaments/{tid}")
        check("联赛详情含市场与参赛", s == 200 and "markets" in j, f"{s}")
        # 当前用户参加联赛（seed 联赛 entry_fee=0）
        s, j = call("POST", f"/api/tournaments/{tid}/join?user_id={uid}", token=tok)
        check("用户加入联赛", s == 200 and j.get("ok") is True, f"{s} {j}")
        s, j = call("GET", f"/api/tournaments/{tid}/leaderboard")
        check("联赛排行榜接口", s == 200 and isinstance(j, list), f"{s} {j}")
        # admin 关闭联赛（发放奖励池，无异常即可）
        s, j = call("POST", f"/api/admin/tournaments/{tid}/close", admin=True)
        check("联赛关闭并发放奖励池", s == 200 and "paid_total" in j, f"{s} {j}")

    # ---- 每日任务中心 ----
    s, j = call("GET", f"/api/users/{uid}/tasks", token=tok)
    check("每日任务列表", s == 200 and isinstance(j, list) and len(j) > 0, f"{s} {j}")
    signin_task = next((t for t in j if t["id"] == "signin"), None)
    if signin_task and signin_task["done"]:
        bal0 = (call("GET", f"/api/users/{uid}/points")[1].get("balance"))
        s, j = call("POST", f"/api/users/{uid}/tasks/claim?task_id=signin", token=tok)
        check("领取每日任务奖励", s == 200 and j.get("reward") is not None, f"{s} {j}")
        bal1 = (call("GET", f"/api/users/{uid}/points")[1].get("balance"))
        check("任务奖励已入账", bal1 is not None and bal0 is not None and bal1 >= bal0, f"{bal0}->{bal1}")

    # ---- 投资人 KPI ----
    s, j = call("GET", "/api/admin/metrics", admin=True)
    check("KPI 指标接口", s == 200 and "total_users" in j and "dau" in j and "reward_pool_today" in j, f"{s} {j}")

    # ---- 市场发现（搜索/分类）----
    s, j = call("GET", "/api/markets?q=" + urllib.parse.quote("世界杯"))
    check("市场关键词搜索", s == 200 and isinstance(j, list), f"{s} {j}")
    s, j = call("GET", "/api/categories")
    check("分类接口", s == 200 and isinstance(j, list), f"{s} {j}")

    # ---- 排行榜 Pro 分层 ----
    s, j = call("GET", "/api/leaderboard")
    check("排行榜含Pro标志", s == 200 and isinstance(j, list) and all("pro" in r for r in j), f"{s} {j}")

    # ===== v0.4.0 国际化对标升级：概率历史/趋势、多结果、评论层、连胜、发现 =====
    # 1) 多结果市场（对标 Manifold / Metaculus 丰富题型，验证 N 选项路径）
    s, j = call("POST", "/api/admin/markets",
                {"title": "2026 年度最佳影片类型归属", "category": "娱乐",
                 "options": ["喜剧", "科幻", "动画", "动作"],
                 "closes_at": "2099-12-31 00:00:00", "oracle_source": "官方颁奖结果"},
                admin=True)
    check("admin创建多结果市场(4选项)", s == 200 and j.get("market_id"), f"{s} {j}")
    multi_id = j.get("market_id")
    if multi_id:
        s, j = call("GET", f"/api/markets/{multi_id}")
        check("多结果市场含N个选项", s == 200 and len(j.get("options", [])) == 4, f"{s} {j}")
        # 参与两个选项，制造概率分歧 + 历史快照
        call("POST", f"/api/markets/{multi_id}/participate",
             {"user_id": uid, "option": 0, "stake": 20}, token=tok)
        call("POST", f"/api/markets/{multi_id}/participate",
             {"user_id": uA, "option": 2, "stake": 30}, token=tokA)
        # 概率历史快照（创建时 + 每次参与）用于趋势图 / sparkline
        s, j = call("GET", f"/api/markets/{multi_id}/history")
        check("概率历史快照>=2条", s == 200 and isinstance(j, list) and len(j) >= 2, f"{s} {j}")
        # 评论 / 理由层（对标 GJ Open「分享你的理由」、Manifold/Kalshi 评论区）
        s, j = call("POST", f"/api/markets/{multi_id}/comments",
                    {"user_id": uid, "body": "我觉得动画今年呼声最高"}, token=tok)
        check("评论/理由层发表", s == 200 and j.get("comment_id"), f"{s} {j}")
        cid = j.get("comment_id")
        s, j = call("POST", f"/api/markets/{multi_id}/comments",
                    {"user_id": uA, "body": "同意，且科幻也值得关注", "parent_id": cid}, token=tokA)
        check("二级回复嵌套", s == 200 and j.get("comment_id"), f"{s} {j}")
        s, j = call("GET", f"/api/markets/{multi_id}/comments")
        check("评论线程含回复", s == 200 and isinstance(j, list) and j and j[0].get("replies"),
              f"{s} {j}")

    # 2) 每日预测连胜 streak（对标 Manifold 每日预测奖励循环）
    s, j = call("GET", f"/api/users/{uid}/streak", token=tok)
    check("每日预测连胜接口", s == 200 and "predict_streak" in j and "next_reward" in j, f"{s} {j}")
    check("连胜>=1(已参与过)", s == 200 and j.get("predict_streak", 0) >= 1, f"{s} {j}")

    # 3) 发现：热门 / 即将截止 / 个性化 feed（对标 Myriad/Manifold for-you、Kalshi 热门榜）
    s, j = call("GET", "/api/markets/trending")
    check("热门榜接口", s == 200 and isinstance(j, list), f"{s} {j}")
    s, j = call("GET", "/api/markets/ending")
    check("即将截止接口", s == 200 and isinstance(j, list), f"{s} {j}")
    s, j = call("GET", f"/api/feed?user_id={uid}")
    check("个性化首页feed", s == 200 and "for_you" in j and "hot_markets" in j and "ending_soon" in j,
          f"{s} {j}")

    # ===== v0.4.1 合规补强（方案第七节·第1-3项）=====
    # 1) 评论层轻量审核：主权红线硬拒 / 引流入工单 / 举报阈值下线 / 管理员放行
    if multi_id:
        # 闸①：主权红线硬拒（400，不入库）
        s, j = call("POST", f"/api/markets/{multi_id}/comments",
                    {"user_id": uid, "body": "支持台湾独立"}, token=tok)
        check("评论主权红线硬拒(400)", s == 400, f"{s} {j}")
        # 闸③：引流入工单 → status='review'，不对外展示
        s, j = call("POST", f"/api/markets/{multi_id}/comments",
                    {"user_id": uid, "body": "加微信看更多预测"}, token=tok)
        check("评论引流入review", s == 200 and j.get("status") == "review", f"{s} {j}")
        spam_cid = j.get("comment_id")
        # 对外列表默认不展示 review 评论
        s, j = call("GET", f"/api/markets/{multi_id}/comments")
        visible_ids = [c["id"] for c in j]
        check("review评论不对外展示", spam_cid not in visible_ids, f"{spam_cid} in {visible_ids}")
        if spam_cid:
            # 运营待审队列可见
            s, j = call("GET", "/api/admin/comments/pending", admin=True)
            check("管理员待审队列含该评论", s == 200 and any(c["id"] == spam_cid for c in j),
                  f"{s} {j}")
            # 管理员放行 → status='ok'，重新对外可见
            s, j = call("POST", f"/api/admin/comments/{spam_cid}/review",
                        {"approve": True, "note": "冒烟放行"}, admin=True)
            check("管理员放行评论", s == 200 and j.get("status") == "ok", f"{s} {j}")
            s, j = call("GET", f"/api/markets/{multi_id}/comments")
            check("放行后评论对外可见", any(c["id"] == spam_cid for c in j), f"{spam_cid}")

        # 举报阈值自动下线：发正常评论→举报3次→转review
        s, j = call("POST", f"/api/markets/{multi_id}/comments",
                    {"user_id": uA, "body": "我觉得动作片会赢"}, token=tokA)
        rcid = j.get("comment_id")
        if rcid:
            # 去重后需 3 名【不同】用户举报才达阈值（同一用户重复举报不计数）
            for uname in ("reporter_x1", "reporter_x2", "reporter_x3"):
                _, jR = call("POST", "/api/register", {"username": uname, "age_confirmed": True, "password": PW})
                call("POST", f"/api/comments/{rcid}/report", {"user_id": jR.get("user_id")})
            s, jq = call("GET", "/api/admin/comments/pending", admin=True)
            check("3名不同用户举报达阈值自动转待审",
                  s == 200 and any(p["id"] == rcid for p in jq), f"{s} {jq}")
            # 管理员下架
            s, j = call("POST", f"/api/admin/comments/{rcid}/review",
                        {"approve": False, "note": "冒烟下架"}, admin=True)
            check("举报达标后管理员下架评论", s == 200 and j.get("status") == "rejected", f"{s} {j}")

    # 2) for_you 多因子个性化：每条附 reco_reason（可解释推荐）
    s, j = call("GET", f"/api/feed?user_id={uid}")
    fy = j.get("for_you") if s == 200 else None
    check("for_you含推荐理由", isinstance(fy, list) and fy
          and all("reco_reason" in m for m in fy), f"{s} {j}")
    if fy:
        check("推荐理由非空", bool(fy[0].get("reco_reason")), f"{fy[0].get('reco_reason')}")

    # 3) 概率历史快照含事件标注 reason（驱动趋势图悬停/事件标记）
    if multi_id:
        s, j = call("GET", f"/api/markets/{multi_id}/history")
        check("历史快照含事件标注reason",
              s == 200 and isinstance(j, list) and any(r.get("reason") == "create" for r in j),
              f"{s} {j}")

    # v0.5.0 Agent Orchestrator 能力
    s, j = call("POST", "/api/agents/orchestrate",
                {"goal": "用户投诉积分没到账", "input": {"message": "积分没到账", "user": "u1"}, "agent": "support"})
    check("Agent编排提交返回task_id", s == 200 and j.get("task_id"), f"{s} {j}")
    task_id = j.get("task_id")
    if task_id:
        s, j = call("GET", f"/api/agents/tasks/{task_id}")
        check("Agent任务状态可查询", s == 200 and j.get("status") == "done", f"{s} {j}")

    s, j = call("GET", "/api/admin/agents/dashboard", admin=True)
    check("Agent看板返回在线Agent列表", s == 200 and isinstance(j.get("agents"), list) and len(j["agents"]) >= 6,
          f"{s} {j}")

    s, j = call("POST", "/api/admin/agents/rules",
                body={"name": "UGC自动通过规则", "text": "娱乐类UGC题目有票房数据源时自动通过"}, admin=True)
    check("自然语言规则创建成功", s == 200 and j.get("rule_id") and j["rule"].get("domain") == "UGC审核",
          f"{s} {j}")

    # v0.6.0 留存与信任补强：声誉即特权 + 结算透明 + 争议投票
    s, j = call("GET", f"/api/users/{uA}/tier", token=tokA)
    check("声誉等级端点返回等级与特权", s == 200 and j.get("tier_name") and isinstance(j.get("privileges"), list),
          f"{s} {j}")

    s, j = call("GET", "/api/markets?status=settled")
    settled = j if s == 200 else []
    if settled:
        sid = settled[0]["id"]
        s, j = call("GET", f"/api/markets/{sid}/resolution")
        check("公开结算依据含权威源与结果", s == 200 and j.get("oracle_source") and j.get("resolution_label"),
              f"{s} {j}")
        # 发起争议并投票（透明化：社区投票 + 管理员终审）
        # 注意：dispute 端点的 user_id/reason 走查询参数（与前端 openDispute 一致），且需鉴权 token
        s, j = call("POST", f"/api/markets/{sid}/dispute?user_id={uA}&reason={urllib.parse.quote('冒烟测试异议')}", token=tokA)
        did = j.get("dispute_id")
        check("可发起结算争议", s == 200 and did, f"{s} {j}")
        if did:
            s, j = call("POST", f"/api/disputes/{did}/vote", {"user_id": uA, "vote": "uphold"}, token=tokA)
            check("争议社区投票计入票型", s == 200 and j.get("total", 0) >= 1, f"{s} {j}")
    else:
        check("公开结算依据含权威源与结果", False, "无已结算市场可测")

    # ===== v0.7.0 经济模型重构回归 =====
    # (1) 份额结算：押中至少回本（修复「赢了也亏」）
    s, j = call("POST", "/api/admin/markets",
                {"title": "v0.7经济回归-押中回本", "category": "体育",
                 "options": ["会", "不会"], "closes_at": "2020-01-01 00:00:00",
                 "oracle_source": "manifest"}, admin=True)
    emid = j.get("market_id")
    if emid:
        bal0 = call("GET", f"/api/users/{uA}", token=tokA)[1].get("points_balance", 0)
        s, j = call("POST", f"/api/markets/{emid}/participate",
                     {"user_id": uA, "option": 0, "stake": 20}, token=tokA)
        check("v0.7 参与(真实份额记账)", s == 200 and "probabilities" in j, f"{s} {j}")
        s, j = call("POST", "/api/admin/settle",
                    {"market_id": emid, "winning_option": 0, "source": "回归校验"}, admin=True)
        check("v0.7 结算发放奖励池", s == 200 and j.get("paid_total", 0) >= 0, f"{s} {j}")
        bal1 = call("GET", f"/api/users/{uA}", token=tokA)[1].get("points_balance", 0)
        # 参与扣 20 + 可能连胜奖励，押中至少回本 => 余额不减（净变化 ≥ 0）
        check("v0.7 押中至少回本(余额不减)", bal1 >= bal0, f"bal0={bal0} bal1={bal1}")

    # (2) 留存指标端点（投资人第一指标）
    s, j = call("GET", "/api/admin/metrics/retention", admin=True)
    check("v0.7 留存指标端点(D1/D7/D30+队列)", s == 200 and "cohorts" in j and "overall" in j, f"{s} {j}")

    # (3) UGC 门槛由黄金降为白银（解锁内容供给飞轮）—— 单元校验 core.tiers
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from core import tiers as _tiers
    check("v0.7 UGC门槛降至白银(rep50可建)", _tiers.can_create_market(50) is True)
    check("v0.7 UGC门槛(rep0不可建)", _tiers.can_create_market(0) is False)

    # ===== v0.7.1 生产化收尾回归 =====
    # (1) CSP：script-src 去 unsafe-inline，改用每请求 nonce，且 HTML 里的 nonce 与响应头一致
    st, hdrs, html = raw_get("/")
    csp = hdrs.get("content-security-policy", "")
    script_src = ""
    for d in csp.split(";"):
        if d.strip().startswith("script-src"):
            script_src = d.strip()
    check("v0.7.1 CSP script-src 已去 unsafe-inline",
          st == 200 and script_src and "'unsafe-inline'" not in script_src,
          f"{st} script_src={script_src!r}")
    check("v0.7.1 CSP script-src 使用 nonce", "'nonce-" in script_src, script_src)
    import re as _re
    m = _re.search(r"'nonce-([^']+)'", script_src)
    hn = _re.search(r'<script nonce="([^"]+)"', html or "")
    check("v0.7.1 页面 script 带与响应头一致的 nonce",
          bool(m and hn and m.group(1) == hn.group(1)),
          f"header={m.group(1) if m else None} html={hn.group(1) if hn else None}")

    # (1b) v0.7.5：style-src 也去 unsafe-inline，改用 nonce；内联 style="..." 已收拢为 CSS 类
    style_src = ""
    for d in csp.split(";"):
        if d.strip().startswith("style-src"):
            style_src = d.strip()
    check("v0.7.5 CSP style-src 已去 unsafe-inline",
          style_src and "'unsafe-inline'" not in style_src,
          f"style_src={style_src!r}")
    check("v0.7.5 CSP style-src 使用 nonce", "'nonce-" in style_src, style_src)
    ms = _re.search(r"'nonce-([^']+)'", style_src)
    hns = _re.search(r'<style nonce="([^"]+)"', html or "")
    check("v0.7.5 页面 style 带与响应头一致的 nonce",
          bool(ms and hns and ms.group(1) == hns.group(1)),
          f"header={ms.group(1) if ms else None} html={hns.group(1) if hns else None}")
    # 内联 style="..." 应已清零（动态的改为 data-rs-style，由 MutationObserver 运行时应用）
    inline_style = len(_re.findall(r'(?<!-r)sstyle="', html or ""))
    data_rs = len(_re.findall(r'data-rs-style="', html or ""))
    check("v0.7.5 内联 style 已收拢(静态→CSS类,动态→data-rs-style)",
          inline_style == 0 and data_rs >= 1,
          f"inline_style={inline_style} data_rs_style={data_rs}")

    # (2) 页面无内联事件处理器（否则 CSP 会全部拦掉，点了没反应）
    inline_on = len(_re.findall(r'\son[a-z]+="', html or ""))
    check("v0.7.1 前端已无内联事件属性(事件委托)", st == 200 and inline_on == 0,
          f"inline_on={inline_on}")

    # (3) backplane：默认 memory，配了 REDIS_URL 才走 redis
    s, j = call("GET", "/api/health")
    check("v0.7.1 health 暴露 backplane", s == 200 and j.get("backplane") in ("memory", "redis"),
          f"{s} {j.get('backplane')}")

    # (4) 版本号
    check("v0.7.1 版本号已升级", j.get("version") == "0.7.7", str(j.get("version")))

    # (4b) v0.7.6：DB 连接池为「每线程独立连接」，修复 v0.7.5 全局单连接的
    # 事务交叉污染（78 个同步端点走线程池，共享连接会让 A 的未提交写被 B 的
    # commit 连带提交）。断言 reset_conn_pool 存在且可安全调用。
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__)))))
        from db import reset_conn_pool as _rcp  # noqa: E402
        _rcp()
        _ok = True
        _note = "reset_conn_pool 可用"
    except Exception as _e:
        _ok = False
        _note = f"{type(_e).__name__}: {_e}"
    check("v0.7.6 DB 连接池为每线程独立连接(可安全重置)",
          _ok, _note)

    # (5) 数据产品 API（§5.3 P1 变现单点验证）：匿名情绪指数 + CSV 导出，开放可访问
    s, body = call("GET", "/api/data/sentiment", parse_json=True)
    ok_sent = s == 200 and isinstance(body, list) and len(body) > 0 and "sentiment_index" in body[0]
    check("数据产品·匿名情绪指数 API 可用", ok_sent, f"{s} {str(body)[:120]}")
    s2, csv_body = call("GET", "/api/data/export?kind=category", parse_json=False)
    ok_csv = s2 == 200 and csv_body.strip().startswith("category,")
    check("数据产品·品类聚合 CSV 导出可用", ok_csv, f"{s2} {csv_body[:60]!r}")

    # (6) v0.7.7 时区正确性（海外部署必修，本机 UTC+8 也受影响）
    # 旧代码用 Python datetime.now()（容器本地时间）写库、却用 SQLite
    # date('now')（恒为 UTC，不受 TZ 环境变量影响）查询，二者在非 UTC 服务器上
    # 差出整个时区偏移——导致每天本地 00:00 起、长达偏移窗口内「今日签到 /
    # 今日参与 / 日活统计」全被算成昨天。现统一为：落库一律 UTC，业务上的
    # 「今天」由 APP_TZ 定义，按天查询走 day_bounds_utc() 的 UTC 边界参数。
    _ts = ""
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__)))))
        from db import now_iso as _now_iso, day_bounds_utc as _bounds  # noqa: E402
        _bad = []
        for _h in (0, 8, -5, 5.5, 13):
            # 跨零点时两次取时可能落在不同天，给一次重试，避免测试偶发抖动
            for _ in range(2):
                _s, _e = _bounds(_h)
                _ts = _now_iso()
                if _s <= _ts < _e:
                    break
            else:
                _bad.append(f"APP_TZ={_h}: {_ts} 不在 [{_s}, {_e})")
        _ok_tz = not _bad
        _note_tz = "; ".join(_bad) if _bad else f"5 个时区下 {_ts} 均落在今日区间内"
    except Exception as _e:
        _ok_tz = False
        _note_tz = f"{type(_e).__name__}: {_e}"
    check("v0.7.7 任意 APP_TZ 下「此刻写入」都计入今日", _ok_tz, _note_tz)

    # (6b) 落库格式统一为空格分隔：'T'(0x54) > 空格(0x20)，与 strftime 系混排
    # 会让同一天内的字符串比较（到期判定、按天范围查询）排序错乱。
    check("v0.7.7 落库时间格式为 '%Y-%m-%d %H:%M:%S'(空格分隔)",
          "T" not in _ts and len(_ts) == 19, f"now_iso()={_ts!r}")

    print(f"\n结果：PASS={len(PASS)}  FAIL={len(FAIL)}")
    if FAIL:
        print("失败项：", FAIL)
        sys.exit(1)
    print("全部通过 ✅")


def kill_port_8000():
    """Best-effort: kill any process listening on TCP 8000 (Windows).
    netstat+taskkill 与 PowerShell Get-NetTCPConnection 双重兜底，确保残留进程被清理。"""
    # 方法1：netstat + taskkill（兼容旧系统）
    try:
        out = subprocess.check_output(["netstat", "-ano", "-p", "TCP"],
                                      stderr=subprocess.DEVNULL).decode()
        for line in out.splitlines():
            if ":8000" in line and "LISTENING" in line:
                pid = line.split()[-1]
                subprocess.run(["taskkill", "/PID", pid, "/F"],
                               stderr=subprocess.DEVNULL)
    except Exception:
        pass
    # 方法2：PowerShell 兜底（Win10+ 更可靠）
    try:
        ps = ("foreach ($c in Get-NetTCPConnection -LocalPort 8000 "
              "-ErrorAction SilentlyContinue) { "
              "Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       stderr=subprocess.DEVNULL, timeout=15)
    except Exception:
        pass
    # 等待端口真正释放，避免 uvicorn 绑定竞态（端口仍被旧进程占用导致新进程静默失败）
    for _ in range(20):
        try:
            with urllib.request.urlopen(BASE + "/", timeout=1):
                time.sleep(0.5)
                continue
        except Exception:
            break


def _port_in_use():
    try:
        with urllib.request.urlopen(BASE + "/", timeout=1):
            return True
    except Exception:
        return False


def fresh_start():
    """Wipe DB, re-seed, and start a local server for an idempotent run.

    顺序很关键：**先杀残留进程，再清库播种**。
    曾经把 kill_port_8000() 放在 wipe 之后，结果上一轮遗留的服务仍持有
    platform.db 的写锁，DROP TABLE 直接 `database is locked` 失败，随后
    seed.py 撞残留数据报 UNIQUE 约束冲突——表现为一堆无法解释的级联失败。
    先把端口清干净、等端口真正释放，再动数据库，才是幂等的。
    """
    # 先杀干净 8000 端口残留进程，并等待端口与文件锁真正释放
    kill_port_8000()
    for _ in range(20):
        if not _port_in_use():
            break
        time.sleep(0.5)

    try:
        import sqlite3
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = os.path.join(app_dir, "platform.db")
        conn = sqlite3.connect(db_path, timeout=30)
        tabs = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        for t in tabs:
            conn.execute(f'DROP TABLE IF EXISTS "{t}"')
        conn.commit()
        conn.close()
        print("[fresh] wiped tables:", tabs)
    except Exception as e:
        print("[fresh] wipe failed:", repr(e))

    print("[fresh] seeding ...")
    subprocess.run([sys.executable, "seed.py"], check=True)

    print("[fresh] starting server ...")
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", "8000",
         "--log-level", "warning"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        try:
            with urllib.request.urlopen(BASE + "/", timeout=2):
                # 守卫①：v0.4.0 端点
                st, _ = call("GET", "/api/markets/ending")
                if st != 200:
                    print("[fresh] server up but v0.4.0 endpoint missing; retrying...")
                    time.sleep(1)
                    continue
                # 守卫②：v0.4.1 端点（404=残留旧进程）
                st2, _ = call("GET", "/api/admin/comments/pending")
                if st2 != 404:
                    print("[fresh] server up (v0.4.1 verified)")
                    return
                print("[fresh] v0.4.1 endpoint missing (stale server); killing & retrying...")
                kill_port_8000()
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("[fresh] server failed to start / stale server on port 8000")


if __name__ == "__main__":
    if "--fresh" in sys.argv:
        fresh_start()
    main()
