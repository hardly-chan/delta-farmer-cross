from decimal import Decimal

import pytest

from strategy.spread_models import SpreadConfig
from strategy.spread_trade import (
    SpreadPlan,
    calc_cross_spread_pct,
    detect_spread_direction,
    open_spread,
)


class MockClient:
    def __init__(self, name: str, fail_open: bool = False):
        self.name = name
        self.fail_open = fail_open
        self.calls: list[tuple[str, str, Decimal, bool]] = []

    async def market_order(self, symbol: str, side: str, qty: Decimal, reduce_only=False):
        self.calls.append((symbol, side, qty, reduce_only))
        if self.fail_open and not reduce_only:
            raise RuntimeError(f"{self.name} open failure")
        return {"ok": True}


def test_calc_cross_spread_pct():
    spread = calc_cross_spread_pct(Decimal("100"), Decimal("100.1"))
    assert spread == Decimal("0.1")


def test_detect_spread_direction_omni_long():
    result = detect_spread_direction(
        omni_bid=Decimal("100"),
        omni_ask=Decimal("100.00"),
        nado_bid=Decimal("100.20"),
        nado_ask=Decimal("100.30"),
        min_open_spread_pct=Decimal("0.1"),
    )

    assert result == ("omni_long", Decimal("0.2"))


def test_detect_spread_direction_nado_long():
    result = detect_spread_direction(
        omni_bid=Decimal("100.20"),
        omni_ask=Decimal("100.30"),
        nado_bid=Decimal("100"),
        nado_ask=Decimal("100.00"),
        min_open_spread_pct=Decimal("0.1"),
    )

    assert result == ("nado_long", Decimal("0.2"))


def test_detect_spread_direction_none_when_below_threshold():
    result = detect_spread_direction(
        omni_bid=Decimal("100"),
        omni_ask=Decimal("100.05"),
        nado_bid=Decimal("100.09"),
        nado_ask=Decimal("100.12"),
        min_open_spread_pct=Decimal("0.1"),
    )

    assert result is None


async def test_open_spread_rolls_back_when_one_leg_fails():
    long_client = MockClient("omni")
    short_client = MockClient("nado", fail_open=True)
    plan = SpreadPlan(
        symbol="BTC",
        direction="omni_long",
        long_client=long_client,
        short_client=short_client,
        long_entry_price=Decimal("100"),
        short_entry_price=Decimal("100.2"),
        spread_pct=Decimal("0.2"),
        qty=Decimal("1"),
    )

    with pytest.raises(RuntimeError, match="short leg failed"):
        await open_spread(plan)

    assert long_client.calls == [
        ("BTC", "bid", Decimal("1"), False),
        ("BTC", "ask", Decimal("1"), True),
    ]
    assert short_client.calls == [("BTC", "ask", Decimal("1"), False)]


def test_open_and_close_spread_use_opposite_book_sides():
    # Entry (omni_long): buy at omni_ask, sell at nado_bid → entry spread uses ask/bid.
    # Exit (omni_long): sell at omni_bid, buy back at nado_ask → exit spread uses bid/ask.
    # When prices fully converge (same mid), the exit spread must drop to ~0, not stay
    # stuck at the bid-ask gap.
    omni_bid, omni_ask = Decimal("99.99"), Decimal("100.01")
    nado_bid, nado_ask = Decimal("99.99"), Decimal("100.01")

    entry_spread = calc_cross_spread_pct(omni_ask, nado_bid)
    exit_spread = calc_cross_spread_pct(omni_bid, nado_ask)

    assert entry_spread == exit_spread
    assert abs(exit_spread) < Decimal("0.05")  # converges to ~0 at price convergence


def test_spread_config_uses_min_open_time():
    cfg = SpreadConfig.model_validate(
        {
            "symbol": "BTC",
            "leverage": 10,
            "trade_size_usd": [100, 120],
            "min_open_spread_pct": "0.1",
            "min_close_spread_pct": "0.02",
            "min_open_time": "7m",
            "omni": {"name": "o", "privkey": "x" * 32},
            "nado": {"name": "n", "privkey": "x" * 32},
        }
    )

    assert int(cfg.min_open_time) == 7 * 60


def test_spread_config_accepts_safety_limits():
    cfg = SpreadConfig.model_validate(
        {
            "symbol": "BTC",
            "leverage": 10,
            "trade_size_usd": [100, 120],
            "min_open_spread_pct": "0.1",
            "min_close_spread_pct": "0.02",
            "max_abs_pnl_usd": "30",
            "max_abs_roi": "0.05",
            "omni": {"name": "o", "privkey": "x" * 32},
            "nado": {"name": "n", "privkey": "x" * 32},
        }
    )

    assert cfg.max_abs_pnl_usd == Decimal("30")
    assert cfg.max_abs_roi == Decimal("0.05")
