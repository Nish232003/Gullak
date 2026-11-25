from datetime import date

from gullak.advice import (
    AdviceConfig,
    AdviceType,
    ItemInventorySnapshot,
    evaluate_item_advice,
    generate_daily_advice,
)


def test_restock_triggered_below_trip_plus_buffer():
    snap = ItemInventorySnapshot(
        item_id="sugar",
        name_en="Sugar",
        name_hi="चीनी",
        stock=10.0,
        unit="kg",
        unit_hi="किलो",
        pack_qty=50.0,
        pack_hi="बोरी",
        cost=42.0,
        price=48.0,
        daily_demand=5.0,
        days_until_next_trip=1,  # target = 1 + 2 = 3 days -> 15 kg needed
    )
    advice = evaluate_item_advice(snap)
    assert len(advice) == 1
    assert advice[0].advice_type == AdviceType.RESTOCK
    assert advice[0].details["suggested_packs"] == 1


def test_dont_restock_overstock_30_days():
    snap = ItemInventorySnapshot(
        item_id="salt",
        name_en="Salt",
        name_hi="नमक",
        stock=100.0,
        cost=20.0,
        price=25.0,
        daily_demand=3.0,  # 33.3 days of cover
    )
    advice = evaluate_item_advice(snap)
    assert [a.advice_type for a in advice] == [AdviceType.DONT_RESTOCK_OVERSTOCK]


def test_dont_restock_no_sale_14_days_overrides_low_stock():
    snap = ItemInventorySnapshot(
        item_id="sabudana",
        name_en="Sabudana",
        name_hi="साबूदाना",
        stock=0.5,
        cost=70.0,
        price=82.0,
        daily_demand=1.0,
        days_since_last_sale=14,
    )
    advice = evaluate_item_advice(snap)
    assert [a.advice_type for a in advice] == [AdviceType.DONT_RESTOCK_NO_SALE]


def test_dont_restock_close_to_expiry():
    snap = ItemInventorySnapshot(
        item_id="milk",
        name_en="Milk",
        name_hi="दूध",
        stock=5.0,
        cost=27.0,
        price=29.0,
        daily_demand=10.0,
        days_to_expiry=2,
    )
    advice = evaluate_item_advice(snap, AdviceConfig(expiry_warning_days=5))
    assert [a.advice_type for a in advice] == [AdviceType.DONT_RESTOCK_EXPIRING]


def test_watch_margin_below_cost_and_thin_margin():
    below_cost = ItemInventorySnapshot(
        item_id="onion",
        name_en="Onion",
        name_hi="प्याज",
        stock=20.0,
        cost=35.0,
        price=30.0,
        daily_demand=5.0,
    )
    thin = ItemInventorySnapshot(
        item_id="sugar",
        name_en="Sugar",
        name_hi="चीनी",
        stock=20.0,
        cost=100.0,
        price=102.5,  # 2.5% margin <= 3%
        daily_demand=5.0,
    )
    summary = generate_daily_advice([below_cost, thin], as_of=date(2026, 11, 20))
    assert len(summary.watch_margin) == 2
    assert summary.watch_margin[0].reason == "below_cost"
    assert summary.watch_margin[1].reason == "thin_margin"


def test_advice_multilingual_output():
    snap = ItemInventorySnapshot(
        item_id="paracetamol",
        name_en="Paracetamol 500mg",
        name_hi="पैरासिटामोल",
        stock=2.0,
        unit="pc",
        unit_hi="पत्ता",
        pack_qty=10.0,
        pack_hi="बॉक्स",
        cost=20.0,
        price=25.0,
        daily_demand=3.0,
    )
    for lang in ("en", "hi", "mr", "gu", "ta", "te"):
        advice = evaluate_item_advice(snap, language=lang)
        assert len(advice) == 1
        assert advice[0].language == lang
        assert advice[0].message

