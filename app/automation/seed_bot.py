"""初始概率播种机器人（P3，合规只读版）。

借鉴 poly-maker 的「流动性 / 初始概率播种」思路，但**只播「参考水位」，不替用户下注**：

- 对尚无外部参考的新开市场，若配置了 EXTERNAL_REF_ENDPOINT，自动拉取外部公开概率
  写入 markets.external_ref_*，作为冷启动的「参考水位」展示（见 core/external_ref.py 合规边界）。
- 绝不调用 participate / 结算 / 任何写分逻辑——只写 external_ref 列，与 settlement 零耦合。
- 未配置端点时整体 no-op，安全。

运行：python -m automation.seed_bot   （建议由 Railway cron / 定时任务周期性调用）
"""
from __future__ import annotations
import json

from db import get_conn
from core import external_ref


def seed_once(limit: int = 50) -> dict:
    """对新开且尚无外部参考的市场，拉取一次只读外部概率作为参考水位。

    返回统计；未配置端点时返回 ran=False（安全 no-op）。
    """
    if not external_ref.enabled():
        return {"ran": False, "reason": "EXTERNAL_REF_ENDPOINT 未配置，跳过（安全 no-op）"}
    refreshed = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM markets WHERE status='open' "
            "AND external_ref_prob IS NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    for r in rows:
        ref = external_ref.refresh(r["id"])
        if ref:
            refreshed += 1
    return {"ran": True, "checked": len(rows), "refreshed": refreshed}


if __name__ == "__main__":
    print(json.dumps(seed_once(), ensure_ascii=False, indent=2))
