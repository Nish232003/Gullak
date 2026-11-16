from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REF_DIR = Path(__file__).resolve().parents[3] / "data" / "reference"
SUPPLIER = {"dairy": "डेयरी", "bakery": "डेयरी", "vegetables": "सब्ज़ी मंडी", "pooja": "होलसेल"}
COVER = {"daily": 1, "alt": 3, "biweekly": 14}
FIXED = {"rent": 8000, "helper_salary": 7000}


def parse_tags(s: str) -> dict[str, float]:
    return {k: float(v) for k, v in (p.split(":") for p in s.split("|") if p)}


def load_reference(ref_dir: Path = REF_DIR) -> tuple[pd.DataFrame, pd.DataFrame]:
    items = pd.read_csv(ref_dir / "items.csv", keep_default_na=False)
    fest = pd.read_csv(ref_dir / "festivals.csv", parse_dates=["start", "end"])
    fest["start"], fest["end"] = fest["start"].dt.date, fest["end"].dt.date
    return items, fest


@dataclass
class ItemState:
    row: tuple
    tags: dict[str, float]
    cost: float
    price: float
    avg_cost: float = 0.0
    lots: deque = field(default_factory=deque)
    pending: tuple[date, float] | None = None
    daily_qty: float = 0.0

    @property
    def stock(self) -> float:
        return sum(q for q, _ in self.lots)


