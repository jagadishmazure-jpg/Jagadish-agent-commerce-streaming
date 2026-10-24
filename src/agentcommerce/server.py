"""FastAPI app: SSE token and tool-event streaming, approve/reject for risky actions, cancel.

    uvicorn agentcommerce.server:app        (or: agentcommerce serve)

Endpoints
  POST /api/session                     -> {"session_id"}
  POST /api/chat     {session_id, message}  -> {"run_id"}
  POST /api/decision {session_id, approve}  -> {"run_id"}
  GET  /api/runs/{run_id}/stream        -> text/event-stream
  POST /api/runs/{run_id}/cancel        -> {"cancelled": true}
  GET  /api/paid/report                 -> 402 quote, or 200 with a valid X-Payment proof
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .agent import CommerceAgent, Event, new_session
from .pii import SafeError, get_logger, scrub
from .x402 import PaywalledResource

log = get_logger("agentcommerce.server")
STATIC = Path(__file__).parent / "static"
MAX_RUNS = 500


def _delay(name: str, default: float) -> float:
    v = float(os.environ.get(name, default))
    if v < 0:
        raise SafeError(f"{name} must not be negative")
    return v


@dataclass
class Run:
    events: Iterator[Event]
    session_id: str
    created: float = field(default_factory=time.monotonic)
    cancelled: bool = False
    started: bool = False
    approval_sent: bool = False


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=8, max_length=64)
    message: str = Field(min_length=1, max_length=2000)


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=8, max_length=64)
    approve: bool


def create_app(first_token_delay: float | None = None, token_delay: float | None = None) -> FastAPI:
    app = FastAPI(title="agent commerce streaming lab", docs_url=None, redoc_url=None)
    first = _delay("FIRST_TOKEN_DELAY_S", 0.12) if first_token_delay is None else first_token_delay
    per = _delay("TOKEN_DELAY_S", 0.02) if token_delay is None else token_delay
    if first < 0 or per < 0:
        raise SafeError("delays must not be negative")
    user_key = os.environ.get("AGENT_USER_KEY", "").encode() or secrets.token_bytes(32)
    agents: dict[str, CommerceAgent] = {}
    runs: dict[str, Run] = {}
    paywall = PaywalledResource(user_key, "larkspur-books", 25, "/api/paid/report")
    app.state.agents, app.state.runs, app.state.paywall, app.state.user_key = agents, runs, paywall, user_key

    def agent_for(sid: str) -> CommerceAgent:
        if sid not in agents:
            raise HTTPException(404, "unknown session")
        return agents[sid]

    def new_run(events: Iterator[Event], sid: str) -> dict:
        if len(runs) >= MAX_RUNS:
            oldest = min(runs, key=lambda k: runs[k].created)
            runs.pop(oldest)
        rid = "run_" + secrets.token_hex(8)
        runs[rid] = Run(events, sid)
        return {"run_id": rid}

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    @app.exception_handler(SafeError)
    async def safe_error(_: Request, exc: SafeError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/app.js")
    async def js() -> FileResponse:
        return FileResponse(STATIC / "app.js", media_type="text/javascript")

    @app.get("/app.css")
    async def css() -> FileResponse:
        return FileResponse(STATIC / "app.css", media_type="text/css")

    @app.post("/api/session")
    async def session() -> dict:
        sid = "s_" + secrets.token_hex(8)
        agents[sid] = CommerceAgent(new_session(user_key))
        return {"session_id": sid}

    @app.post("/api/chat")
    async def chat(body: ChatIn) -> dict:
        a = agent_for(body.session_id)
        log.info("chat %s: %s", body.session_id, body.message)
        return new_run(iter(list(a.respond(body.message))), body.session_id)

    @app.post("/api/decision")
    async def decision(body: DecisionIn) -> dict:
        a = agent_for(body.session_id)
        return new_run(iter(list(a.decide(body.approve))), body.session_id)

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel(run_id: str) -> dict:
        if run_id not in runs:
            raise HTTPException(404, "unknown run")
        runs[run_id].cancelled = True
        return {"cancelled": True}

    async def sse(run_id: str, run: Run) -> AsyncIterator[str]:
        await asyncio.sleep(first)
        first_token = True
        for ev in run.events:
            if run.cancelled:
                a = agents.get(run.session_id)
                if a is not None and a.s.pending is not None and not run.approval_sent:
                    a.s.pending = None  # never act later on an approval prompt the user never saw
                yield f"event: cancelled\ndata: {json.dumps({'run_id': run_id})}\n\n"
                runs.pop(run_id, None)
                return
            if ev.type == "token":
                if not first_token:
                    await asyncio.sleep(per)
                first_token = False
            if ev.type == "approval_required":
                run.approval_sent = True
            data = {k: (scrub(v) if isinstance(v, str) else v) for k, v in ev.data.items()}
            yield f"event: {ev.type}\ndata: {json.dumps(data)}\n\n"
        runs.pop(run_id, None)

    @app.get("/api/runs/{run_id}/stream")
    async def stream(run_id: str) -> StreamingResponse:
        run = runs.get(run_id)
        if run is None or run.started:
            raise HTTPException(404, "unknown or already streamed run")
        run.started = True
        return StreamingResponse(
            sse(run_id, run), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
        )

    @app.get("/api/paid/report")
    async def paid(request: Request):
        raw = request.headers.get("x-payment")
        if not raw:
            q = paywall.quote(time.time())
            return JSONResponse({"error": "payment required", "quote": {**q.__dict__}}, status_code=402)
        try:
            proof = json.loads(raw)
            if not isinstance(proof, dict):
                raise ValueError
            content = paywall.redeem(proof, time.time())
        except (ValueError, SafeError) as e:
            msg = str(e) if isinstance(e, SafeError) else "malformed payment proof"
            return JSONResponse({"error": msg}, status_code=402)
        return {"report": content}

    return app


app = create_app()
