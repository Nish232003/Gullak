from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import pandas as pd
from rapidfuzz import fuzz, process

from gullak.pipeline.contracts import ExtractedLine, MatchedLine
from gullak.pipeline.validate import normalize_digits

REF_ITEMS_PATH = Path(__file__).resolve().parents[3] / "data" / "reference" / "items.csv"

UNIT_ALIASES: dict[str, tuple[str, float]] = {
    "kg": ("kg", 1.0),
    "kilo": ("kg", 1.0),
    "किलो": ("kg", 1.0),
    "किग्रा": ("kg", 1.0),
    "g": ("kg", 0.001),
    "gm": ("kg", 0.001),
    "gms": ("kg", 0.001),
    "gram": ("kg", 0.001),
    "grams": ("kg", 0.001),
    "ग्राम": ("kg", 0.001),
    "l": ("l", 1.0),
    "ltr": ("l", 1.0),
    "litre": ("l", 1.0),
    "liter": ("l", 1.0),
    "लीटर": ("l", 1.0),
    "ml": ("l", 0.001),
    "मिली": ("l", 0.001),
    "pc": ("pc", 1.0),
    "pcs": ("pc", 1.0),
    "piece": ("pc", 1.0),
    "pkt": ("pc", 1.0),
    "packet": ("pc", 1.0),
    "पैकेट": ("pc", 1.0),
    "नग": ("pc", 1.0),
    "बोतल": ("pc", 1.0),
    "पाउच": ("pc", 1.0),
    "डिब्बा": ("pc", 1.0),
}


def normalize_text(text: str) -> str:
    text = normalize_digits(text).strip().lower()
    return re.sub(r"\s+", " ", text)


def convert_to_base_unit(qty: float, unit: str, target_base_unit: str) -> tuple[float, str]:
    """Convert extracted quantity & unit into the item's base unit when compatible."""
    norm_u = normalize_text(unit)
    mapped = UNIT_ALIASES.get(norm_u)
    if mapped is None:
        return qty, target_base_unit or norm_u or "pc"
    canonical_dim, factor = mapped
    target = normalize_text(target_base_unit) or canonical_dim
    if canonical_dim == target:
        return round(qty * factor, 4), target
    # If item is tracked in 'pc' (e.g., 1kg salt packet) and register wrote 'पैकेट' or 'pc', factor is 1.
    if target == "pc" and canonical_dim == "pc":
        return qty, "pc"
    return qty, target


@dataclass(frozen=True)
class CatalogItem:
    item_id: str
    name_en: str
    name_hi: str
    base_unit: str
    pack_qty: float = 1.0
    status: str = "approved"


