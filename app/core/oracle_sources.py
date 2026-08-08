"""可插拔 Oracle 权威源（消除「结算 Oracle 只能手动」）。

设计：每个 OracleSource 实现 resolve(market) -> Optional[int]，
返回赢家 option 下标，或 None（无法判定，交由人工/保留待结算）。

平台按注册顺序尝试已启用的源；任一源给出结果即采用并留痕 source。

内置两类真实可用的适配器：
- ManifestOracleSource：读取本地结果清单（seed/运维预置），适合已知结果的
  演示/历史/可复现市场。无需外网，确定性强。
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


# 注册源（顺序即优先级）：manifest 本地优先，http 外部权威次之。
SOURCES: List[BaseOracleSource] = [ManifestOracleSource(), HttpOracleSource()]


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
