"""Measure time to first token (TTFT) and total stream time from real local runs.

    agentcommerce bench   -> results/stream_bench.json

The model is simulated: the server waits FIRST_TOKEN_DELAY before the first event and TOKEN_DELAY
between tokens, like a model that thinks and then generates. What is measured for real is
everything around it: HTTP, SSE framing, the event loop and, in the browser, EventSource and DOM
updates. A second pass with both delays at zero shows the pure serving overhead.
"""

from __future__ import annotations

import json
import platform
import statistics
import time
from pathlib import Path

import httpx

from .localserver import serve

PROMPTS = ["buy a headlamp", "hi there", "buy a tent", "get me something for camping"]


def _stats(xs: list[float]) -> dict[str, float]:
    xs = sorted(xs)
    return {
        "p50_ms": round(statistics.median(xs), 1),
        "p95_ms": round(xs[int(0.95 * (len(xs) - 1))], 1),
        "min_ms": round(xs[0], 1),
        "n": len(xs),
    }


def http_runs(base: str, n: int) -> dict[str, object]:
    """TTFT is measured from the POST /api/chat call, so it includes run creation."""
    ttft, total, tokens = [], [], []
    with httpx.Client(base_url=base, timeout=10) as c:
        for i in range(n):
            sid = c.post("/api/session").json()["session_id"]
            t0 = time.perf_counter()
            rid = c.post("/api/chat", json={"session_id": sid, "message": PROMPTS[i % len(PROMPTS)]}).json()["run_id"]
            first, count = None, 0
            with c.stream("GET", f"/api/runs/{rid}/stream") as r:
                for line in r.iter_lines():
                    if line == "event: token":
                        count += 1
                        if first is None:
                            first = time.perf_counter()
            end = time.perf_counter()
            ttft.append((first - t0) * 1000)
            total.append((end - t0) * 1000)
            tokens.append(count)
    return {"ttft": _stats(ttft), "total": _stats(total), "tokens_per_reply_mean": round(statistics.mean(tokens), 1)}


def cancel_runs(base: str, n: int) -> dict[str, object]:
    """Cancel after the third token; measure cancel-to-'cancelled' latency and tokens received."""
    lat, received = [], []
    with httpx.Client(base_url=base, timeout=10) as c:
        for _ in range(n):
            sid = c.post("/api/session").json()["session_id"]
            rid = c.post("/api/chat", json={"session_id": sid, "message": "hi there"}).json()["run_id"]
            count, t_cancel = 0, 0.0
            with c.stream("GET", f"/api/runs/{rid}/stream") as r:
                for line in r.iter_lines():
                    if line == "event: token":
                        count += 1
                        if count == 3:
                            t_cancel = time.perf_counter()
                            httpx.post(f"{base}/api/runs/{rid}/cancel", timeout=5)
                    if line == "event: cancelled":
                        lat.append((time.perf_counter() - t_cancel) * 1000)
                        break
            received.append(count)
    return {"cancel_to_stop": _stats(lat), "tokens_received_mean": round(statistics.mean(received), 1)}


def timings(page) -> list[dict]:
    return page.evaluate("() => window.agentTiming.map((r) => ({...r}))")


def wait_runs(page, count: int, timeout_s: float = 15) -> list[dict]:
    """Poll until `count` runs exist and none is streaming. (wait_for_function with a string needs
    'unsafe-eval', which the page's CSP correctly forbids, so poll with a function instead.)"""
    end = time.time() + timeout_s
    while time.time() < end:
        t = timings(page)
        if len(t) >= count and all(r["status"] != "streaming" for r in t):
            return t
        time.sleep(0.02)
    raise TimeoutError(f"browser runs did not finish: {timings(page)}")


def browser_runs(base: str, n: int) -> dict[str, object] | None:
    """Headless Chromium; TTFT runs from EventSource creation to the first token in the DOM."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    ttft, total = [], []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page()
        page.goto(base)
        done = 0
        for i in range(n):
            page.fill("#msg", "hi there" if i % 2 else "buy a headlamp")
            page.click("#send")
            done += 1
            rec = wait_runs(page, done)[done - 1]
            ttft.append(rec["ttft_ms"])
            total.append(rec["total_ms"])
            if page.is_visible("#approval"):
                page.click("#reject")
                done += 1
                wait_runs(page, done)
        b.close()
    return {"ttft": _stats(ttft), "total": _stats(total)}


def run(n: int = 30, first_token_delay: float = 0.12, token_delay: float = 0.02) -> dict[str, object]:
    if n < 1 or first_token_delay < 0 or token_delay < 0:
        raise ValueError("n must be positive and delays non-negative")
    with serve(first_token_delay, token_delay) as (base, _):
        out: dict[str, object] = {
            "config": {"first_token_delay_ms": first_token_delay * 1000, "token_delay_ms": token_delay * 1000, "runs": n},
            "machine": {"python": platform.python_version(), "arch": platform.machine()},
            "http_client": http_runs(base, n),
            "cancel": cancel_runs(base, min(n, 20)),
            "browser": browser_runs(base, min(n, 20)),
        }
    with serve(0.0, 0.0) as (base, _):
        out["zero_delay_overhead"] = http_runs(base, n)
    return out


def write(path: Path, **kw: float) -> dict[str, object]:
    out = run(**kw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n")
    return out
