from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np
import pandas as pd

DEVANAGARI_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")


def qty_text(qty: float, unit: str, unit_hi: str) -> str:
    if unit == "kg" and qty < 1:
        return f"{int(round(qty * 1000))} ग्राम"
    return f"{qty:g} {unit_hi}"


def build_kit(data_dir: Path, out_dir: Path, pages: int = 30, lines_per_page: int = 15, seed: int = 7) -> list[dict]:
    rng = np.random.default_rng(seed)
    sales = pd.read_csv(data_dir / "sales.csv")
    days = sorted(sales.date.unique())
    kit = []
    for n in range(pages):
        day = days[int(rng.integers(len(days)))]
        rows = sales[sales.date == day].reset_index(drop=True)
        first = int(rng.integers(max(1, len(rows) - lines_per_page)))
        rows = rows.iloc[first:first + lines_per_page]
        native = bool(rng.random() < 0.2)
        lines = [
            {"item_id": r.item_id, "name_hi": r.name_hi, "qty": r.qty, "unit": r.unit,
             "text": f"{r.name_hi}  {qty_text(r.qty, r.unit, r.unit_hi)}  {r.amount}", "amount": int(r.amount)}
            for r in rows.itertuples(index=False)
        ]
        page = {"page": n + 1, "date": day, "native_digits": native, "lines": lines,
                "total": sum(l["amount"] for l in lines)}
        kit.append(page)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "truth.json").write_text(json.dumps(kit, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "kit.html").write_text(render_html(kit), encoding="utf-8")
    return kit


def render(text: str, native: bool) -> str:
    return text.translate(DEVANAGARI_DIGITS) if native else text


def render_html(kit: list[dict]) -> str:
    css = ("body{font-family:'Noto Sans Devanagari',sans-serif;margin:0}"
           ".p{page-break-after:always;padding:24px 32px}h2{margin:0 0 4px}"
           ".m{color:#666;font-size:12px;margin-bottom:12px}li{font-size:22px;line-height:1.8}"
           ".t{font-size:22px;font-weight:bold;margin-top:8px}")
    parts = [f"<!DOCTYPE html><html lang='hi'><meta charset='utf-8'><title>Handwriting kit</title><style>{css}</style>"]
    for p in kit:
        nat = p["native_digits"]
        items = "".join(f"<li>{html.escape(render(l['text'], nat))}</li>" for l in p["lines"])
        parts.append(
            f"<div class='p'><h2>पेज {render(str(p['page']), nat)} · {render(p['date'], nat)}</h2>"
            f"<div class='m'>Copy these lines by hand on paper, register-style. Photo name: page_{p['page']:02d}.jpg</div>"
            f"<ol>{items}</ol><div class='t'>कुल: {render(str(p['total']), nat)}</div></div>"
        )
    return "".join(parts) + "</html>"
