from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from gullak.api.app import create_app
from gullak.pipeline.contracts import ExtractedLine, ExtractedPage, ItemPriceRecord, PhotoKind, StockLot
from gullak.pipeline.costing import allocate_fifo, update_price_scd2
from gullak.pipeline.matching import ItemMatcher
from gullak.pipeline.runner import benchmark_handwriting_kit, run_synthetic_pipeline
from gullak.pipeline.validate import normalize_digits, validate_page


def test_devanagari_digit_normalization_and_validation():
    page = ExtractedPage(
        image_hash="abc123",
        shop_id="s1",
        kind=PhotoKind.SALES,
        page_date=date(2026, 11, 20),
        written_total=76.0,
        lines=[
            ExtractedLine(line_no=1, text="आलू  १ किलो  २७"),
            ExtractedLine(line_no=2, text="चीनी  १ किलो  ४९"),
        ],
    )
    res = validate_page(page)
    assert res.passed
    assert res.normalized_page.lines[0].qty == 1.0
    assert res.normalized_page.lines[0].amount == 27.0
    assert res.normalized_page.lines[1].amount == 49.0


def test_validation_catches_total_mismatch_and_price_outlier():
    page = ExtractedPage(
        image_hash="bad1",
        shop_id="s1",
        kind=PhotoKind.SALES,
        page_date=date(2026, 11, 20),
        written_total=100.0,
        lines=[
            ExtractedLine(line_no=1, text="आलू 1 किलो 90", item_name="आलू", qty=1.0, unit="किलो", amount=90.0),
        ],
    )
    res = validate_page(page, recent_avg_prices={"आलू": 30.0})
    assert not res.passed
    codes = {i.code for i in res.issues}
    assert "total_mismatch" in codes
    assert "price_outlier" in codes
    assert 1 in res.doubtful_lines


def test_item_matching_hindi_alias_fuzzy_and_grams_conversion():
    matcher = ItemMatcher.from_reference_csv(aliases={"चीनी दाना": "sugar"})
    lines = [
        ExtractedLine(line_no=1, text="आलू 500 ग्राम 15", item_name="आलू", qty=500, unit="ग्राम", amount=15),
        ExtractedLine(line_no=2, text="चीनी दाना 2 किलो 96", item_name="चीनी दाना", qty=2, unit="किलो", amount=96),
        ExtractedLine(line_no=3, text="Basmati rce 1 kg 100", item_name="Basmati rce", qty=1, unit="kg", amount=100),
    ]
    matched = matcher.match_page(lines)
    assert matched[0].item_id == "potato"
    assert matched[0].qty == 0.5
    assert matched[0].base_unit == "kg"
    assert matched[1].item_id == "sugar"
    assert matched[1].match_method == "alias"
    assert matched[2].item_id == "rice_basmati"
    assert matched[2].match_method == "fuzzy"


def test_fifo_costing_pdf_sugar_example_and_cost_missing():
    # PDF Page 6-7 example: 20 kg @ ₹40 + 50 kg @ ₹44 -> sell 25 kg @ ₹48 (₹1,200) -> COGS ₹1,020
    lots = [
        StockLot(
            lot_id="L1", shop_id="s1", item_id="sugar", bill_id="B1",
            received_on=date(2026, 11, 1), qty_received=20, qty_left=20, unit_cost=40.0,
        ),
        StockLot(
            lot_id="L2", shop_id="s1", item_id="sugar", bill_id="B2",
            received_on=date(2026, 11, 10), qty_received=50, qty_left=50, unit_cost=44.0,
        ),
    ]
    allocs, cogs, status = allocate_fifo(lots, sale_line_id="SL-1", qty_needed=25.0)
    assert status == "ok"
    assert cogs == 1020.0
    assert len(allocs) == 2
    assert lots[0].qty_left == 0.0
    assert lots[1].qty_left == 45.0

    # Selling more than available returns cost_missing
    _, missing_cogs, missing_status = allocate_fifo(lots, sale_line_id="SL-2", qty_needed=100.0)
    assert missing_status == "cost_missing"
    assert missing_cogs is None


def test_scd2_price_history():
    history: list[ItemPriceRecord] = []
    update_price_scd2(history, "s1", "sugar", 46.0, date(2026, 11, 1))
    update_price_scd2(history, "s1", "sugar", 48.0, date(2026, 11, 10))
    assert len(history) == 2
    assert history[0].valid_to == date(2026, 11, 10)
    assert history[1].valid_to is None
    assert history[1].selling_price == 48.0


