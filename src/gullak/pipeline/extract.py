from __future__ import annotations

import base64
import os
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from gullak.pipeline.contracts import ExtractedLine, ExtractedPage, PhotoKind

DEFAULT_VISION_MODEL = "gemini-4-hydrogen"

_SALES_PROMPT = """You are reading a photo of a shopkeeper's handwritten daily sales register.
The register may be written in English, Hindi, Marathi, Gujarati, Tamil, Telugu, or mixed scripts,
and may use native digits (such as Devanagari ०१२३४५६७८९).
Extract:
1. page_date (YYYY-MM-DD if visible on the page, else null)
2. written_total (the total written at the bottom of the page in rupees, else null)
3. Every sale line in order:
   - line_no (1-based)
   - text (exact line text as written)
   - item_name (item name without qty/amount)
   - qty (numeric quantity; convert native digits to standard numbers)
   - unit (unit such as kg, g, ग्राम, किलो, pc, पैकेट, नग, l, ml)
   - amount (line total amount in rupees)
   - confidence (0.0 to 1.0 estimate of handwriting legibility)
Do not invent or guess missing lines. Lower confidence (< 0.7) if handwriting is unclear."""

_BILL_PROMPT = """You are reading a photo of a wholesale supplier bill / invoice for a shopkeeper.
The bill may be printed or handwritten in any Indian regional language or English.
Extract:
1. page_date (YYYY-MM-DD if visible on the bill, else null)
2. written_total (the grand total written on the bill in rupees, else null)
3. Every billed item line in order:
   - line_no (1-based)
   - text (exact line text)
   - item_name (product name)
   - qty (total quantity in base units or packs)
   - unit (unit such as kg, pc, पैकेट, बोरी, पेटी)
   - pack_qty (units per pack if shown, else null)
   - unit_price (cost per unit if shown, else null)
   - amount (line total cost in rupees)
   - confidence (0.0 to 1.0 estimate of legibility)
Do not invent or guess missing lines."""


class VisionExtractionError(RuntimeError):
    """Raised when Gemini Vision extraction cannot be performed."""


class VisionExtractionLine(BaseModel):
    line_no: int = Field(..., ge=1)
    text: str
    item_name: str = ""
    qty: float = 0.0
    unit: str = ""
    amount: float = 0.0
    unit_price: float | None = None
    pack_qty: float | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class VisionExtractionPayload(BaseModel):
    page_date: date | None = None
    written_total: float | None = None
    lines: list[VisionExtractionLine] = Field(default_factory=list)


class GeminiVisionExtractor:
    """Extracts structured register and wholesale bill lines using Gemini Vision."""

    def __init__(
        self,
        model: str = DEFAULT_VISION_MODEL,
        client: Any | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self.model = model
        self._client = client
        self.cache_dir = cache_dir
        self._cache: dict[tuple[str, PhotoKind], VisionExtractionPayload] = {}

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
            raise VisionExtractionError(
                "GEMINI_API_KEY or GOOGLE_API_KEY environment variable is required for Gemini Vision OCR."
            )
        from google import genai

        self._client = genai.Client()
        return self._client

    def _cache_file(self, image_hash: str, kind: PhotoKind) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"{image_hash}_{kind.value}.json"

    def extract_page(
        self,
        image_bytes: bytes,
        image_hash: str,
        shop_id: str,
        kind: PhotoKind,
        mime_type: str = "image/jpeg",
    ) -> ExtractedPage:
        """Extract an ExtractedPage from raw image bytes, caching results by (image_hash, kind)."""
        cache_key = (image_hash, kind)
        payload = self._cache.get(cache_key)

        if payload is None:
            disk_file = self._cache_file(image_hash, kind)
            if disk_file is not None and disk_file.exists():
                payload = VisionExtractionPayload.model_validate_json(
                    disk_file.read_text(encoding="utf-8")
                )
                self._cache[cache_key] = payload

        if payload is None:
            client = self._get_client()
            prompt = _SALES_PROMPT if kind == PhotoKind.SALES else _BILL_PROMPT
            encoded = base64.b64encode(image_bytes).decode("utf-8")

            interaction = client.interactions.create(
                model=self.model,
                input=[
                    {"type": "text", "text": prompt},
                    {"type": "image", "data": encoded, "mime_type": mime_type},
                ],
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": VisionExtractionPayload.model_json_schema(),
                },
            )
            raw_json = getattr(interaction, "output_text", None)
            if not raw_json:
                raise VisionExtractionError("Gemini Vision returned an empty response.")

            payload = VisionExtractionPayload.model_validate_json(raw_json)
            self._cache[cache_key] = payload
            if disk_file is not None:
                disk_file.parent.mkdir(parents=True, exist_ok=True)
                disk_file.write_text(payload.model_dump_json(indent=2), encoding="utf-8")

        return ExtractedPage(
            image_hash=image_hash,
            shop_id=shop_id,
            kind=kind,
            page_date=payload.page_date,
            written_total=payload.written_total,
            lines=[
                ExtractedLine(
                    line_no=line.line_no,
                    text=line.text,
                    item_name=line.item_name,
                    qty=line.qty,
                    unit=line.unit,
                    amount=line.amount,
                    unit_price=line.unit_price,
                    pack_qty=line.pack_qty,
                    confidence=line.confidence,
                )
                for line in payload.lines
            ],
        )
