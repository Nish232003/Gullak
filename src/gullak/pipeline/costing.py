from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from gullak.pipeline.contracts import ItemPriceRecord, SaleLotAllocation, StockLot


@dataclass
class CostedSaleLine:
    sale_line_id: str
    shop_id: str
    item_id: str
    sale_date: date
    qty: float
    amount: float
    source_image_hash: str
    source_line_no: int
    cogs: float | None
    profit: float | None
    cost_status: Literal["ok", "cost_missing"]
    allocations: list[SaleLotAllocation] = field(default_factory=list)


@dataclass
class BestSeller:
    item_id: str
    qty_sold: float
    revenue: float
    profit: float


@dataclass
class DailyProfitMart:
    shop_id: str
    date: date
    sales: float
    cost_of_goods: float
    profit: float
    lines_total: int
    lines_unmatched: int
    cost_missing_lines: int
    best_sellers: list[BestSeller] = field(default_factory=list)


def allocate_fifo(
    lots: list[StockLot],
    sale_line_id: str,
    qty_needed: float,
) -> tuple[list[SaleLotAllocation], float | None, Literal["ok", "cost_missing"]]:
    """Allocate sold quantity across oldest available stock lots (FIFO).

    Mutates `qty_left` on the passed `StockLot` objects in chronological order.
    If there is not enough stock across all lots, returns `cost_status="cost_missing"`
    and `cogs=None` (never guesses cost).
    """
    ordered = sorted(
        (lot for lot in lots if lot.qty_left > 1e-9),
        key=lambda l: (l.received_on, l.lot_id),
    )

    total_available = sum(l.qty_left for l in ordered)
    if total_available + 1e-9 < qty_needed:
        return [], None, "cost_missing"

    remaining = qty_needed
    allocations: list[SaleLotAllocation] = []
    total_cost = 0.0

    for lot in ordered:
        if remaining <= 1e-9:
            break
        take = min(lot.qty_left, remaining)
        lot.qty_left = round(lot.qty_left - take, 6)
        remaining = round(remaining - take, 6)
        total_cost += take * lot.unit_cost
        allocations.append(
            SaleLotAllocation(
                sale_line_id=sale_line_id,
                lot_id=lot.lot_id,
                qty=round(take, 6),
                unit_cost=lot.unit_cost,
            )
        )

    return allocations, round(total_cost, 2), "ok"


def update_price_scd2(
    history: list[ItemPriceRecord],
    shop_id: str,
    item_id: str,
    new_price: float,
    effective_date: date,
) -> list[ItemPriceRecord]:
    """Update SCD Type 2 selling price history for a shop item."""
    open_records = [
        r for r in history
        if r.shop_id == shop_id and r.item_id == item_id and r.valid_to is None
    ]
    if open_records:
        current = open_records[-1]
        if abs(current.selling_price - new_price) < 1e-6:
            return history
        current.valid_to = effective_date

    history.append(
        ItemPriceRecord(
            item_id=item_id,
            shop_id=shop_id,
            selling_price=round(new_price, 2),
            valid_from=effective_date,
            valid_to=None,
        )
    )
    return history


def compute_daily_mart(
    shop_id: str,
    target_date: date,
    costed_lines: list[CostedSaleLine],
    lines_unmatched: int = 0,
    top_n: int = 5,
) -> DailyProfitMart:
    """Aggregate daily P&L and top N best sellers for a shop and date."""
    day_lines = [l for l in costed_lines if l.shop_id == shop_id and l.sale_date == target_date]
    total_sales = 0.0
    total_cogs = 0.0
    cost_missing_lines = 0
    by_item: dict[str, dict[str, float]] = {}

    for line in day_lines:
        total_sales += line.amount
        if line.cost_status == "cost_missing" or line.cogs is None:
            cost_missing_lines += 1
            item_cogs = 0.0
            item_profit = 0.0
        else:
            item_cogs = line.cogs
            item_profit = line.amount - line.cogs
            total_cogs += item_cogs

        agg = by_item.setdefault(line.item_id, {"qty": 0.0, "revenue": 0.0, "profit": 0.0})
        agg["qty"] += line.qty
        agg["revenue"] += line.amount
        agg["profit"] += item_profit

    best = sorted(
        (
            BestSeller(
                item_id=item_id,
                qty_sold=round(vals["qty"], 3),
                revenue=round(vals["revenue"], 2),
                profit=round(vals["profit"], 2),
            )
            for item_id, vals in by_item.items()
        ),
        key=lambda b: (b.revenue, b.profit),
        reverse=True,
    )[:top_n]

    return DailyProfitMart(
        shop_id=shop_id,
        date=target_date,
        sales=round(total_sales, 2),
        cost_of_goods=round(total_cogs, 2),
        profit=round(total_sales - total_cogs, 2),
        lines_total=len(day_lines) + lines_unmatched,
        lines_unmatched=lines_unmatched,
        cost_missing_lines=cost_missing_lines,
        best_sellers=best,
    )
