"""MCP 兼容服务（P4-B3）：把真测预测能力暴露为标准 JSON-RPC 2.0 工具。

设计：
- 纯标准库 + 现有 core/db，**无外部依赖**（不引入 mcp SDK），降低合规面与维护成本。
- 协议最小化子集：initialize / tools/list / tools/call（批处理数组也支持）。
- 只读工具（list_markets / get_market / get_forecast / get_leaderboard）无需鉴权；
  写工具 submit_prediction **必须**带 Authorization: Bearer <user_token>，且在服务端核验
  token 与 user_id 匹配后才走普通 participate 路径（与平台所有写操作同一条合规链路）。
- 外部概率仅只读返回，绝不经此接口写入结算（合规隔离）。

参考：Coral Protocol 等让 agent 通过标准协议发预测；本模块是「AI 协作预测」的标准入口。
"""
import json
from db import get_conn
from core import markets, rating
from core import forecast as forecast_mod

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "realcast-mcp", "version": "0.1.0"}

TOOLS = [
    {
        "name": "list_markets",
        "description": "列出预测市场；可选 status 过滤（open/settled），limit 控制数量。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "open 或 settled"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "get_market",
        "description": "获取单个市场详情（选项、概率、状态、共识）。",
        "inputSchema": {
            "type": "object",
            "properties": {"market_id": {"type": "integer"}},
            "required": ["market_id"],
        },
    },
    {
        "name": "get_forecast",
        "description": "获取聚合共识（分类/数值统一）及群体 vs 外部参考的偏差分析。",
        "inputSchema": {
            "type": "object",
            "properties": {"market_id": {"type": "integer"}},
            "required": ["market_id"],
        },
    },
    {
        "name": "get_leaderboard",
        "description": "获取排行榜；sort=points(积分榜) 或 skill(技能榜，不确定性感知)。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "sort": {"type": "string", "default": "points"},
            },
        },
    },
    {
        "name": "submit_prediction",
        "description": "提交一个预测（写操作，需鉴权）。走与普通用户相同的 participate 路径。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer"},
                "market_id": {"type": "integer"},
                "option_index": {"type": "integer"},
                "stake": {"type": "integer"},
            },
            "required": ["user_id", "market_id", "option_index", "stake"],
        },
        "requires_auth": True,
    },
]


def _resolve_user(token):
    """由 bearer token 反查 user_id；无效返回 None。"""
    if not token:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE token=?", (token,)
        ).fetchone()
    return row["id"] if row else None


def _err(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _call_tool(name, args, auth_token):
    if name == "list_markets":
        status = args.get("status")
        limit = int(args.get("limit", 20))
        with get_conn() as conn:
            if status:
                ids = conn.execute(
                    "SELECT id FROM markets WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                ids = conn.execute(
                    "SELECT id FROM markets ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [markets.get_market(r["id"]) for r in ids]
    if name == "get_market":
        m = markets.get_market(int(args["market_id"]))
        if not m:
            raise ValueError("market not found")
        return m
    if name == "get_forecast":
        mid = int(args["market_id"])
        fc = forecast_mod.aggregate(mid, weighted=True)
        if not fc:
            raise ValueError("market not found")
        fc["deviation_vs_external"] = forecast_mod.deviation_vs_external(mid)
        return fc
    if name == "get_leaderboard":
        from core import tiers
        sort = args.get("sort", "points")
        limit = int(args.get("limit", 20))
        # 复用 main 的排行榜逻辑成本高，这里直接出精简版（与公开榜一致）
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, username, points_balance, reputation, skill_rating, skill_rd "
                "FROM users ORDER BY "
                + ("(skill_rating - 1.96*skill_rd) DESC" if sort == "skill" else "points_balance DESC")
                + " LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if sort == "skill":
                low, high = rating.rating_interval(
                    d.pop("skill_rating"), d.pop("skill_rd"))
                d["skill_low"], d["skill_high"] = round(low, 1), round(high, 1)
            out.append(d)
        return out
    if name == "submit_prediction":
        uid = _resolve_user(auth_token)
        if not uid or uid != int(args["user_id"]):
            raise PermissionError("token 与 user_id 不匹配或无效")
        return markets.participate(
            int(args["user_id"]), int(args["market_id"]),
            int(args["option_index"]), int(args["stake"]),
        )
    raise ValueError(f"unknown tool {name}")


def handle(payload, auth_token=None):
    """处理一个 JSON-RPC 请求（dict）或批处理（list）。返回 dict 或 list。"""
    if isinstance(payload, list):
        return [handle_one(p, auth_token) for p in payload if isinstance(p, dict)]
    return handle_one(payload, auth_token)


def handle_one(req, auth_token):
    rid = req.get("id")
    method = req.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "tools": [{"name": t["name"], "description": t["description"],
                       "inputSchema": t["inputSchema"]} for t in TOOLS]}}
    if method == "tools/call":
        params = req.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {})
        tool = next((t for t in TOOLS if t["name"] == name), None)
        if not tool:
            return _err(rid, -32601, f"unknown tool: {name}")
        if tool.get("requires_auth") and not auth_token:
            return _err(rid, -32001, "auth required: provide Authorization: Bearer <token>")
        try:
            result = _call_tool(name, args or {}, auth_token)
        except PermissionError as e:
            return _err(rid, -32001, str(e))
        except Exception as e:  # 业务错误（如市场不存在/参数非法）统一返回
            return _err(rid, -32000, str(e))
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text",
                         "text": json.dumps(result, ensure_ascii=False, default=str)}]}}
    return _err(rid, -32601, f"unsupported method: {method}")
