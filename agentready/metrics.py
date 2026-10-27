"""Before/after metrics with honest uncertainty."""

from __future__ import annotations

import math
from statistics import mean, median


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def condition_metrics(episodes: list, prices: dict | None = None) -> dict:
    n = len(episodes)
    if n == 0:
        return {"n": 0}
    solved = [e for e in episodes if e["success"]]
    lo, hi = wilson(len(solved), n)
    tokens = sum(e["prompt_tokens"] + e["completion_tokens"] for e in episodes)
    touches = [e["first_touch_step"] for e in episodes if e.get("first_touch_step")]
    out = {
        "n": n,
        "solved": len(solved),
        "success_rate": len(solved) / n,
        "ci95": [lo, hi],
        "avg_steps": mean(e["n_steps"] for e in episodes),
        "avg_steps_solved": mean(e["n_steps"] for e in solved) if solved else None,
        "median_first_touch": median(touches) if touches else None,
        "found_code_rate": len(touches) / n,
        "ran_tests_rate": sum(1 for e in episodes if e.get("ran_tests")) / n,
        "tests_tampered": sum(1 for e in episodes if e.get("tests_touched")),
        "tokens": tokens,
        "tokens_per_solved": (tokens / len(solved)) if solved else None,
        "avg_seconds": mean(e["seconds"] for e in episodes),
    }
    if prices and "super" in prices:
        pin, pout = prices["super"]
        cost = sum(e["prompt_tokens"] * pin + e["completion_tokens"] * pout for e in episodes) / 1e6
        out["cost_usd"] = cost
        out["cost_per_solved"] = cost / len(solved) if solved else None
    return out


def readiness_score(m: dict) -> int:
    """0-100. Mostly success rate, with a small efficiency component."""
    if not m or not m.get("n"):
        return 0
    eff = 0.0
    if m.get("avg_steps_solved"):
        eff = max(0.0, 1 - (m["avg_steps_solved"] - 4) / 20)  # ~4 steps is ideal, 24 is the budget
    return round(100 * (0.85 * m["success_rate"] + 0.15 * eff * m["success_rate"]))


def per_task(episodes: list) -> dict:
    table: dict = {}
    for e in episodes:
        row = table.setdefault(e["task_id"], {"baseline": [], "improved": []})
        row[e["condition"]].append(e["success"])
    return table
