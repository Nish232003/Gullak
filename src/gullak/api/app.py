from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from gullak.pipeline.contracts import ExtractedLine, ExtractedPage, PhotoKind
from gullak.pipeline.store import GullakStore


class CreateShopRequest(BaseModel):
    shop_id: str
    user_id: str = "owner-1"
    name: str
    shop_type: str = "retail"


class ApproveBillRequest(BaseModel):
    lines: list[dict[str, Any]]


class ResolveReviewRequest(BaseModel):
    page_date: date
    lines: list[ExtractedLine]


def create_app(raw_dir: Path | None = None, seed_reference: bool = True) -> FastAPI:
    app = FastAPI(title="Gullak API", version="0.1.0")
    store = GullakStore(raw_dir=raw_dir, seed_reference=seed_reference)
    app.state.store = store

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/shops")
    def create_shop(req: CreateShopRequest) -> dict[str, str]:
        shop = store.create_shop(
            shop_id=req.shop_id,
            user_id=req.user_id,
            name=req.name,
            shop_type=req.shop_type,
        )
        return {
            "shop_id": shop.shop_id,
            "user_id": shop.user_id,
            "name": shop.name,
            "shop_type": shop.shop_type,
        }

    @app.get("/shops")
    def list_shops() -> list[dict[str, str]]:
        return [
            {
                "shop_id": s.shop_id,
                "user_id": s.user_id,
                "name": s.name,
                "shop_type": s.shop_type,
            }
            for s in store.shops.values()
        ]

    @app.post("/ingest/photo")
    async def ingest_photo(
        shop_id: str = Form(...),
        kind: PhotoKind = Form(...),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Empty photo file")
        rec, is_duplicate = store.ingest_photo(
            shop_id=shop_id,
            kind=kind,
            content=content,
            filename=file.filename or "photo.jpg",
        )
        return {
            "image_hash": rec.image_hash,
            "shop_id": rec.shop_id,
            "kind": rec.kind.value,
            "status": rec.status,
            "duplicate": is_duplicate,
            "file_path": rec.file_path,
        }

    @app.post("/ingest/extracted")
    def ingest_extracted(page: ExtractedPage) -> dict[str, Any]:
        return store.process_extracted_page(page)

    @app.post("/bills/{bill_id}/approve")
    def approve_bill(bill_id: str, req: ApproveBillRequest) -> dict[str, Any]:
        if bill_id not in store.pending_bills:
            raise HTTPException(status_code=404, detail="Bill not found")
        return store.approve_bill(bill_id=bill_id, approved_lines=req.lines)

    @app.get("/shops/{shop_id}/review-queue")
    def get_review_queue(shop_id: str) -> list[dict[str, Any]]:
        return [
            {
                "review_id": r.review_id,
                "shop_id": r.shop_id,
                "image_hash": r.image_hash,
                "kind": r.kind.value,
                "page_date": r.page_date.isoformat() if r.page_date else None,
                "status": r.status,
                "issues": r.issues,
                "lines": r.lines,
            }
            for r in store.review_queue.values()
            if r.shop_id == shop_id and r.status == "pending"
        ]

    @app.post("/shops/{shop_id}/review-queue/{review_id}/resolve")
    def resolve_review(shop_id: str, review_id: str, req: ResolveReviewRequest) -> dict[str, Any]:
        entry = store.review_queue.get(review_id)
        if not entry or entry.shop_id != shop_id:
            raise HTTPException(status_code=404, detail="Review item not found")
        return store.resolve_review(
            review_id=review_id,
            corrected_date=req.page_date,
            corrected_lines=req.lines,
        )

    @app.get("/shops/{shop_id}/daily-summary")
    def daily_summary(shop_id: str, target_date: date) -> dict[str, Any]:
        return store.get_daily_summary(shop_id=shop_id, target_date=target_date)

    @app.get("/shops/{shop_id}/lineage/{sale_line_id}")
    def get_lineage(shop_id: str, sale_line_id: str) -> dict[str, Any]:
        res = store.get_lineage(shop_id=shop_id, sale_line_id=sale_line_id)
        if res is None:
            raise HTTPException(status_code=404, detail="Sale line not found")
        return res

    return app


app = create_app()
