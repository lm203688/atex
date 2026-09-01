"""可插拔 Oracle 权威源（消除「结算 Oracle 只能手动」）。

设计：每个 OracleSource 实现 resolve(market) -> Optional[int]，
返回赢家 option 下标，或 None（无法判定，交由人工/保留待结算）。

平台按注册顺序尝试已启用的源；任一源给出结果即采用并留痕 source。

内置三类真实可用的适配器：
- ManifestOracleSource：读取本地结果清单（seed/运维预置），适合已知结果的
  演示/历史/可复现市场。无需外网，确定性强。
- EspnOracleSource：对接 ESPN 公开比分 API（无需密钥、本机网络可达），
  真实裁决体育市场（仅对带 espn oracle_meta 的市场生效）。
- HttpOracleSource：对接外部权威 API 的范例适配器（需配置 endpoint+key），
  是接入真实权威源（体育比分 API、官方公告 API、新闻核验 API）的标准入口。
  未配置时 resolve 返回 None（不启用），绝不冒充结果。
"""
from __future__ import annotations

import json
import os
import time
from typing import List, Optional

from db import get_conn
from core import markets


class BaseOracleSource:
    name = "base"

    def resolve(self, market: dict) -> Optional[int]:
        raise NotImplementedError

    def enabled(self) -> bool:
        return True


class ManifestOracleSource(BaseOracleSource):
    """本地结果清单（JSON），适合已知结果的演示/历史市场。

    清单格式：{"<market_id 或 标题>": {"winning_option": <int 或 option文本>}, ...}
    命中后按 option 下标或文本归一化为下标返回。
    """

    name = "manifest"

    def __init__(self, path: str = "oracle_manifest.json"):
        self.path = path
        self._data: dict = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}

    def reload(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}

    def resolve(self, market: dict) -> Optional[int]:
        self.reload()
        m = self._data.get(str(market.get("id")))
        if m is None:
            m = self._data.get(market.get("title"))
        if not isinstance(m, dict):
            return None
        opt = m.get("winning_option")
        options = market.get("options") or []
        if isinstance(opt, int):
            return opt if 0 <= opt < len(options) else None
        if isinstance(opt, str):
            try:
                return options.index(opt)
            except (ValueError, AttributeError):
                return None
        return None


class HttpOracleSource(BaseOracleSource):
    """对接外部权威 API 的适配器（真实接入点，生产可落地）。

    配置：环境变量 ORACLE_HTTP_ENDPOINT（必填，否则不启用）、ORACLE_HTTP_APIKEY（可选）。
    支持的响应形态（任一即可，自动归一化）：
      - 单条对象：{"market_id": <id>, "winning_option": <int>} 或 {"result": <int>}
      - 列表：[{"market_id": <id>, "winning_option": <int>}, ...]
    带 60s TTL 内存缓存，避免高频轮询打爆外部源。
    未配置 endpoint 时 resolve 返回 None（不启用），绝不冒充结果。
    """

    name = "http"
    CACHE_TTL = 60.0

    def __init__(self, endpoint: Optional[str] = None, apikey: Optional[str] = None):
        self.endpoint = endpoint or os.environ.get("ORACLE_HTTP_ENDPOINT")
        self.apikey = apikey or os.environ.get("ORACLE_HTTP_APIKEY")
        self._cache: dict = {}
        self._cache_at: dict = {}

    def enabled(self) -> bool:
        return bool(self.endpoint)

    def _cached(self, market_id: int):
        now = time.time()
        if market_id in self._cache and now - self._cache_at.get(market_id, 0) < self.CACHE_TTL:
            return self._cache[market_id]
        return None

    def _put(self, market_id: int, val):
        self._cache[market_id] = val
        self._cache_at[market_id] = time.time()

    @staticmethod
    def _norm(opt, options) -> Optional[int]:
        if isinstance(opt, int) and 0 <= opt < len(options):
            return opt
        if isinstance(opt, str):
            try:
                return options.index(opt)
            except (ValueError, AttributeError):
                return None
        return None

    def resolve(self, market: dict) -> Optional[int]:
        if not self.endpoint:
            return None
        mid = market.get("id")
        cached = self._cached(mid)
        if cached is not None:
            return cached
        import urllib.request

        url = f"{self.endpoint.rstrip('/')}/resolve?market_id={mid}"
        try:
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {self.apikey}"} if self.apikey else {},
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            options = market.get("options") or []
            # 列表形态：找匹配 market_id 的条目
            if isinstance(data, list):
                hit = next((d for d in data if d.get("market_id") == mid), None)
                data = hit or {}
            opt = data.get("winning_option")
            if opt is None:
                opt = data.get("result")
            val = self._norm(opt, options)
            self._put(mid, val)
            return val
        except Exception:
            return None


