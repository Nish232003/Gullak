import argparse
from datetime import date
from pathlib import Path

from .generator import generate
from .kit import build_kit

p = argparse.ArgumentParser(prog="python -m gullak.synth")
sub = p.add_subparsers(dest="cmd", required=True)
g = sub.add_parser("generate")
g.add_argument("--start", type=date.fromisoformat, default=date(2025, 11, 16))
g.add_argument("--days", type=int, default=365)
g.add_argument("--seed", type=int, default=42)
g.add_argument("--out", type=Path, default=Path("data/synthetic"))
k = sub.add_parser("kit")
k.add_argument("--data", type=Path, default=Path("data/synthetic"))
k.add_argument("--pages", type=int, default=30)
k.add_argument("--out", type=Path, default=Path("data/handwriting_kit"))
a = p.parse_args()

if a.cmd == "generate":
    f = generate(a.start, a.days, a.seed, a.out)
    t = f["daily_pnl_truth"]
    print(f"{len(f['sales'])} sale lines, {len(f['purchases'])} purchase lines -> {a.out}")
    print(t[["sales", "cogs", "wastage", "net_profit"]].sum().round(0).to_string())
else:
    build_kit(a.data, a.out, a.pages)
    print(f"{a.pages} pages -> {a.out}/kit.html, truth.json")
