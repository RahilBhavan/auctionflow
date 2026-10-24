"""Render the synthetic replay journal as an SVG (standard library only).

Usage: render_replay_figure.py EVENTS.jsonl DECISIONS.jsonl OUTPUT.svg
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

W, H = 760, 330
L, R, T, B = 70, 30, 70, 60
INK, MUTED, ACCENT = "#1b1b1b", "#6b6b6b", "#b3261e"


def main(events_path: Path, decisions_path: Path, output: Path) -> None:
    events = [json.loads(x) for x in events_path.read_text().splitlines() if x]
    decisions = {d["event_id"]: d for d in map(json.loads, decisions_path.read_text().splitlines()) if d}
    prices = [e["price_ticks"] for e in events]
    lo, hi = min(prices) - 1, max(prices) + 1
    n = len(events)

    def x(i: int) -> float:
        return L + (W - L - R) * i / (n - 1)

    def y(p: float) -> float:
        return T + (H - T - B) * (hi - p) / (hi - lo)

    out = [
        (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        'font-family="Helvetica, Arial, sans-serif" role="img" '
        'aria-labelledby="t d"><title id="t">Synthetic replay: five WATCH entries, then one simulated AUTHORIZE_2_MES</title>'
        '<desc id="d">Price in ticks per event with the journal decision at each event.</desc>'),
        f'<rect width="{W}" height="{H}" fill="#fff"/>',
        f'<text x="{L}" y="26" font-size="16" font-weight="700" fill="{INK}">One synthetic session: five WATCH entries, then one simulated alert</text>',
        f'<text x="{L}" y="46" font-size="12" fill="{MUTED}">tests/fixtures/acceptance_breakout.jsonl (synthetic, not market data); decisions from `auctionflow replay`</text>',
    ]
    for p in range(lo + 1, hi, 2):
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(p):.1f}" y2="{y(p):.1f}" stroke="#e6e6e6"/>')
        out.append(f'<text x="{L - 8}" y="{y(p) + 4:.1f}" font-size="11" text-anchor="end" fill="{MUTED}">{p}</text>')
    out.append(f'<text x="14" y="{T - 4}" font-size="11" text-anchor="start" fill="{MUTED}">price (ticks)</text>')
    pts = " ".join(f"{x(i):.1f},{y(p):.1f}" for i, p in enumerate(prices))
    out.append(f'<polyline points="{pts}" fill="none" stroke="{INK}" stroke-width="1.5"/>')
    for i, e in enumerate(events):
        d = decisions[e["event_id"]]
        auth = d["decision"].startswith("AUTHORIZE")
        fill, stroke = (ACCENT, ACCENT) if auth else ("#fff", INK)
        out.append(f'<circle cx="{x(i):.1f}" cy="{y(e["price_ticks"]):.1f}" r="{6 if auth else 4.5}" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
        hhmm = e["timestamp"][11:16]
        out.append(f'<text x="{x(i):.1f}" y="{H - B + 20}" font-size="11" text-anchor="middle" fill="{MUTED}">{hhmm}Z</text>')
        label = d["decision"] + (f" ({d['risk']['quantity']} MES, entry {d['risk']['entry_ticks']})" if auth else "")
        anchor = "end" if auth else "middle"
        dx = -12 if auth else 0
        below = i > 0 and prices[i] < prices[i - 1]
        dy = 4 if auth else (22 if below else -14)
        out.append(f'<text x="{x(i) + dx:.1f}" y="{y(e["price_ticks"]) + dy:.1f}" font-size="11" text-anchor="{anchor}" fill="{ACCENT if auth else INK}" font-weight="{700 if auth else 400}">{label}</text>')
    out.append(f'<text x="{L}" y="{H - 10}" font-size="11" fill="{MUTED}">Event time, 2025-01-02 (UTC). AUTHORIZE is a journal entry from a simulation, not an order.</text>')
    out.append("</svg>")
    output.write_text("\n".join(out) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
