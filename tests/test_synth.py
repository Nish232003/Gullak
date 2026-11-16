from datetime import date

import pandas as pd
import pytest

from gullak.synth.generator import Simulator
from gullak.synth.kit import build_kit


@pytest.fixture(scope="module")
def frames():
    return Simulator(date(2026, 9, 1), 75, seed=1).run()


def test_deterministic(frames):
    again = Simulator(date(2026, 9, 1), 75, seed=1).run()
    pd.testing.assert_frame_equal(frames["sales"], again["sales"])


def test_amount_is_rounded_qty_times_price(frames):
    s = frames["sales"]
    assert ((s.qty * s.unit_price).round().astype(int) == s.amount).all()


def test_stock_never_negative(frames):
    opening = frames["opening"].groupby("item_id").qty.sum()
    bought = frames["purchases"].groupby("item_id").qty.sum()
    sold = frames["sales"].groupby("item_id").qty.sum()
    wasted = frames["wastage"].groupby("item_id").qty.sum()
    left = opening.add(bought, fill_value=0).sub(sold, fill_value=0).sub(wasted, fill_value=0)
    assert (left > -1e-6).all()


def test_pnl_truth_reconciles(frames):
    t = frames["daily_pnl_truth"].set_index("date")
    s = frames["sales"].groupby("date")
    assert t.sales.sum() == frames["sales"].amount.sum()
    assert abs(t.cogs.sum() - frames["sales"].cogs.sum()) < 1
    assert abs(t.wastage.sum() - frames["wastage"].value_at_cost.sum()) < 1
    expected = t.sales - t.cogs - t.wastage - t.variable_expenses - t.fixed_expenses_alloc
    assert (expected - t.net_profit).abs().max() < 0.05
    assert (s.amount.sum() - t.sales[s.amount.sum().index]).abs().max() == 0


def test_festival_lifts_sales(frames):
    t = frames["daily_pnl_truth"]
    t["date"] = pd.to_datetime(t.date)
    navratri = t[(t.date >= "2026-10-11") & (t.date <= "2026-10-20")].sales.mean()
    september = t[(t.date >= "2026-09-15") & (t.date <= "2026-09-30")].sales.mean()
    assert navratri > september


def test_kit_totals(frames, tmp_path):
    frames["sales"].to_csv(tmp_path / "sales.csv", index=False)
    kit = build_kit(tmp_path, tmp_path / "kit", pages=5)
    assert all(p["total"] == sum(l["amount"] for l in p["lines"]) for p in kit)
    assert (tmp_path / "kit" / "kit.html").exists()
