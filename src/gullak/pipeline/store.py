from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from gullak.advice import AdviceConfig, DailyAdviceSummary, ItemInventorySnapshot, generate_daily_advice
from gullak.pipeline.contracts import (
    ExtractedLine,
    ExtractedPage,
    ItemPriceRecord,
    MatchedLine,
    PhotoKind,
    RawImageRecord,
    StockLot,
    ValidationResult,
)
from gullak.pipeline.costing import CostedSaleLine, DailyProfitMart, allocate_fifo, compute_daily_mart, update_price_scd2
from gullak.pipeline.matching import CatalogItem, ItemMatcher
from gullak.pipeline.validate import validate_page


@dataclass
class ShopProfile:
    shop_id: str
    user_id: str
    name: str
    shop_type: str = "retail"


@dataclass
class ReviewQueueEntry:
    review_id: str
    shop_id: str
    image_hash: str
    kind: PhotoKind
    page_date: date | None
    issues: list[dict[str, Any]]
    lines: list[dict[str, Any]]
    status: str = "pending"


@dataclass
class PurchaseLineRecord:
    bill_id: str
    shop_id: str
    item_id: str
    purchase_date: date
    qty: float
    unit_cost: float
    total_cost: float
    source_image_hash: str
    source_line_no: int


class GullakStore:
    """Multi-tenant in-memory + raw-disk store implementing Gullak's 10-stage pipeline."""

    def __init__(self, raw_dir: Path | None = None, seed_reference: bool = True) -> None:
        self.raw_dir = raw_dir
        self.seed_reference = seed_reference
        self.shops: dict[str, ShopProfile] = {}
        self.raw_images: dict[str, RawImageRecord] = {}
        self.matchers: dict[str, ItemMatcher] = {}
        self.stock_lots: dict[str, list[StockLot]] = {}
        self.price_history: dict[str, list[ItemPriceRecord]] = {}
        self.purchases: dict[str, list[PurchaseLineRecord]] = {}
        self.sales: dict[str, list[CostedSaleLine]] = {}
        self.unmatched_counts: dict[tuple[str, date], int] = {}
        self.review_queue: dict[str, ReviewQueueEntry] = {}
        self.pending_bills: dict[str, dict[str, Any]] = {}
        self._seq = 0

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq:06d}"

    def create_shop(self, shop_id: str, user_id: str, name: str, shop_type: str = "retail") -> ShopProfile:
        profile = ShopProfile(shop_id=shop_id, user_id=user_id, name=name, shop_type=shop_type)
        self.shops[shop_id] = profile
        if self.seed_reference:
            self.matchers[shop_id] = ItemMatcher.from_reference_csv()
        else:
            self.matchers[shop_id] = ItemMatcher(items=[])
        self.stock_lots.setdefault(shop_id, [])
        self.price_history.setdefault(shop_id, [])
        self.purchases.setdefault(shop_id, [])
        self.sales.setdefault(shop_id, [])
        return profile

    def _ensure_shop(self, shop_id: str) -> ShopProfile:
        if shop_id not in self.shops:
            return self.create_shop(shop_id=shop_id, user_id="default-user", name=shop_id)
        return self.shops[shop_id]

    def ingest_photo(self, shop_id: str, kind: PhotoKind, content: bytes, filename: str = "photo.jpg") -> tuple[RawImageRecord, bool]:
        """Store raw photo immutably by SHA-256 content hash. Returns (record, is_duplicate)."""
        self._ensure_shop(shop_id)
        image_hash = hashlib.sha256(content).hexdigest()
        key = f"{shop_id}:{image_hash}"
        if key in self.raw_images:
            return self.raw_images[key], True

        file_path = f"memory://{shop_id}/{image_hash}_{filename}"
        if self.raw_dir is not None:
            shop_dir = self.raw_dir / shop_id
            shop_dir.mkdir(parents=True, exist_ok=True)
            ext = Path(filename).suffix or ".jpg"
            dest = shop_dir / f"{image_hash}{ext}"
            dest.write_bytes(content)
            file_path = str(dest)

        rec = RawImageRecord(
            image_hash=image_hash,
            shop_id=shop_id,
            kind=kind,
            uploaded_at=datetime.now(timezone.utc),
            status="queued",
            file_path=file_path,
        )
        self.raw_images[key] = rec
        return rec, False

    def recent_avg_prices(self, shop_id: str) -> dict[str, float]:
        """Compute recent average selling prices per item name for price-outlier checks."""
        prices: dict[str, list[float]] = {}
        matcher = self.matchers.get(shop_id)
        for line in self.sales.get(shop_id, []):
            if line.qty > 0:
                unit_p = line.amount / line.qty
                prices.setdefault(line.item_id.lower(), []).append(unit_p)
                if matcher and line.item_id in matcher.items:
                    it = matcher.items[line.item_id]
                    if it.name_hi:
                        prices.setdefault(it.name_hi.lower(), []).append(unit_p)
                    if it.name_en:
                        prices.setdefault(it.name_en.lower(), []).append(unit_p)
        return {k: sum(v) / len(v) for k, v in prices.items() if v}

    def process_extracted_page(self, page: ExtractedPage) -> dict[str, Any]:
        """Run validation gates -> item matching -> FIFO costing or review queue."""
        self._ensure_shop(page.shop_id)
        matcher = self.matchers[page.shop_id]
        validation: ValidationResult = validate_page(
            page,
            recent_avg_prices=self.recent_avg_prices(page.shop_id),
        )
        norm_page = validation.normalized_page
        matched = matcher.match_page(norm_page.lines)

        has_unmatched = any(m.needs_confirm for m in matched)
        raw_key = f"{page.shop_id}:{page.image_hash}"

        if not validation.passed or has_unmatched:
            review_id = self._next_id("REV")
            self.review_queue[review_id] = ReviewQueueEntry(
                review_id=review_id,
                shop_id=page.shop_id,
                image_hash=page.image_hash,
                kind=page.kind,
                page_date=norm_page.page_date,
                issues=[i.model_dump() for i in validation.issues],
                lines=[m.model_dump() for m in matched],
            )
            if raw_key in self.raw_images:
                self.raw_images[raw_key].status = "needs_review"
            if norm_page.page_date is not None:
                unmatched_n = sum(1 for m in matched if m.item_id is None)
                self.unmatched_counts[(page.shop_id, norm_page.page_date)] = (
                    self.unmatched_counts.get((page.shop_id, norm_page.page_date), 0) + unmatched_n
                )
            return {
                "status": "needs_review",
                "review_id": review_id,
                "validation": validation.model_dump(),
                "matched_lines": [m.model_dump() for m in matched],
            }

        if norm_page.kind == PhotoKind.BILL:
            bill_id = self._next_id("BILL")
            self.pending_bills[bill_id] = {
                "bill_id": bill_id,
                "shop_id": page.shop_id,
                "image_hash": page.image_hash,
                "bill_date": norm_page.page_date,
                "lines": [m.model_dump() for m in matched],
                "status": "draft",
            }
            if raw_key in self.raw_images:
                self.raw_images[raw_key].status = "validated"
            return {
                "status": "draft_bill",
                "bill_id": bill_id,
                "matched_lines": [m.model_dump() for m in matched],
            }

        # Sales page: allocate FIFO lots
        costed = self._record_sales(page.shop_id, norm_page.page_date, page.image_hash, matched)
        if raw_key in self.raw_images:
            self.raw_images[raw_key].status = "processed"
        return {
            "status": "processed",
            "sales_recorded": len(costed),
            "cost_missing_lines": sum(1 for c in costed if c.cost_status == "cost_missing"),
        }

    def _record_sales(
        self,
        shop_id: str,
        sale_date: date,
        image_hash: str,
        matched: list[MatchedLine],
    ) -> list[CostedSaleLine]:
        costed_out: list[CostedSaleLine] = []
        shop_lots = self.stock_lots.setdefault(shop_id, [])
        for m in matched:
            if m.item_id is None:
                continue
            sale_line_id = self._next_id("SL")
            item_lots = [l for l in shop_lots if l.item_id == m.item_id]
            allocs, cogs, status = allocate_fifo(item_lots, sale_line_id=sale_line_id, qty_needed=m.qty)
            profit = round(m.amount - cogs, 2) if cogs is not None else None
            record = CostedSaleLine(
                sale_line_id=sale_line_id,
                shop_id=shop_id,
                item_id=m.item_id,
                sale_date=sale_date,
                qty=m.qty,
                amount=m.amount,
                source_image_hash=image_hash,
                source_line_no=m.line_no,
                cogs=cogs,
                profit=profit,
                cost_status=status,
                allocations=allocs,
            )
            self.sales.setdefault(shop_id, []).append(record)
            costed_out.append(record)
        return costed_out

    def approve_bill(
        self,
        bill_id: str,
        approved_lines: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Owner approves wholesale bill lines, sets selling prices, and creates FIFO stock lots."""
        bill = self.pending_bills[bill_id]
        shop_id = bill["shop_id"]
        bill_date: date = bill["bill_date"]
        image_hash: str = bill["image_hash"]
        self._ensure_shop(shop_id)

        lots_created: list[StockLot] = []
        for idx, line in enumerate(approved_lines, start=1):
            item_id = line["item_id"]
            qty = float(line["qty"])
            unit_cost = float(line["unit_cost"])
            expiry_date = line.get("expiry_date")
            if isinstance(expiry_date, str) and expiry_date:
                expiry_date = date.fromisoformat(expiry_date)
            else:
                expiry_date = None

            lot = StockLot(
                lot_id=self._next_id("LOT"),
                shop_id=shop_id,
                item_id=item_id,
                bill_id=bill_id,
                received_on=bill_date,
                qty_received=qty,
                qty_left=qty,
                unit_cost=unit_cost,
                expiry_date=expiry_date,
            )
            self.stock_lots[shop_id].append(lot)
            lots_created.append(lot)

            self.purchases[shop_id].append(
                PurchaseLineRecord(
                    bill_id=bill_id,
                    shop_id=shop_id,
                    item_id=item_id,
                    purchase_date=bill_date,
                    qty=qty,
                    unit_cost=unit_cost,
                    total_cost=round(qty * unit_cost, 2),
                    source_image_hash=image_hash,
                    source_line_no=int(line.get("line_no", idx)),
                )
            )

            if line.get("selling_price") is not None:
                update_price_scd2(
                    self.price_history[shop_id],
                    shop_id=shop_id,
                    item_id=item_id,
                    new_price=float(line["selling_price"]),
                    effective_date=bill_date,
                )

        bill["status"] = "approved"
        raw_key = f"{shop_id}:{image_hash}"
        if raw_key in self.raw_images:
            self.raw_images[raw_key].status = "processed"
        return {"bill_id": bill_id, "status": "approved", "lots_created": len(lots_created)}

    def resolve_review(
        self,
        review_id: str,
        corrected_date: date,
        corrected_lines: list[ExtractedLine],
    ) -> dict[str, Any]:
        """Owner fixes doubtful lines in review queue and reprocesses the page."""
        entry = self.review_queue[review_id]
        entry.status = "resolved"
        page = ExtractedPage(
            image_hash=entry.image_hash,
            shop_id=entry.shop_id,
            kind=entry.kind,
            page_date=corrected_date,
            written_total=sum(l.amount for l in corrected_lines),
            lines=corrected_lines,
        )
        return self.process_extracted_page(page)

    def get_daily_summary(
        self,
        shop_id: str,
        target_date: date,
        advice_config: AdviceConfig | None = None,
    ) -> dict[str, Any]:
        self._ensure_shop(shop_id)
        shop_sales = self.sales.get(shop_id, [])
        unmatched = self.unmatched_counts.get((shop_id, target_date), 0)
        mart: DailyProfitMart = compute_daily_mart(
            shop_id=shop_id,
            target_date=target_date,
            costed_lines=shop_sales,
            lines_unmatched=unmatched,
        )

        # Build inventory snapshots for restock & advice evaluation
        matcher = self.matchers[shop_id]
        lots = self.stock_lots.get(shop_id, [])
        prices = self.price_history.get(shop_id, [])

        # Recent 14-day average daily demand per item
        lookback_start = target_date.toordinal() - 13
        snapshots: list[ItemInventorySnapshot] = []
        active_item_ids = {l.item_id for l in lots} | {s.item_id for s in shop_sales}

        for item_id in sorted(active_item_ids):
            item_lots = [l for l in lots if l.item_id == item_id and l.qty_left > 1e-9]
            stock = sum(l.qty_left for l in item_lots)
            avg_cost = (
                sum(l.qty_left * l.unit_cost for l in item_lots) / stock
                if stock > 0
                else 0.0
            )
            expiries = [
                (l.expiry_date - target_date).days
                for l in item_lots
                if l.expiry_date is not None
            ]
            days_to_expiry = min(expiries) if expiries else None

            open_price = [
                p.selling_price for p in prices
                if p.item_id == item_id and p.valid_to is None
            ]
            selling_price = open_price[-1] if open_price else 0.0

            item_sales = [s for s in shop_sales if s.item_id == item_id and s.sale_date <= target_date]
            recent_qty = sum(
                s.qty for s in item_sales if s.sale_date.toordinal() >= lookback_start
            )
            daily_demand = recent_qty / 14.0
            last_sale_date = max((s.sale_date for s in item_sales), default=None)
            days_since_last = (target_date - last_sale_date).days if last_sale_date else None

            cat = matcher.items.get(item_id)
            snapshots.append(
                ItemInventorySnapshot(
                    item_id=item_id,
                    name_en=cat.name_en if cat else item_id,
                    name_hi=cat.name_hi if cat else item_id,
                    stock=stock,
                    unit=cat.base_unit if cat else "pc",
                    pack_qty=cat.pack_qty if cat else 1.0,
                    cost=avg_cost,
                    price=selling_price,
                    daily_demand=daily_demand,
                    days_since_last_sale=days_since_last,
                    days_to_expiry=days_to_expiry,
                )
            )

        advice: DailyAdviceSummary = generate_daily_advice(
            snapshots=snapshots,
            as_of=target_date,
            config=advice_config,
        )

        return {
            "shop_id": shop_id,
            "date": target_date.isoformat(),
            "sales": mart.sales,
            "cost_of_goods": mart.cost_of_goods,
            "profit": mart.profit,
            "lines_total": mart.lines_total,
            "lines_unmatched": mart.lines_unmatched,
            "cost_missing_lines": mart.cost_missing_lines,
            "best_sellers": [
                {
                    "item_id": b.item_id,
                    "qty_sold": b.qty_sold,
                    "revenue": b.revenue,
                    "profit": b.profit,
                }
                for b in mart.best_sellers
            ],
            "advice": advice.to_dict(),
        }

    def get_lineage(self, shop_id: str, sale_line_id: str) -> dict[str, Any] | None:
        """Trace a sale line's profit back to its source photo hash, line number, and FIFO lots."""
        for line in self.sales.get(shop_id, []):
            if line.sale_line_id == sale_line_id:
                return {
                    "sale_line_id": line.sale_line_id,
                    "shop_id": line.shop_id,
                    "item_id": line.item_id,
                    "sale_date": line.sale_date.isoformat(),
                    "qty": line.qty,
                    "amount": line.amount,
                    "cogs": line.cogs,
                    "profit": line.profit,
                    "cost_status": line.cost_status,
                    "source_image_hash": line.source_image_hash,
                    "source_line_no": line.source_line_no,
                    "lot_allocations": [a.model_dump() for a in line.allocations],
                }
        return None
