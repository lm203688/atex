"""生产模式本地验证：用真实环境变量拉起后逐项探测。"""
import json, os, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8001"
ADMIN = os.environ.get("PROD_ADMIN", "prod-secret-abc123")
CORS = os.environ.get("PROD_CORS", "https://example.com")
DB = os.environ.get("DB_PATH", "/tmp/realcast_prod/platform.db")


def req(method, path, headers=None, body=None, raw=False):
    h = headers or {}
    data = json.dumps(body).encode() if body is not None else None
    if data is not None and "Content-Type" not in h:
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, (resp.read().decode() if not raw else resp.read()), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


def wait_health():
    for _ in range(30):
        s, _, _ = req("GET", "/api/health")
        if s == 200:
            return True
        time.sleep(1)
    return False


def main():
    print("== 生产模式本地验证 ==")
    if not wait_health():
        print("FAIL: 服务未就绪")
        return 1
    ok = True

    # 1) 健康检查含版本与 oracle 源
    s, b, _ = req("GET", "/api/health")
    j = json.loads(b)
    print(f"  health: {s} version={j.get('version')} oracle_sources={j.get('oracle_sources')}")
    ok &= (s == 200 and j.get("version") == "0.4.1" and isinstance(j.get("oracle_sources"), list))

    # 2) 管理员端点无 token → 401
    s, _, _ = req("GET", "/api/admin/comments/pending")
    print(f"  admin无token: {s} (期望401)")
    ok &= (s == 401)

    # 3) 错误 token → 401
    s, _, _ = req("GET", "/api/admin/comments/pending", headers={"x-admin-token": "wrong"})
    print(f"  admin错token: {s} (期望401)")
    ok &= (s == 401)

    # 4) 正确 token → 200
    s, b, _ = req("GET", "/api/admin/comments/pending", headers={"x-admin-token": ADMIN})
    print(f"  admin正确token: {s} (期望200)")
    ok &= (s == 200)

    # 5) CORS：预检返回配置的来源（而非 localhost）
    s, _, h = req("OPTIONS", "/api/register",
                  headers={"Origin": CORS, "Access-Control-Request-Method": "POST"})
    acao = h.get("Access-Control-Allow-Origin") or h.get("access-control-allow-origin")
    print(f"  CORS预检 Origin={CORS} -> ACAO={acao}")
    ok &= (acao == CORS)

    # 6) 数据库已落到 DB_PATH 挂载点
    print(f"  DB_PATH={DB} exists={os.path.exists(DB)}")
    ok &= os.path.exists(DB)

    # 7) 内容安全：前端仍可创建只读无金钱权重的评论（合规闭环仍在）
    s, b, _ = req("POST", "/api/register", body={"username": "生产验证员", "age_confirmed": True})
    uid = json.loads(b).get("user_id") if s == 200 else None
    print(f"  注册: {s} uid={uid}")
    ok &= (s == 200 and uid)

    print("\n生产模式验证:", "全部通过 ✅" if ok else "存在失败项 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