class Simulator:
    def __init__(self, start: date, days: int, seed: int = 42, ref_dir: Path = REF_DIR):
        self.start, self.days = start, days
        self.rng = np.random.default_rng(seed)
        items, self.fest = load_reference(ref_dir)
        self.items = {
            r.item_id: ItemState(r, parse_tags(r.festival_tags), float(r.cost), float(r.price), daily_qty=float(r.daily_qty))
            for r in items.itertuples(index=False)
        }
        self.out = {k: [] for k in ("sales", "purchases", "wastage", "expenses", "opening")}
        self.bill_seq = 0

    def day_factors(self, d: date) -> tuple[float, frozenset[str]]:
        cache = self.__dict__.setdefault("_days", {})
        if d not in cache:
            act = self.fest[(self.fest.start <= d) & (self.fest.end >= d)]
            f = 1.2 if d.weekday() == 6 else 1.1 if d.weekday() == 5 else 1.0
            f *= (1.12 if d.day <= 5 else 1.0) * float(np.prod(act.store_mult))
            cache[d] = (f, frozenset(act.tag))
        return cache[d]

    def expected(self, s: ItemState, d: date) -> float:
        f, tags = self.day_factors(d)
        for tag in tags:
            f *= s.tags.get(tag, 1.0)
        return s.daily_qty * f

    def next_bill(self, prefix: str, d: date) -> str:
        self.bill_seq += 1
        return f"{prefix}-{d:%Y%m%d}-{self.bill_seq:06d}"

    def receive(self, s: ItemState, qty: float, unit_cost: float, d: date) -> None:
        stock = s.stock
        s.avg_cost = (stock * s.avg_cost + qty * unit_cost) / (stock + qty)
        shelf = int(s.row.shelf_days)
        s.lots.append([qty, d + timedelta(days=shelf - 1) if shelf else None])

    def drift_prices(self, d: date) -> None:
        for s in self.items.values():
            base, r = float(s.row.cost), s.row
            if r.category == "vegetables" and d.weekday() == 0:
                s.cost = base * math.exp(0.7 * math.log(s.cost / base) + self.rng.normal(0, 0.08))
            elif r.category != "vegetables" and d.day == 1:
                s.cost *= math.exp(self.rng.normal(0.002, 0.01) if r.mrp else self.rng.normal(0.004, 0.02))
            else:
                continue
            if not r.mrp:
                target = s.cost * float(r.price) / base
                new = round(target) if target >= 20 else round(target * 2) / 2
                s.pending = (d + timedelta(days=int(self.rng.integers(0, 8))), float(new))
        for s in self.items.values():
            if s.pending and s.pending[0] <= d:
                s.price, s.pending = s.pending[1], None

    def restock(self, d: date, opening: bool = False) -> None:
        bills: dict[str, str] = {}
        for s in self.items.values():
            mode = s.row.restock
            due = opening or mode == "daily" or (mode == "alt" and (d - self.start).days % 2 == 0)
            due = due or (mode == "biweekly" and d.weekday() in (0, 3))
            if not due:
                continue
            cover = COVER[mode]
            need = sum(self.expected(s, d + timedelta(days=k)) for k in range(cover))
            if mode == "daily":
                need *= 1.1
            elif not opening and s.stock >= need * (1.0 if mode == "alt" else 5 / cover):
                continue
            packs = math.ceil(max(0.0, need - s.stock) / s.row.pack_qty)
            if packs == 0:
                continue
            qty = packs * float(s.row.pack_qty)
            unit_cost = round(s.cost, 2)
            self.receive(s, qty, unit_cost, d)
            if opening:
                self.out["opening"].append({"item_id": s.row.item_id, "qty": qty, "unit_cost": unit_cost})
                continue
            supplier = SUPPLIER.get(s.row.category, "होलसेल")
            bills.setdefault(supplier, self.next_bill("P", d))
            self.out["purchases"].append({
                "bill_id": bills[supplier], "date": d, "supplier": supplier, "item_id": s.row.item_id,
                "name_hi": s.row.name_hi, "pack_hi": s.row.pack_hi, "packs": packs,
                "pack_qty": s.row.pack_qty, "qty": qty, "unit_cost": unit_cost,
                "total_cost": round(qty * unit_cost, 2),
            })

    def take(self, s: ItemState, qty: float) -> None:
        while qty > 1e-9:
            lot = s.lots[0]
            used = min(lot[0], qty)
            lot[0] -= used
            qty -= used
            if lot[0] <= 1e-9:
                s.lots.popleft()

    def sell(self, d: date) -> None:
        states = list(self.items.values())
        exp = np.array([self.expected(s, d) for s in states])
        typ = np.array([float(s.row.typical_qty) for s in states])
        w = exp / typ
        p = w / w.sum()
        for _ in range(self.rng.poisson(w.sum() / 2.5)):
            k = 1 + self.rng.binomial(5, 0.3)
            picks = self.rng.choice(len(states), size=k, replace=False, p=p)
            mode = "UPI" if self.rng.random() < 0.5 else "cash"
            bill, line = None, 0
            for i in picks:
                s = states[i]
                if s.row.unit == "kg":
                    qty = typ[i] * self.rng.choice([0.5, 1, 1, 1, 2])
                    qty = max(0.25, round(qty * 4) / 4)
                else:
                    qty = float(max(1, self.rng.poisson(typ[i])))
                stock = s.stock
                if s.row.unit != "kg":
                    stock = math.floor(stock + 1e-9)
                qty = min(qty, stock)
                if qty <= 0:
                    continue
                bill = bill or self.next_bill("S", d)
                line += 1
                cogs = qty * s.avg_cost
                self.take(s, qty)
                self.out["sales"].append({
                    "bill_id": bill, "date": d, "line_no": line, "item_id": s.row.item_id,
                    "name_hi": s.row.name_hi, "qty": qty, "unit": s.row.unit, "unit_hi": s.row.unit_hi,
                    "unit_price": s.price, "amount": int(round(qty * s.price)), "payment_mode": mode,
                    "cogs": round(cogs, 4), "below_cost": s.price < s.avg_cost,
                })

    def expire(self, d: date) -> None:
        for s in self.items.values():
            gone = [lot for lot in s.lots if lot[1] is not None and lot[1] <= d]
            if not gone:
                continue
            qty = sum(lot[0] for lot in gone)
            s.lots = deque(lot for lot in s.lots if not (lot[1] is not None and lot[1] <= d))
            self.out["wastage"].append({
                "date": d, "item_id": s.row.item_id, "name_hi": s.row.name_hi, "qty": qty,
                "value_at_cost": round(qty * s.avg_cost, 4), "reason": "expired",
            })

    def spend(self, d: date) -> None:
        rows = []
        if d.day == 1:
            rows += [(k, v, "fixed") for k, v in FIXED.items()]
        if d.day == 10:
            rows.append(("electricity", self.electricity(d), "fixed"))
        rows.append(("tea", int(self.rng.integers(20, 41)), "variable"))
        if d.weekday() in (0, 3):
            rows.append(("transport", int(self.rng.integers(150, 301)), "variable"))
        if (d - self.start).days % 7 == 0:
            rows.append(("polythene", 100, "variable"))
        if self.rng.random() < 0.1:
            rows.append(("misc", int(self.rng.integers(50, 201)), "variable"))
        self.out["expenses"] += [{"date": d, "head": h, "amount": a, "kind": k} for h, a, k in rows]

    def electricity(self, d: date) -> int:
        key = (d.year, d.month)
        cache = self.__dict__.setdefault("_elec", {})
        if key not in cache:
            cache[key] = int(round(1200 + (400 if d.month in (4, 5, 6) else 0) + self.rng.normal(0, 100)))
        return cache[key]

    def run(self) -> dict[str, pd.DataFrame]:
        self.restock(self.start - timedelta(days=1), opening=True)
        for n in range(self.days):
            d = self.start + timedelta(days=n)
            self.drift_prices(d)
            self.restock(d)
            self.sell(d)
            self.expire(d)
            self.spend(d)
        months = sorted({(self.start + timedelta(days=n)).replace(day=1) for n in range(self.days)})
        fixed = pd.DataFrame([
            {"month": m, **FIXED, "electricity": self.electricity(m.replace(day=10))} for m in months
        ])
        frames = {k: pd.DataFrame(v) for k, v in self.out.items()}
        frames["monthly_fixed"] = fixed
        frames["daily_pnl_truth"] = daily_pnl(frames, self.start, self.days)
        return frames


