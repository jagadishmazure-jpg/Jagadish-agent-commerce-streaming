"""agentcommerce serve | bench | personas"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import bench, personas


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agentcommerce")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the chat UI on http://127.0.0.1:8000")
    s.add_argument("--port", type=int, default=8000)
    b = sub.add_parser("bench", help="measure TTFT and total stream time")
    b.add_argument("--runs", type=int, default=30)
    b.add_argument("--out", type=Path, default=Path("results/stream_bench.json"))
    r = sub.add_parser("personas", help="run the persona simulator and write a pass-rate report")
    r.add_argument("--n", type=int, default=40)
    r.add_argument("--seed", type=int, default=2026)
    r.add_argument("--out", type=Path, default=Path("results/persona_report.json"))
    a = p.parse_args(argv)
    if a.cmd == "serve":
        import uvicorn

        uvicorn.run("agentcommerce.server:app", host="127.0.0.1", port=a.port)
    elif a.cmd == "bench":
        if a.runs < 1:
            p.error("--runs must be positive")
        out = bench.write(a.out, n=a.runs)
        print(json.dumps(out["http_client"], indent=2))
    else:
        if a.n < 1:
            p.error("--n must be positive")
        rep = personas.run_suite(a.n, a.seed)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(rep, indent=2) + "\n")
        for k, v in rep.items():
            print(f"{k:14s} pass {v['passed']}/{v['n']}  safety {v['safety_rate']:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
