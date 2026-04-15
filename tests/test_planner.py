from decimal import Decimal
from typing import cast

import pytest

from strategy import Side, TradingClient
from strategy.cycle import SAFE_PCT, calc_total_from_pct
from strategy.trade import calc_symbol_sizes, plan_delta_trades


class DummyClient:
    def __init__(self, name: str):
        self._name = name
        self._balance = Decimal("1000")

    @property
    def name(self) -> str:
        return self._name

    async def balance(self) -> Decimal:
        return self._balance


def _sum_side(trade, side: Side) -> Decimal:
    return sum((leg.size_usd for leg in trade.legs if leg.side == side), Decimal(0))


def test_calc_symbol_sizes_contains_direction():
    sides: list[Side] = ["bid", "ask"]
    for side in sides:
        sizes = calc_symbol_sizes(Decimal("100"), ["BTC", "ETH", "SOL"], side)
        opposite = "ask" if side == "bid" else "bid"
        assert sizes == {
            "BTC": (Decimal("50.00"), side),
            "ETH": (Decimal("25.00"), opposite),
            "SOL": (Decimal("25.00"), opposite),
        }


def test_calc_symbol_sizes_supported_counts():
    expected = [
        {"BTC": ("100", "bid")},
        {"BTC": ("50", "bid"), "ETH": ("50", "ask")},
        {"BTC": ("50", "bid"), "ETH": ("25", "ask"), "SOL": ("25", "ask")},
        {"BTC": ("25", "bid"), "ETH": ("25", "bid"), "SOL": ("25", "ask"), "XRP": ("25", "ask")},
    ]

    for item in expected:
        symbols = list(item.keys())
        expect = {symbol: (Decimal(size), side) for symbol, (size, side) in item.items()}
        assert calc_symbol_sizes(Decimal("100"), symbols, "bid") == expect


def test_calc_symbol_sizes_rejects_more_than_four_symbols():
    symbols = ["BTC", "ETH", "SOL", "XRP", "DOGE"]

    with pytest.raises(ValueError, match="up to 4 symbols"):
        calc_symbol_sizes(Decimal("100"), symbols, "bid")


async def test_plan_delta_trades_keeps_symbol_and_account_delta_neutral(monkeypatch):
    accounts = [DummyClient("prime"), DummyClient("acc2"), DummyClient("acc3")]

    def fake_find_safe_pair(_balances, _size_usd, _leverage):
        return [("prime", Decimal("50")), ("acc2", Decimal("20")), ("acc3", Decimal("30"))]

    monkeypatch.setattr("strategy.trade.find_safe_pair", fake_find_safe_pair)
    monkeypatch.setattr("strategy.trade.random.choice", lambda _: "bid")

    trades = await plan_delta_trades(
        accounts=cast(list[TradingClient], accounts),
        symbols=["BTC", "ETH", "SOL"],
        total_size_usd=Decimal("100"),
        leverage=10,
        balances=[("prime", 1000.0), ("acc2", 1000.0), ("acc3", 1000.0)],
    )

    assert trades is not None
    assert [trade.symbol for trade in trades] == ["BTC", "ETH", "SOL"]
    assert all(trade.lead not in trade.rest for trade in trades)

    for trade in trades:
        assert _sum_side(trade, "bid") == _sum_side(trade, "ask")

    totals: dict[str, dict[str, Decimal]] = {
        "prime": {"bid": Decimal(0), "ask": Decimal(0)},
        "acc2": {"bid": Decimal(0), "ask": Decimal(0)},
        "acc3": {"bid": Decimal(0), "ask": Decimal(0)},
    }
    for trade in trades:
        for leg in trade.legs:
            totals[leg.client.name][leg.side] += leg.size_usd

    assert totals["prime"]["bid"] == totals["prime"]["ask"] == Decimal("25.00")
    assert totals["acc2"]["bid"] == totals["acc2"]["ask"] == Decimal("10.00")
    assert totals["acc3"]["bid"] == totals["acc3"]["ask"] == Decimal("15.00")


async def test_plan_delta_trades_returns_none_when_pair_not_found(monkeypatch):
    accounts = [DummyClient("a"), DummyClient("b")]
    monkeypatch.setattr("strategy.trade.find_safe_pair", lambda *_: None)

    trades = await plan_delta_trades(
        accounts=cast(list[TradingClient], accounts),
        symbols=["BTC", "ETH"],
        total_size_usd=Decimal("100"),
        leverage=10,
        balances=[("a", 1000.0), ("b", 1000.0)],
    )

    assert trades is None


def test_calc_total_from_pct_two_accounts():
    bals = [("prime", 1000.0), ("hedge", 500.0)]
    assert calc_total_from_pct(bals, leverage=10, pct=1.0) == 500 * 10 * SAFE_PCT / Decimal("0.5")


def test_calc_total_from_pct_min_on_prime():
    bals = [("prime", 100.0), ("hedge", 900.0)]
    assert calc_total_from_pct(bals, leverage=10, pct=1.0) == 100 * 10 * SAFE_PCT / Decimal("0.5")


def test_calc_total_from_pct_min_on_hedge():
    bals = [("prime", 900.0), ("h1", 100.0), ("h2", 900.0)]
    assert calc_total_from_pct(bals, leverage=10, pct=1.0) == 100 * 10 * SAFE_PCT / Decimal("0.25")


def test_calc_total_from_pct_main_binding_despite_larger_balance():
    bals = [("prime", 300.0), ("h1", 200.0), ("h2", 200.0)]
    result = calc_total_from_pct(bals, leverage=10, pct=1.0)
    assert result == 300 * 10 * SAFE_PCT / Decimal("0.5")
    assert result < 200 * 10 * SAFE_PCT / Decimal("0.25")


def test_calc_total_from_pct_pct_scales_linearly():
    bals = [("prime", 1000.0), ("hedge", 1000.0)]
    full = calc_total_from_pct(bals, leverage=10, pct=1.0)
    half = calc_total_from_pct(bals, leverage=10, pct=0.5)
    assert half == full / 2


async def test_plan_delta_trades_uses_actual_pair_total(monkeypatch):
    accounts = [DummyClient("prime"), DummyClient("acc2"), DummyClient("acc3")]

    def fake_find_safe_pair(_balances, _size_usd, _leverage):
        return [("prime", Decimal("40")), ("acc2", Decimal("10")), ("acc3", Decimal("30"))]

    monkeypatch.setattr("strategy.trade.find_safe_pair", fake_find_safe_pair)
    monkeypatch.setattr("strategy.trade.random.choice", lambda _: "bid")

    trades = await plan_delta_trades(
        accounts=cast(list[TradingClient], accounts),
        symbols=["BTC", "ETH"],
        total_size_usd=Decimal("100"),
        leverage=10,
        balances=[("prime", 1000.0), ("acc2", 1000.0), ("acc3", 1000.0)],
    )

    assert trades is not None
    assert sum(leg.size_usd for leg in trades[0].legs) == Decimal("40.00")
    assert sum(leg.size_usd for leg in trades[1].legs) == Decimal("40.00")
