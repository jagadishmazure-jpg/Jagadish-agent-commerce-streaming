"""Regenerate the numbers in README.md and docs/ from real runs, or check they are current.

Blocks between `<!-- BEGIN:name -->` and `<!-- END:name -->` are replaced:
  personas -> pass-rate table from a fresh deterministic simulator run
  stream   -> TTFT / total / cancel table from the recorded results/stream_bench.json
  tests    -> the collected test count

    python scripts/render_docs.py          # rewrite
    python scripts/render_docs.py --check  # exit 1 if anything is stale (CI)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from count_tests import count  # noqa: E402

from agentcommerce.personas import run_suite  # noqa: E402

FILES = ["README.md", "docs/results.md", "docs/interview-points.md"]
INLINE = {"tests"}


def persona_table() -> str:
    rows = ["| Persona | Passed | Safety (no wrong money movement) |", "|---|---|---|"]
    for name, v in run_suite(40, 2026).items():
        rows.append(f"| {name} | {v['passed']}/{v['n']} ({v['rate']:.1%}) | {v['safety_rate']:.0%} |")
    return "\n".join(rows)


def _row(label: str, st: dict) -> str:
    return f"| {label} | {st['p50_ms']} ms | {st['p95_ms']} ms | {st['n']} |"


def stream_table(d: dict) -> str:
    c, h, b, z, k = d["config"], d["http_client"], d["browser"], d["zero_delay_overhead"], d["cancel"]
    rows = [
        f"Simulated model: {c['first_token_delay_ms']:.0f} ms before the first token, {c['token_delay_ms']:.0f} ms per token.",
        "",
        "| Measurement | p50 | p95 | n |",
        "|---|---|---|---|",
        _row("TTFT, HTTP client (from POST /api/chat)", h["ttft"]),
        _row("Total reply time, HTTP client", h["total"]),
    ]
    if b:
        rows += [
            _row("TTFT, headless Chromium (EventSource to first token)", b["ttft"]),
            _row("Total reply time, headless Chromium", b["total"]),
        ]
    rows += [
        _row("Cancel to `cancelled` event", k["cancel_to_stop"]),
        _row("TTFT with zero simulated delay (serving overhead)", z["ttft"]),
        _row("Total with zero simulated delay", z["total"]),
        "",
        f"Replies average {h['tokens_per_reply_mean']} tokens; "
        f"a cancel after token 3 delivered {k['tokens_received_mean']} tokens on average.",
    ]
    return "\n".join(rows)


def blocks() -> dict[str, str]:
    return {
        "personas": persona_table(),
        "stream": stream_table(json.loads((ROOT / "results" / "stream_bench.json").read_text())),
        "tests": str(count()),
    }


def render(text: str, values: dict[str, str]) -> str:
    for name, value in values.items():
        pat = re.compile(rf"(<!-- BEGIN:{name} -->)(.*?)(<!-- END:{name} -->)", re.S)
        sep = "" if name in INLINE else "\n"
        text = pat.sub(lambda m, v=value, s=sep: f"{m.group(1)}{s}{v}{s}{m.group(3)}", text)
    return text


def main(argv: list[str]) -> int:
    check = "--check" in argv
    values = blocks()
    stale = []
    for f in FILES:
        p = ROOT / f
        if not p.exists():
            continue
        old = p.read_text()
        new = render(old, values)
        if old != new:
            stale.append(f)
            if not check:
                p.write_text(new)
    if check and stale:
        print("stale generated blocks in: " + ", ".join(stale))
        return 1
    print("docs up to date" if check else "rendered: " + ", ".join(FILES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
