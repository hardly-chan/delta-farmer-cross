"""Tests for HyperLiquidClient position filtering by DEX scope."""

from decimal import Decimal

import pytest

from clients.hyena import HyenaClient
from clients.hyperliquid import HyperLiquidClient
from clients.onyx import OnyxClient
from strategy.models import Position, Side

_FAKE_KEY = "a" * 64


class FakeHyperLiquidClient(HyperLiquidClient):
    def __init__(self, responses: dict[str, object]):
        super().__init__(name="test", privkey=_FAKE_KEY)
        self.responses = responses
        self.calls: list[str] = []

    async def _info(self, **kwargs):
        request_type = kwargs["type"]
        self.calls.append(request_type)
        return self.responses[request_type]


def _pos(symbol: str, side: Side = "bid") -> Position:
    return Position(
        id=symbol,
        symbol=symbol,
        side=side,
        size=Decimal("0.01"),
        entry_price=Decimal("80000"),
        unrealized_pnl=Decimal("0"),
    )


# MARK: Balance


@pytest.mark.asyncio
async def test_hyperliquid_balance_uses_clearinghouse_state_for_standard_accounts():
    c = FakeHyperLiquidClient(
        {
            "userAbstraction": "disabled",
            "clearinghouseState": {"marginSummary": {"accountValue": "123.45"}},
        }
    )

    assert await c.balance() == Decimal("123.45")
    assert c.calls == ["userAbstraction", "clearinghouseState"]


@pytest.mark.asyncio
async def test_hyperliquid_balance_uses_spot_state_for_unified_accounts():
    c = FakeHyperLiquidClient(
        {
            "userAbstraction": "unifiedAccount",
            "spotClearinghouseState": {
                "balances": [
                    {"coin": "HYPE", "total": "2"},
                    {"coin": "USDC", "total": "456.78", "hold": "12.34"},
                ]
            },
        }
    )

    assert await c.balance() == Decimal("456.78")
    assert c.calls == ["userAbstraction", "spotClearinghouseState"]


# MARK: HyenaClient


def test_hyena_keeps_only_hyna_positions():
    """Hyena must only manage hyna: positions, ignoring native HL and other DEX positions."""
    c = HyenaClient(name="test", privkey=_FAKE_KEY)
    result = c._filter_positions([_pos("hyna:BTC"), _pos("BTC", "ask"), _pos("xyz:TSLA")])
    assert [p.symbol for p in result] == ["hyna:BTC"]


def test_hyena_empty_when_no_hyna_positions():
    """Hyena returns empty list when no hyna: positions are open."""
    c = HyenaClient(name="test", privkey=_FAKE_KEY)
    assert c._filter_positions([_pos("BTC"), _pos("xyz:TSLA")]) == []


# MARK: OnyxClient


def test_onyx_ignores_hyna_positions_by_default():
    """Onyx configured for native BTC must not see hyna:BTC, but still sees xyz: positions."""
    c = OnyxClient(name="test", privkey=_FAKE_KEY)
    c._symbols = ["BTC"]
    result = c._filter_positions([_pos("hyna:BTC"), _pos("BTC", "ask"), _pos("xyz:TSLA")])
    assert [p.symbol for p in result] == ["BTC", "xyz:TSLA"]


def test_onyx_includes_explicit_hyna_symbol():
    """Onyx configured for hyna:BTC explicitly must include that position."""
    c = OnyxClient(name="test", privkey=_FAKE_KEY)
    c._symbols = ["hyna:BTC"]
    result = c._filter_positions([_pos("hyna:BTC"), _pos("BTC", "ask"), _pos("xyz:TSLA")])
    assert [p.symbol for p in result] == ["hyna:BTC", "BTC", "xyz:TSLA"]


def test_onyx_explicit_hyna_does_not_unlock_other_hyna_coins():
    """Onyx with hyna:ETH in config must still block hyna:BTC."""
    c = OnyxClient(name="test", privkey=_FAKE_KEY)
    c._symbols = ["BTC", "xyz:TSLA", "hyna:ETH"]
    result = c._filter_positions(
        [_pos("hyna:BTC"), _pos("hyna:ETH"), _pos("BTC"), _pos("xyz:TSLA")]
    )
    assert [p.symbol for p in result] == ["hyna:ETH", "BTC", "xyz:TSLA"]


def test_onyx_no_symbols_blocks_all_hyna():
    """Onyx with empty _symbols still blocks all hyna: positions."""
    c = OnyxClient(name="test", privkey=_FAKE_KEY)
    c._symbols = []
    result = c._filter_positions([_pos("hyna:BTC"), _pos("BTC"), _pos("xyz:TSLA")])
    assert [p.symbol for p in result] == ["BTC", "xyz:TSLA"]
