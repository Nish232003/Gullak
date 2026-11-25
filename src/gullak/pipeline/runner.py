from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from gullak.pipeline.contracts import ExtractedLine, ExtractedPage, PhotoKind, StockLot
from gullak.pipeline.matching import ItemMatcher
from gullak.pipeline.store import GullakStore
from gullak.synth.generator import Simulator, load_reference


def run_synthetic_pipeline(
    start: date = date(2026, 9, 1),
    days: int = 30,
    seed: int = 1,
    shop_id: str = "kirana-01",
) -> dict:
    """Replay synthetic shop data through Gullak's validation, matching, FIFO, and advice pipeline."""
    frames = Simulator(start=start, days=days, seed=seed).run()
    items_df, _ = load_reference()
    item_lookup = items_df.set_index("item_id").to_dict("index")

    store = GullakStore(seed_reference=True)
    store.create_shop(shop_id=shop_id, user_id="owner-1", name="शर्मा किराना स्टोर")

    # Seed opening stock lots
    opening_date = start - timedelta(days=1)
    for row in frames["opening"].itertuples(index=False):
        meta = item_lookup.get(row.item_id, {})
        shelf = int(meta.get("shelf_days", 0))
        expiry = opening_date + timedelta(days=shelf - 1) if shelf > 0 else None
        store.stock_lots[shop_id].append(
            StockLot(
                lot_id=store._next_id("LOT"),
                shop_id=shop_id,
                item_id=row.item_id,
                bill_id="OPENING",
                received_on=opening_date,
                qty_received=float(row.qty),
                qty_left=float(row.qty),
                unit_cost=float(row.unit_cost),
                expiry_date=expiry,
            )
        )

    purchases_by_day = frames["purchases"].groupby("date")
    sales_by_day = frames["sales"].groupby("date")

    daily_summaries = []
    for n in range(days):
        d = start + timedelta(days=n)

        # Ingest wholesale bills for day d
        if d in purchases_by_day.groups:
            p_df = purchases_by_day.get_group(d)
            for bill_id, bill_df in p_df.groupby("bill_id"):
                lines = [
                    {
                        "line_no": idx,
                        "item_id": r.item_id,
                        "qty": float(r.qty),
                        "unit_cost": float(r.unit_cost),
                        "selling_price": float(item_lookup[r.item_id]["price"]) if r.item_id in item_lookup else None,
                    }
                    for idx, r in enumerate(bill_df.itertuples(index=False), start=1)
                ]
                store.pending_bills[bill_id] = {
                    "bill_id": bill_id,
                    "shop_id": shop_id,
                    "image_hash": f"synth-bill-{bill_id}",
                    "bill_date": d,
                    "lines": lines,
                    "status": "draft",
                }
                store.approve_bill(bill_id, lines)

        # Ingest sales register lines for day d through validation + matching + FIFO
        if d in sales_by_day.groups:
            s_df = sales_by_day.get_group(d)
            ext_lines = [
                ExtractedLine(
                    line_no=idx,
                    text=f"{r.name_hi}  {r.qty:g} {r.unit_hi}  {int(r.amount)}",
                    item_name=str(r.name_hi),
                    qty=float(r.qty),
                    unit=str(r.unit_hi),
                    amount=float(r.amount),
                    confidence=1.0,
                )
                for idx, r in enumerate(s_df.itertuples(index=False), start=1)
            ]
            page = ExtractedPage(
                image_hash=f"synth-sales-{d.isoformat()}",
                shop_id=shop_id,
                kind=PhotoKind.SALES,
                page_date=d,
                written_total=float(s_df.amount.sum()),
                lines=ext_lines,
            )
            store.process_extracted_page(page)

        summary = store.get_daily_summary(shop_id=shop_id, target_date=d)
        daily_summaries.append(summary)

    truth_df = frames["daily_pnl_truth"]
    return {
        "days_processed": len(daily_summaries),
        "total_sales": round(sum(s["sales"] for s in daily_summaries), 2),
        "truth_sales": round(float(truth_df["sales"].sum()), 2),
        "total_gross_profit_fifo": round(sum(s["profit"] for s in daily_summaries), 2),
        "last_day_summary": daily_summaries[-1],
    }


def benchmark_handwriting_kit(truth_path: Path) -> dict:
    """Evaluate validation + Hindi item matching on the 30-page handwriting kit truth.json."""
    pages = json.loads(truth_path.read_text(encoding="utf-8"))
    matcher = ItemMatcher.from_reference_csv()

    total_lines = 0
    matched_lines = 0
    exact_item_hits = 0

    for p in pages:
        ext_lines = [
            ExtractedLine(
                line_no=idx,
                text=l["text"],
                item_name="",
                qty=0.0,
                unit="",
                amount=0.0,
            )
            for idx, l in enumerate(p["lines"], start=1)
        ]
        page = ExtractedPage(
            image_hash=f"kit-page-{p['page']:02d}",
            shop_id="kit-bench",
            kind=PhotoKind.SALES,
            page_date=date.fromisoformat(p["date"]),
            written_total=float(p["total"]),
            lines=ext_lines,
        )
        from gullak.pipeline.validate import validate_page

        res = validate_page(page)
        matched = matcher.match_page(res.normalized_page.lines)
        for orig, m in zip(p["lines"], matched):
            total_lines += 1
            if not m.needs_confirm and m.item_id is not None:
                matched_lines += 1
            if m.item_id == orig["item_id"]:
                exact_item_hits += 1

    return {
        "pages": len(pages),
        "total_lines": total_lines,
        "auto_matched_pct": round(100.0 * matched_lines / max(1, total_lines), 2),
        "exact_item_accuracy_pct": round(100.0 * exact_item_hits / max(1, total_lines), 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Gullak synthetic pipeline & kit benchmark")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--kit-truth", type=Path, default=Path("data/handwriting_kit/truth.json"))
    args = parser.parse_args()

    res = run_synthetic_pipeline(days=args.days)
    print(json.dumps({
        "synthetic_run": {
            "days_processed": res["days_processed"],
            "total_sales": res["total_sales"],
            "truth_sales": res["truth_sales"],
            "total_gross_profit_fifo": res["total_gross_profit_fifo"],
            "last_day": res["last_day_summary"]["date"],
            "last_day_restock_count": len(res["last_day_summary"]["advice"]["restock"]),
            "last_day_dont_restock_count": len(res["last_day_summary"]["advice"]["dont_restock"]),
            "last_day_watch_margin_count": len(res["last_day_summary"]["advice"]["watch_margin"]),
        }
    }, ensure_ascii=False, indent=2))

    if args.kit_truth.exists():
        bench = benchmark_handwriting_kit(args.kit_truth)
        print(json.dumps({"handwriting_kit_benchmark": bench}, indent=2))


if __name__ == "__main__":
    main()
