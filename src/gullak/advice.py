from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any


class AdviceType(str, Enum):
    RESTOCK = "restock"
    DONT_RESTOCK_OVERSTOCK = "dont_restock_overstock"
    DONT_RESTOCK_NO_SALE = "dont_restock_no_sale"
    DONT_RESTOCK_EXPIRING = "dont_restock_expiring"
    WATCH_MARGIN = "watch_margin"


@dataclass(frozen=True)
class AdviceConfig:
    buffer_days: float = 2.0
    overstock_days: float = 30.0
    no_sale_days: int = 14
    expiry_warning_days: int | None = 7
    margin_warning_pct: float = 0.03
    default_days_until_next_trip: int = 1


@dataclass
class ItemInventorySnapshot:
    item_id: str
    name_en: str = ""
    name_hi: str = ""
    stock: float = 0.0
    unit: str = "pc"
    unit_hi: str = "पैकेट"
    pack_qty: float = 1.0
    pack_hi: str = "पेटी"
    cost: float = 0.0
    price: float = 0.0
    daily_demand: float = 0.0
    days_until_next_trip: int | None = None
    days_since_last_sale: int | None = None
    days_to_expiry: int | None = None


@dataclass
class AdviceItem:
    item_id: str
    name_en: str
    name_hi: str
    advice_type: AdviceType
    reason: str
    message_en: str
    message_hi: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class DailyAdviceSummary:
    as_of: date | None
    restock: list[AdviceItem] = field(default_factory=list)
    dont_restock: list[AdviceItem] = field(default_factory=list)
    watch_margin: list[AdviceItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "restock": [self._item_dict(x) for x in self.restock],
            "dont_restock": [self._item_dict(x) for x in self.dont_restock],
            "watch_margin": [self._item_dict(x) for x in self.watch_margin],
        }

    @staticmethod
    def _item_dict(item: AdviceItem) -> dict[str, Any]:
        return {
            "item_id": item.item_id,
            "name_en": item.name_en,
            "name_hi": item.name_hi,
            "advice_type": item.advice_type.value,
            "reason": item.reason,
            "message_en": item.message_en,
            "message_hi": item.message_hi,
            "details": item.details,
        }


