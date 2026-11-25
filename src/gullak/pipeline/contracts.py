from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class PhotoKind(str, Enum):
    SALES = "sales"
    BILL = "bill"


class ExtractedLine(BaseModel):
    line_no: int = Field(..., ge=1)
    text: str
    item_name: str = ""
    qty: float = 0.0
    unit: str = ""
    amount: float = 0.0
    unit_price: float | None = None
    pack_qty: float | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ExtractedPage(BaseModel):
    image_hash: str
    shop_id: str
    kind: PhotoKind
    page_date: date | None = None
    written_total: float | None = None
    lines: list[ExtractedLine] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    line_no: int | None = None
    code: str
    message: str


class ValidationResult(BaseModel):
    passed: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    normalized_page: ExtractedPage
    doubtful_lines: list[int] = Field(default_factory=list)


class MatchedLine(BaseModel):
    line_no: int
    raw_text: str
    item_id: str | None = None
    matched_name: str = ""
    qty: float
    base_unit: str
    amount: float
    unit_price: float
    match_score: float = 0.0
    match_method: Literal["exact", "alias", "fuzzy", "unmatched"] = "unmatched"
    needs_confirm: bool = False


class StockLot(BaseModel):
    lot_id: str
    shop_id: str
    item_id: str
    bill_id: str
    received_on: date
    qty_received: float
    qty_left: float
    unit_cost: float
    expiry_date: date | None = None


class SaleLotAllocation(BaseModel):
    sale_line_id: str
    lot_id: str
    qty: float
    unit_cost: float


class ItemPriceRecord(BaseModel):
    item_id: str
    shop_id: str
    selling_price: float
    valid_from: date
    valid_to: date | None = None


class RawImageRecord(BaseModel):
    image_hash: str
    shop_id: str
    kind: PhotoKind
    uploaded_at: datetime
    status: Literal["queued", "validated", "needs_review", "processed"] = "queued"
    file_path: str
