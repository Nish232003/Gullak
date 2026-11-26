import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from gullak.api.app import create_app
from gullak.pipeline.contracts import PhotoKind
from gullak.pipeline.extract import GeminiVisionExtractor, VisionExtractionError


class _FakeInteractions:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return SimpleNamespace(output_text=json.dumps(self.payload, ensure_ascii=False))


class _FakeClient:
    def __init__(self, payload: dict) -> None:
        self.interactions = _FakeInteractions(payload)


def test_gemini_vision_extractor_and_hash_cache(tmp_path):
    fake_client = _FakeClient(
        {
            "page_date": "2026-11-25",
            "written_total": 76.0,
            "lines": [
                {
                    "line_no": 1,
                    "text": "आलू  १ किलो  २७",
                    "item_name": "आलू",
                    "qty": 1.0,
                    "unit": "किलो",
                    "amount": 27.0,
                    "confidence": 0.95,
                },
                {
                    "line_no": 2,
                    "text": "चीनी  १ किलो  ४९",
                    "item_name": "चीनी",
                    "qty": 1.0,
                    "unit": "किलो",
                    "amount": 49.0,
                    "confidence": 0.92,
                },
            ],
        }
    )
    extractor = GeminiVisionExtractor(client=fake_client, cache_dir=tmp_path / "cache")

    page1 = extractor.extract_page(
        image_bytes=b"img-bytes",
        image_hash="hash-001",
        shop_id="shop-1",
        kind=PhotoKind.SALES,
    )
    assert page1.written_total == 76.0
    assert len(page1.lines) == 2
    assert fake_client.interactions.calls == 1
    assert fake_client.interactions.last_kwargs["model"] == "gemini-4-hydrogen"

    # Second call with same hash uses in-memory cache
    page2 = extractor.extract_page(
        image_bytes=b"img-bytes",
        image_hash="hash-001",
        shop_id="shop-1",
        kind=PhotoKind.SALES,
    )
    assert page2 == page1
    assert fake_client.interactions.calls == 1

    # Fresh extractor instance with same cache_dir uses disk cache
    extractor2 = GeminiVisionExtractor(client=fake_client, cache_dir=tmp_path / "cache")
    page3 = extractor2.extract_page(
        image_bytes=b"img-bytes",
        image_hash="hash-001",
        shop_id="shop-1",
        kind=PhotoKind.SALES,
    )
    assert page3 == page1
    assert fake_client.interactions.calls == 1


def test_missing_api_key_raises_and_returns_503(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    extractor = GeminiVisionExtractor()
    with pytest.raises(VisionExtractionError):
        extractor.extract_page(b"bytes", "h1", "s1", PhotoKind.SALES)

    app = create_app()
    client = TestClient(app)
    res = client.post(
        "/ingest/photo",
        data={"shop_id": "s1", "kind": "sales", "auto_extract": "true"},
        files={"file": ("register.jpg", b"fake-image", "image/jpeg")},
    )
    assert res.status_code == 503


def test_api_photo_upload_with_auto_extract_end_to_end(tmp_path):
    fake_client = _FakeClient(
        {
            "page_date": "2026-11-25",
            "written_total": 48.0,
            "lines": [
                {
                    "line_no": 1,
                    "text": "चीनी 1 किलो 48",
                    "item_name": "चीनी",
                    "qty": 1.0,
                    "unit": "किलो",
                    "amount": 48.0,
                    "confidence": 0.96,
                }
            ],
        }
    )
    extractor = GeminiVisionExtractor(client=fake_client, cache_dir=tmp_path / "ocr")
    app = create_app(raw_dir=tmp_path / "raw", extractor=extractor)
    client = TestClient(app)

    # Seed stock via bill approval
    bill = client.post(
        "/ingest/extracted",
        json={
            "image_hash": "bill-1",
            "shop_id": "shop-ocr",
            "kind": "bill",
            "page_date": "2026-11-24",
            "written_total": 400.0,
            "lines": [
                {"line_no": 1, "text": "चीनी 10 किलो 400", "item_name": "चीनी", "qty": 10, "unit": "किलो", "amount": 400}
            ],
        },
    ).json()
    client.post(
        f"/bills/{bill['bill_id']}/approve",
        json={"lines": [{"line_no": 1, "item_id": "sugar", "qty": 10, "unit_cost": 40.0, "selling_price": 48.0}]},
    )

    # Upload sales photo with auto_extract=true
    res = client.post(
        "/ingest/photo",
        data={"shop_id": "shop-ocr", "kind": "sales", "auto_extract": "true"},
        files={"file": ("page.jpg", b"register-photo-bytes", "image/jpeg")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "processed"
    assert body["extraction"]["sales_recorded"] == 1

    summary = client.get("/shops/shop-ocr/daily-summary", params={"target_date": "2026-11-25"}).json()
    assert summary["sales"] == 48.0
    assert summary["cost_of_goods"] == 40.0
    assert summary["profit"] == 8.0