def daily_pnl(f: dict[str, pd.DataFrame], start: date, days: int) -> pd.DataFrame:
    idx = pd.Index([start + timedelta(days=n) for n in range(days)], name="date")
    s = f["sales"]
    g = s.groupby("date")
    out = pd.DataFrame(index=idx)
    out["sales"] = g.amount.sum()
    out["upi_sales"] = s[s.payment_mode == "UPI"].groupby("date").amount.sum()
    out["cash_sales"] = s[s.payment_mode == "cash"].groupby("date").amount.sum()
    out["cogs"] = g.cogs.sum()
    out["bills"] = g.bill_id.nunique()
    out["lines"] = g.size()
    below = s[s.below_cost]
    out["below_cost_lines"] = below.groupby("date").size()
    out["wastage"] = f["wastage"].groupby("date").value_at_cost.sum() if len(f["wastage"]) else 0.0
    e = f["expenses"]
    out["variable_expenses"] = e[e.kind == "variable"].groupby("date").amount.sum()
    out = out.fillna(0)
    mf = f["monthly_fixed"].set_index("month")
    month_total = mf[list(FIXED) + ["electricity"]].sum(axis=1)
    out["fixed_expenses_alloc"] = [
        month_total[d.replace(day=1)] / pd.Period(d, "M").days_in_month for d in out.index
    ]
    out["gross_profit"] = out.sales - out.cogs
    out["net_profit"] = out.gross_profit - out.wastage - out.variable_expenses - out.fixed_expenses_alloc
    return out.round(2).reset_index()


def generate(start: date, days: int, seed: int, out_dir: Path) -> dict[str, pd.DataFrame]:
    frames = Simulator(start, days, seed).run()
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in frames.items():
        df.to_csv(out_dir / f"{name}.csv", index=False)
    return frames