class ItemMatcher:
    """Matches Hindi/English register & bill lines to approved catalog items."""

    def __init__(
        self,
        items: list[CatalogItem],
        aliases: dict[str, str] | None = None,
        fuzzy_threshold: float = 75.0,
    ) -> None:
        self.items = {it.item_id: it for it in items}
        self.approved_items = {it.item_id: it for it in items if it.status == "approved"}
        self.aliases: dict[str, str] = {}
        self._choices: dict[str, str] = {}
        self.fuzzy_threshold = fuzzy_threshold

        # Index exact names for approved items
        self._exact_map: dict[str, str] = {}
        for it in self.approved_items.values():
            for candidate in (it.item_id, it.name_hi, it.name_en):
                key = normalize_text(candidate)
                if key:
                    self._exact_map[key] = it.item_id

        if aliases:
            for alias_text, item_id in aliases.items():
                self.add_alias(alias_text, item_id)

        self._rebuild_search_corpus()

    def _rebuild_search_corpus(self) -> None:
        self._choices: dict[str, str] = {}
        for key, item_id in self._exact_map.items():
            self._choices[key] = item_id
        for alias_key, item_id in self.aliases.items():
            if item_id in self.approved_items:
                self._choices[alias_key] = item_id

    def add_alias(self, alias_text: str, item_id: str) -> None:
        norm = normalize_text(alias_text)
        if norm and item_id in self.approved_items:
            self.aliases[norm] = item_id
            self._choices[norm] = item_id

    def add_item(self, item: CatalogItem) -> None:
        """Register or update a catalog item for any shop type."""
        self.items[item.item_id] = item
        if item.status == "approved":
            self.approved_items[item.item_id] = item
            for candidate in (item.item_id, item.name_hi, item.name_en):
                key = normalize_text(candidate)
                if key:
                    self._exact_map[key] = item.item_id
        self._rebuild_search_corpus()

    @classmethod
    def from_reference_csv(
        cls,
        csv_path: Path = REF_ITEMS_PATH,
        aliases: dict[str, str] | None = None,
        fuzzy_threshold: float = 75.0,
    ) -> "ItemMatcher":
        df = pd.read_csv(csv_path, keep_default_na=False)
        items = [
            CatalogItem(
                item_id=str(r.item_id),
                name_en=str(r.name_en),
                name_hi=str(r.name_hi),
                base_unit=str(r.unit),
                pack_qty=float(r.pack_qty),
                status="approved",
            )
            for r in df.itertuples(index=False)
        ]
        return cls(items=items, aliases=aliases, fuzzy_threshold=fuzzy_threshold)

    def match_line(self, line: ExtractedLine) -> MatchedLine:
        query = normalize_text(line.item_name or line.text)
        unit_price = round(line.amount / line.qty, 4) if line.qty > 0 else 0.0

        # 1. Exact match on item_id / name_hi / name_en
        if query in self._exact_map:
            it = self.approved_items[self._exact_map[query]]
            qty_conv, base_unit = convert_to_base_unit(line.qty, line.unit, it.base_unit)
            return MatchedLine(
                line_no=line.line_no,
                raw_text=line.text,
                item_id=it.item_id,
                matched_name=it.name_hi or it.name_en,
                qty=qty_conv,
                base_unit=base_unit,
                amount=line.amount,
                unit_price=round(line.amount / qty_conv, 4) if qty_conv > 0 else unit_price,
                match_score=100.0,
                match_method="exact",
                needs_confirm=False,
            )

        # 2. Alias table match
        if query in self.aliases and self.aliases[query] in self.approved_items:
            it = self.approved_items[self.aliases[query]]
            qty_conv, base_unit = convert_to_base_unit(line.qty, line.unit, it.base_unit)
            return MatchedLine(
                line_no=line.line_no,
                raw_text=line.text,
                item_id=it.item_id,
                matched_name=it.name_hi or it.name_en,
                qty=qty_conv,
                base_unit=base_unit,
                amount=line.amount,
                unit_price=round(line.amount / qty_conv, 4) if qty_conv > 0 else unit_price,
                match_score=100.0,
                match_method="alias",
                needs_confirm=False,
            )

        # 3. Fuzzy match
        if query and self._choices:
            res = process.extractOne(query, list(self._choices.keys()), scorer=fuzz.WRatio)
            if res is not None:
                best_key, score, _ = res
                if score >= self.fuzzy_threshold:
                    it = self.approved_items[self._choices[best_key]]
                    qty_conv, base_unit = convert_to_base_unit(line.qty, line.unit, it.base_unit)
                    return MatchedLine(
                        line_no=line.line_no,
                        raw_text=line.text,
                        item_id=it.item_id,
                        matched_name=it.name_hi or it.name_en,
                        qty=qty_conv,
                        base_unit=base_unit,
                        amount=line.amount,
                        unit_price=round(line.amount / qty_conv, 4) if qty_conv > 0 else unit_price,
                        match_score=float(round(score, 2)),
                        match_method="fuzzy",
                        needs_confirm=False,
                    )

        # 4. Unmatched -> goes to confirm list
        qty_conv, base_unit = convert_to_base_unit(line.qty, line.unit, "")
        return MatchedLine(
            line_no=line.line_no,
            raw_text=line.text,
            item_id=None,
            matched_name="",
            qty=qty_conv,
            base_unit=base_unit,
            amount=line.amount,
            unit_price=unit_price,
            match_score=0.0,
            match_method="unmatched",
            needs_confirm=True,
        )

    def match_page(self, lines: list[ExtractedLine]) -> list[MatchedLine]:
        return [self.match_line(line) for line in lines]