def evaluate_item_advice(
    item: ItemInventorySnapshot,
    config: AdviceConfig | None = None,
) -> list[AdviceItem]:
    """Evaluate restock, don't-restock, and margin-warning rules for one item."""
    cfg = config or AdviceConfig()
    results: list[AdviceItem] = []

    display_hi = item.name_hi or item.name_en or item.item_id
    display_en = item.name_en or item.name_hi or item.item_id

    # 1. Watch margin (evaluated independently of inventory status)
    if item.cost > 0 and item.price >= 0:
        margin_vs_cost = (item.price - item.cost) / item.cost
        if item.price < item.cost:
            results.append(
                AdviceItem(
                    item_id=item.item_id,
                    name_en=item.name_en,
                    name_hi=item.name_hi,
                    advice_type=AdviceType.WATCH_MARGIN,
                    reason="below_cost",
                    message_en=(
                        f"{display_en}: Selling price (₹{item.price:.2f}) is below cost "
                        f"(₹{item.cost:.2f})."
                    ),
                    message_hi=(
                        f"{display_hi}: बिक्री मूल्य (₹{item.price:.2f}) लागत "
                        f"(₹{item.cost:.2f}) से कम है! दाम बढ़ाएं।"
                    ),
                    details={
                        "cost": round(item.cost, 2),
                        "price": round(item.price, 2),
                        "margin_pct": round(margin_vs_cost * 100, 2),
                    },
                )
            )
        elif margin_vs_cost <= cfg.margin_warning_pct + 1e-9:
            results.append(
                AdviceItem(
                    item_id=item.item_id,
                    name_en=item.name_en,
                    name_hi=item.name_hi,
                    advice_type=AdviceType.WATCH_MARGIN,
                    reason="thin_margin",
                    message_en=(
                        f"{display_en}: Margin is only {margin_vs_cost * 100:.1f}% "
                        f"(cost ₹{item.cost:.2f}, price ₹{item.price:.2f})."
                    ),
                    message_hi=(
                        f"{display_hi}: मार्जिन केवल {margin_vs_cost * 100:.1f}% है "
                        f"(लागत ₹{item.cost:.2f}, बिक्री ₹{item.price:.2f})।"
                    ),
                    details={
                        "cost": round(item.cost, 2),
                        "price": round(item.price, 2),
                        "margin_pct": round(margin_vs_cost * 100, 2),
                    },
                )
            )

    # 2. Don't restock rules (take precedence over restock)
    dont_restock_triggered = False

    if (
        cfg.expiry_warning_days is not None
        and item.days_to_expiry is not None
        and item.days_to_expiry <= cfg.expiry_warning_days
        and item.stock > 0
    ):
        dont_restock_triggered = True
        results.append(
            AdviceItem(
                item_id=item.item_id,
                name_en=item.name_en,
                name_hi=item.name_hi,
                advice_type=AdviceType.DONT_RESTOCK_EXPIRING,
                reason="close_to_expiry",
                message_en=(
                    f"{display_en}: Stock expires in {item.days_to_expiry} day(s). "
                    "Clear existing stock, do not restock."
                ),
                message_hi=(
                    f"{display_hi}: स्टॉक {item.days_to_expiry} दिन में एक्सपायर होगा। "
                    "अभी नया माल न मंगाएं।"
                ),
                details={
                    "stock": round(item.stock, 3),
                    "days_to_expiry": item.days_to_expiry,
                },
            )
        )

    if item.days_since_last_sale is not None and item.days_since_last_sale >= cfg.no_sale_days:
        dont_restock_triggered = True
        results.append(
            AdviceItem(
                item_id=item.item_id,
                name_en=item.name_en,
                name_hi=item.name_hi,
                advice_type=AdviceType.DONT_RESTOCK_NO_SALE,
                reason="no_recent_sale",
                message_en=(
                    f"{display_en}: No sale in the last {item.days_since_last_sale} days. "
                    "Do not restock."
                ),
                message_hi=(
                    f"{display_hi}: पिछले {item.days_since_last_sale} दिनों से कोई बिक्री नहीं। "
                    "नया माल न मंगाएं।"
                ),
                details={
                    "stock": round(item.stock, 3),
                    "days_since_last_sale": item.days_since_last_sale,
                },
            )
        )

    days_of_cover = (item.stock / item.daily_demand) if item.daily_demand > 0 else None
    if days_of_cover is not None and days_of_cover >= cfg.overstock_days:
        dont_restock_triggered = True
        results.append(
            AdviceItem(
                item_id=item.item_id,
                name_en=item.name_en,
                name_hi=item.name_hi,
                advice_type=AdviceType.DONT_RESTOCK_OVERSTOCK,
                reason="overstock",
                message_en=(
                    f"{display_en}: Stock covers {days_of_cover:.0f} days "
                    f"(>= {cfg.overstock_days:.0f} days). Do not restock."
                ),
                message_hi=(
                    f"{display_hi}: {days_of_cover:.0f} दिन का स्टॉक बाकी है। "
                    "अभी और न मंगाएं।"
                ),
                details={
                    "stock": round(item.stock, 3),
                    "days_of_cover": round(days_of_cover, 1),
                },
            )
        )

    # 3. Restock rule (only if no don't-restock condition fired)
    if not dont_restock_triggered and item.daily_demand > 0:
        trip_days = (
            item.days_until_next_trip
            if item.days_until_next_trip is not None
            else cfg.default_days_until_next_trip
        )
        target_days = trip_days + cfg.buffer_days
        demand_needed = item.daily_demand * target_days

        if item.stock + 1e-9 < demand_needed:
            deficit = demand_needed - item.stock
            pack_qty = item.pack_qty if item.pack_qty > 0 else 1.0
            suggested_packs = max(1, math.ceil(deficit / pack_qty))
            results.append(
                AdviceItem(
                    item_id=item.item_id,
                    name_en=item.name_en,
                    name_hi=item.name_hi,
                    advice_type=AdviceType.RESTOCK,
                    reason="low_stock",
                    message_en=(
                        f"{display_en}: {item.stock:g} {item.unit} left "
                        f"(covers {days_of_cover:.1f} days, need {target_days:g} days). "
                        f"Restock {suggested_packs} pack(s)."
                    ),
                    message_hi=(
                        f"{display_hi}: केवल {item.stock:g} {item.unit_hi} बचा है "
                        f"({days_of_cover:.1f} दिन के लिए)। {suggested_packs} {item.pack_hi} मंगाएं।"
                    ),
                    details={
                        "stock": round(item.stock, 3),
                        "days_of_cover": round(days_of_cover or 0.0, 2),
                        "target_days": target_days,
                        "demand_needed": round(demand_needed, 2),
                        "suggested_packs": suggested_packs,
                        "suggested_qty": round(suggested_packs * pack_qty, 2),
                    },
                )
            )

    return results


def generate_daily_advice(
    snapshots: list[ItemInventorySnapshot],
    as_of: date | None = None,
    config: AdviceConfig | None = None,
) -> DailyAdviceSummary:
    """Evaluate all shop items and group advice into restock, don't restock, and watch margin."""
    summary = DailyAdviceSummary(as_of=as_of)
    for snap in snapshots:
        for advice in evaluate_item_advice(snap, config=config):
            if advice.advice_type == AdviceType.RESTOCK:
                summary.restock.append(advice)
            elif advice.advice_type == AdviceType.WATCH_MARGIN:
                summary.watch_margin.append(advice)
            else:
                summary.dont_restock.append(advice)

    summary.restock.sort(key=lambda a: a.details.get("days_of_cover", 999))
    summary.watch_margin.sort(key=lambda a: a.details.get("margin_pct", 999))
    return summary
