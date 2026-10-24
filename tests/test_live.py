"""Real uvicorn server on a local port: SSE timing over HTTP and a headless browser test."""

import json
import time

import httpx
import pytest

from agentcommerce import bench
from agentcommerce.localserver import serve


@pytest.fixture(scope="module")
def live():
    with serve(0.05, 0.01) as (base, app):
        yield base, app


def test_http_ttft_respects_first_token_delay(live):
    base, _ = live
    r = bench.http_runs(base, 5)
    assert r["ttft"]["min_ms"] >= 50 and r["total"]["p50_ms"] > r["ttft"]["p50_ms"]


def test_cancel_mid_stream_stops_tokens(live):
    base, _ = live
    r = bench.cancel_runs(base, 3)
    assert r["tokens_received_mean"] <= 5  # far fewer than a full reply (about 16 tokens)


def test_bench_rejects_bad_args():
    with pytest.raises(ValueError):
        bench.run(n=0)
    with pytest.raises(ValueError):
        bench.run(n=1, token_delay=-1)


playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def page(live):
    base, _ = live
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001 - browser binary missing on this machine
            pytest.skip(f"chromium not installed: {type(e).__name__}")
        pg = b.new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(base)
        pg.errors = errors
        yield pg
        b.close()


def send(page, text, count):
    page.fill("#msg", text)
    page.click("#send")
    return bench.wait_runs(page, count)


@pytest.mark.browser
def test_browser_stream_reject_approve_cancel(page, live):
    _, app = live
    t = send(page, "buy a headlamp", 1)
    assert t[0]["status"] == "done" and t[0]["ttft_ms"] >= 50
    assert page.is_visible("#approval") and "headlamp" in page.text_content("#approval-text")
    page.click("#reject")
    bench.wait_runs(page, 2)
    assert "did not do it" in page.text_content("#log")

    send(page, "buy a trail mix", 3)
    page.click("#approve")
    bench.wait_runs(page, 4)
    assert "Paid $6.50" in page.text_content("#log")
    sid = list(app.state.agents)[-1]  # the browser session is the newest
    assert app.state.agents[sid].s.service.captured_total() == 650

    page.fill("#msg", "hello there")
    page.click("#send")
    deadline = time.time() + 5
    while page.is_disabled("#cancel") and time.time() < deadline:
        time.sleep(0.005)
    page.click("#cancel")
    t = bench.wait_runs(page, 5)
    assert t[-1]["status"] == "cancelled" and "stopped by user" in page.text_content("#log")
    assert page.errors == []


@pytest.mark.browser
def test_browser_renders_markup_as_text(page):
    n = len(bench.timings(page))
    send(page, "<img src=x onerror=alert(1)>", n + 1)
    assert page.locator("#log img").count() == 0
    assert json.dumps(page.errors) == "[]"


def test_httpx_paid_flow_live(live):
    base, _ = live
    assert httpx.get(f"{base}/api/paid/report").status_code == 402