class EspnOracleSource(BaseOracleSource):
    """真实权威源适配器：对接 ESPN 公开比分 API（无需密钥，本机网络可达）。

    仅对带 espn oracle_meta 的市场生效，避免冒充结果：
      oracle_meta = {"provider":"espn","sport":"soccer","league":"eng.1","date":"20250316"}
    - sport/league 对应 ESPN 路径（soccer/eng.1 = 英超）；date 为 yyyymmdd。
    - 仅采纳 STATUS_FINAL（post）的比赛；按队名与市场价格选项匹配，返回胜方下标。
    - 网络失败 / 无匹配 / 平局（无胜者）均返回 None，交由 manifest 或人工结算。
    带 1h TTL 缓存（同一 league+date 只拉一次）。
    """

    name = "espn"
    BASE = "https://site.api.espn.com/apis/site/v2/sports"
    CACHE_TTL = 3600.0

    def __init__(self):
        self._cache = {}
        self._cache_at = {}

    def enabled(self):
        # 始终启用：只对带 espn meta 的市场产生作用，且网络失败安全返回 None
        return True

    @staticmethod
    def _norm_options(options):
        return {str(o or "").strip().lower(): i for i, o in enumerate(options) if o}

    def _cached(self, key):
        now = time.time()
        if key in self._cache and now - self._cache_at.get(key, 0) < self.CACHE_TTL:
            return self._cache[key]
        return None

    def _put(self, key, val):
        self._cache[key] = val
        self._cache_at[key] = time.time()

    def _fetch_scoreboard(self, sport, league, date):
        """返回 {队名lower: 是否胜者}（仅终场）。带缓存。"""
        key = f"{sport}/{league}/{date}"
        cached = self._cached(key)
        if cached is not None:
            return cached
        url = f"{self.BASE}/{sport}/{league}/scoreboard?dates={date}"
        result = {}
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "Realcast-Oracle/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            for ev in data.get("events", []):
                comp = (ev.get("competitions") or [{}])[0]
                if (comp.get("status", {}).get("type", {}).get("state")) != "post":
                    continue
                for c in comp.get("competitors", []):
                    tn = (c.get("team", {}).get("displayName") or "").strip().lower()
                    if tn:
                        result[tn] = bool(c.get("winner"))
        except Exception:
            result = {}
        self._put(key, result)
        return result

    def resolve(self, market: dict) -> Optional[int]:
        meta = market.get("oracle_meta")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        if not isinstance(meta, dict) or meta.get("provider") != "espn":
            return None
        sport = meta.get("sport", "soccer")
        league = meta.get("league")
        date = meta.get("date")
        if not league or not date:
            return None
        options = market.get("options") or []
        norm = self._norm_options(options)
        if not norm:
            return None
        board = self._fetch_scoreboard(sport, league, date)
        for tn, is_win in board.items():
            if is_win and tn in norm:
                return norm[tn]
        return None


# 注册源（顺序即优先级）：manifest 本地优先，espn 真实可到达权威源次之，
# http 通用外部权威兜底。定义置于 EspnOracleSource 之后以避免前向引用。
SOURCES: List[BaseOracleSource] = [
    ManifestOracleSource(),
    EspnOracleSource(),
    HttpOracleSource(),
]


def resolve_from_sources(market_id: int):
    """依次咨询已注册 Oracle 源；返回 (winning_option, source_name) 或 (None, None)。"""
    market = markets.get_market(market_id)
    if not market:
        return None, None
    for src in SOURCES:
        if not src.enabled():
            continue
        try:
            opt = src.resolve(market)
        except Exception:
            opt = None
        if opt is not None and 0 <= opt < len(market.get("options") or []):
            return opt, src.name
    return None, None


def list_sources() -> list:
    """返回已注册源的运行状态（便于运维看板展示「Oracle 是否接了真实权威源」）。"""
    out = []
    for src in SOURCES:
        out.append({"name": src.name, "enabled": src.enabled()})
    return out
