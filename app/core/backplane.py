"""跨实例背板：限流计数器 + 实时广播（v0.7.1，报告 P2-16）。

问题
----
限流（进程内 dict）与 WS 广播（进程内 WebSocket 集合）都依赖**进程内状态**：
- 限流：N 个实例各存各的计数，实际放行量 = 限额 × N，限流形同虚设；
- 广播：消息只送达落在同一实例的连接，其它实例上的用户永远收不到推送。

这两项是阻碍水平扩展的实际障碍（SQLite 反而不是——当前量级够用）。

方案
----
抽一层 Backplane。配了 `REDIS_URL` 且装了 `redis` 包 → 走 Redis
（限流用 INCR+EXPIRE 计数窗口，广播用 Pub/Sub）；否则回退进程内实现，
行为与改造前完全一致。切换对调用方透明。

降级策略：**限流失败放行**。Redis 挂掉时宁可暂时不限流，也不能因为
一个旁路组件把全站打挂——可用性优先于节流。
"""
import asyncio
import json
import os
import time

# 本实例 ID，用于广播时丢弃自己发出的回声，避免同实例重复推送。
INSTANCE_ID = os.environ.get("INSTANCE_ID") or ("%08x" % (int(time.time()) & 0xFFFFFFFF))


class MemoryBackplane:
    """进程内实现（单实例默认路径）。"""

    name = "memory"

    def __init__(self):
        self._hits = {}
        self._last_prune = 0.0
        self._subs = []

    async def rate_check(self, key, limit, window=60):
        """固定窗口计数。返回 (是否放行, 剩余额度)。"""
        now = time.time()
        if now - self._last_prune > window:
            self._hits = {k: [t for t in v if now - t < window]
                          for k, v in self._hits.items() if v}
            self._last_prune = now
        hits = [t for t in self._hits.get(key, []) if now - t < window]
        if len(hits) >= limit:
            self._hits[key] = hits
            return False, 0
        hits.append(now)
        self._hits[key] = hits
        return True, max(0, limit - len(hits))

    async def publish(self, channel, payload):
        for q in list(self._subs):
            try:
                q.put_nowait((channel, payload))
            except asyncio.QueueFull:
                pass

    async def subscribe(self, channel):
        q = asyncio.Queue(maxsize=256)
        self._subs.append(q)
        try:
            while True:
                ch, payload = await q.get()
                if ch == channel:
                    yield payload
        finally:
            if q in self._subs:
                self._subs.remove(q)

    async def close(self):
        self._subs.clear()
        self._hits.clear()


class RedisBackplane:
    """Redis 实现（多实例部署时启用）。"""

    name = "redis"

    def __init__(self, url):
        import redis.asyncio as aioredis  # 仅在此处导入，未装 redis 包也不影响单实例
        self._mod = aioredis
        self._url = url
        self._client = None

    def _get(self):
        if self._client is None:
            self._client = self._mod.from_url(self._url, decode_responses=True)
        return self._client

    async def rate_check(self, key, limit, window=60):
        try:
            c = self._get()
            bucket = int(time.time() // window)
            rk = "rc:rl:%d:%s" % (bucket, key)
            n = await c.incr(rk)
            if n == 1:
                await c.expire(rk, int(window) + 1)  # 多留 1s，覆盖窗口边界
            return n <= limit, max(0, limit - n)
        except Exception:
            # 降级放行：旁路组件故障不应拖垮主流程
            return True, limit

    async def publish(self, channel, payload):
        try:
            await self._get().publish(
                channel, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        except Exception:
            pass

    async def subscribe(self, channel):
        """订阅频道并持续产出消息；连接断开自动退避重连。"""
        while True:
            client = None
            try:
                client = self._mod.from_url(self._url, decode_responses=True)
                ps = client.pubsub()
                await ps.subscribe(channel)
                async for msg in ps.listen():
                    if msg.get("type") != "message":
                        continue
                    try:
                        yield json.loads(msg["data"])
                    except (ValueError, TypeError):
                        continue
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(2)  # 退避重连，避免故障期打爆 Redis
            finally:
                if client is not None:
                    try:
                        await client.aclose()
                    except Exception:
                        pass

    async def close(self):
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None


_backplane = None


def get_backplane():
    """返回进程唯一的 backplane 实例。REDIS_URL 存在且依赖可用时走 Redis。"""
    global _backplane
    if _backplane is not None:
        return _backplane
    url = (os.environ.get("REDIS_URL") or "").strip()
    if url:
        try:
            _backplane = RedisBackplane(url)
        except Exception:
            _backplane = MemoryBackplane()
    else:
        _backplane = MemoryBackplane()
    return _backplane


def reset_backplane():
    """测试用：丢弃当前实例，让下次 get_backplane() 重新按环境选择。"""
    global _backplane
    _backplane = None
