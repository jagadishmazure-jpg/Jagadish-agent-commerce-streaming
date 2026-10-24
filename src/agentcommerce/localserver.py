"""Run the app with uvicorn in a background thread on a free local port (tests and benchmarks)."""

from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager

import uvicorn

from .server import create_app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def serve(first_token_delay: float = 0.12, token_delay: float = 0.02):
    port = free_port()
    app = create_app(first_token_delay=first_token_delay, token_delay=token_delay)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}", app
    finally:
        server.should_exit = True
        t.join(timeout=5)