def test_api_end_to_end_multi_tenant_and_lineage(tmp_path):
    app = create_app(raw_dir=tmp_path / "raw")
    client = TestClient(app)

    assert client.post("/shops", json={"shop_id": "shop-a", "name": "A Store"}).status_code == 200
    assert client.post("/shops", json={"shop_id": "shop-b", "name": "B Store"}).status_code == 200

    # 1. Photo upload + SHA-256 content-hash deduplication
    r1 = client.post(
        "/ingest/photo",
        data={"shop_id": "shop-a", "kind": "bill"},
        files={"file": ("bill.jpg", b"fake-bill-bytes", "image/jpeg")},
    )
    assert r1.status_code == 200
    assert r1.json()["duplicate"] is False
    bill_hash = r1.json()["image_hash"]

    r2 = client.post(
        "/ingest/photo",
        data={"shop_id": "shop-a", "kind": "bill"},
        files={"file": ("bill.jpg", b"fake-bill-bytes", "image/jpeg")},
    )
    assert r2.json()["duplicate"] is True

    # 2. Ingest wholesale bill -> approve -> creates FIFO stock lot & SCD2 price
    bill_res = client.post("/ingest/extracted", json={
        "image_hash": bill_hash,
        "shop_id": "shop-a",
        "kind": "bill",
        "page_date": "2026-11-18",
        "written_total": 800.0,
        "lines": [
            {"line_no": 1, "text": "चीनी 20 किलो 800", "item_name": "चीनी", "qty": 20, "unit": "किलो", "amount": 800}
        ],
    }).json()
    assert bill_res["status"] == "draft_bill"
    bill_id = bill_res["bill_id"]

    approve_res = client.post(f"/bills/{bill_id}/approve", json={
        "lines": [{"line_no": 1, "item_id": "sugar", "qty": 20, "unit_cost": 40.0, "selling_price": 48.0}]
    })
    assert approve_res.status_code == 200
    assert approve_res.json()["lots_created"] == 1

    # 3. Ingest sales register with total mismatch -> goes to review queue -> resolve
    bad_sales = client.post("/ingest/extracted", json={
        "image_hash": "sales-hash-1",
        "shop_id": "shop-a",
        "kind": "sales",
        "page_date": "2026-11-19",
        "written_total": 500.0,
        "lines": [
            {"line_no": 1, "text": "चीनी 5 किलो 240", "item_name": "चीनी", "qty": 5, "unit": "किलो", "amount": 240}
        ],
    }).json()
    assert bad_sales["status"] == "needs_review"
    review_id = bad_sales["review_id"]

    resolved = client.post(f"/shops/shop-a/review-queue/{review_id}/resolve", json={
        "page_date": "2026-11-19",
        "lines": [
            {"line_no": 1, "text": "चीनी 5 किलो 240", "item_name": "चीनी", "qty": 5, "unit": "किलो", "amount": 240}
        ],
    }).json()
    assert resolved["status"] == "processed"

    # 4. Daily summary & multi-tenancy check
    summary_a = client.get("/shops/shop-a/daily-summary", params={"target_date": "2026-11-19"}).json()
    assert summary_a["sales"] == 240.0
    assert summary_a["cost_of_goods"] == 200.0
    assert summary_a["profit"] == 40.0

    summary_b = client.get("/shops/shop-b/daily-summary", params={"target_date": "2026-11-19"}).json()
    assert summary_b["sales"] == 0.0

    # 5. Lineage check
    sale_line_id = app.state.store.sales["shop-a"][0].sale_line_id
    lineage = client.get(f"/shops/shop-a/lineage/{sale_line_id}").json()
    assert lineage["source_image_hash"] == "sales-hash-1"
    assert lineage["source_line_no"] == 1
    assert lineage["profit"] == 40.0


def test_synthetic_pipeline_and_kit_benchmark():
    res = run_synthetic_pipeline(days=15, seed=1)
    assert res["days_processed"] == 15
    assert abs(res["total_sales"] - res["truth_sales"]) < 1e-2

    kit_truth = Path("data/handwriting_kit/truth.json")
    if kit_truth.exists():
        bench = benchmark_handwriting_kit(kit_truth)
        assert bench["auto_matched_pct"] >= 90.0
        assert bench["exact_item_accuracy_pct"] >= 90.0
