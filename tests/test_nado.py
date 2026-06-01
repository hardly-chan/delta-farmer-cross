from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MethodType

import pytest

from clients.nado import MarketHours, NadoClient, SymbolInfo
from lib.http import ApiError


class FakeResponse:
    def __init__(self, data: dict, ok: bool = True):
        self._data = data
        self.ok = ok
        self.status_code = 200 if ok else 500
        self.text = ""

    def json(self) -> dict:
        return self._data


class FakeHttp:
    def __init__(self, data: dict, ok: bool = True):
        self.data = data
        self.ok = ok
        self.calls: list[tuple[str, str]] = []

    async def request(self, method: str, path: str, **_kwargs):
        self.calls.append((method, path))
        return FakeResponse(self.data, self.ok)


def make_nado_client(data: dict, ok: bool = True) -> NadoClient:
    client = object.__new__(NadoClient)
    client.name = "nado-test"
    client.http = FakeHttp(data, ok)
    return client


def make_symbol(
    *,
    trading_status: str = "live",
    market_hours: MarketHours | None = None,
) -> SymbolInfo:
    return SymbolInfo(
        product_id=1,
        symbol="TEST",
        size_increment=Decimal("0.1"),
        price_increment=Decimal("0.01"),
        min_size=Decimal("1"),
        trading_status=trading_status,
        market_hours=market_hours,
    )


def make_stub_client(symbol_info: SymbolInfo) -> NadoClient:
    client = object.__new__(NadoClient)
    client.name = "nado-test"

    async def _symbol_info(self, *, symbol: str | None = None, product_id: int | None = None):
        assert symbol == "TEST"
        assert product_id is None
        return symbol_info

    client.symbol_info = MethodType(_symbol_info, client)
    return client


async def test_symbols_parse_market_hours_and_defaults():
    client = make_nado_client(
        {
            "XAG-PERP": {
                "type": "perp",
                "product_id": 1,
                "size_increment": "100000000000000000",
                "price_increment_x18": "10000000000000000",
                "min_size": "1000000000000000000",
                "isolated_only": True,
                "trading_status": "soft_reduce_only",
                "market_hours": {
                    "is_open": True,
                    "next_close": "2026-05-29T20:00:00Z",
                    "next_open": None,
                },
            },
            "BTC-PERP": {
                "type": "perp",
                "product_id": 2,
                "size_increment": "1000000000000000",
                "price_increment_x18": "1000000000000000000",
                "min_size": "10000000000000000",
            },
            "wbNVDA": {
                "type": "perp",
                "product_id": 3,
                "size_increment": "100000000000000000",
                "price_increment_x18": "10000000000000000",
                "min_size": "1000000000000000000",
            },
            "USDC": {
                "type": "spot",
                "product_id": 4,
                "size_increment": "1000000000000000000",
                "price_increment_x18": "1000000000000000000",
                "min_size": "1000000000000000000",
            },
        }
    )

    symbols = await client.symbols()

    assert client.http.calls == [("GET", "https://archive.prod.nado.xyz/v2/symbols")]
    by_symbol = {sym.symbol: sym for sym in symbols}
    assert set(by_symbol) == {"XAG", "BTC", "wbNVDA", "USDC"}

    xag = by_symbol["XAG"]
    assert xag.symbol == "XAG"
    assert xag.isolated_only is True
    assert xag.trading_status == "soft_reduce_only"
    assert xag.market_hours == MarketHours(
        is_open=True,
        next_close=datetime(2026, 5, 29, 20, tzinfo=UTC),
        next_open=None,
    )

    btc = by_symbol["BTC"]
    assert btc.symbol == "BTC"
    assert btc.trading_status == "live"
    assert btc.market_hours is None

    assert by_symbol["wbNVDA"].symbol == "wbNVDA"


async def test_symbols_error_response_raises_api_error():
    client = make_nado_client({}, ok=False)

    with pytest.raises(ApiError, match="Symbols error"):
        await client.symbols()


async def test_is_symbol_tradeable_not_tradable_returns_false():
    client = make_stub_client(make_symbol(trading_status="not_tradable"))

    assert await client.is_symbol_tradeable("TEST", datetime(2026, 5, 29, tzinfo=UTC)) is False


async def test_is_symbol_tradeable_not_tradable_reduce_only_returns_false():
    client = make_stub_client(make_symbol(trading_status="not_tradable"))

    assert (
        await client.is_symbol_tradeable(
            "TEST", datetime(2026, 5, 29, tzinfo=UTC), reduce_only=True
        )
        is False
    )


async def test_is_symbol_tradeable_reduce_only_status_blocks_open():
    client = make_stub_client(make_symbol(trading_status="reduce_only"))

    assert await client.is_symbol_tradeable("TEST", datetime(2026, 5, 29, tzinfo=UTC)) is False


async def test_is_symbol_tradeable_reduce_only_status_allows_reduce_only():
    client = make_stub_client(make_symbol(trading_status="reduce_only"))

    assert (
        await client.is_symbol_tradeable(
            "TEST", datetime(2026, 5, 29, tzinfo=UTC), reduce_only=True
        )
        is True
    )


async def test_is_symbol_tradeable_soft_reduce_only_status_blocks_open():
    client = make_stub_client(make_symbol(trading_status="soft_reduce_only"))

    assert await client.is_symbol_tradeable("TEST", datetime(2026, 5, 29, tzinfo=UTC)) is False


async def test_is_symbol_tradeable_soft_reduce_only_status_allows_reduce_only():
    client = make_stub_client(make_symbol(trading_status="soft_reduce_only"))

    assert (
        await client.is_symbol_tradeable(
            "TEST", datetime(2026, 5, 29, tzinfo=UTC), reduce_only=True
        )
        is True
    )


async def test_is_symbol_tradeable_post_only_status_without_hours_is_true():
    client = make_stub_client(make_symbol(trading_status="post_only"))

    assert await client.is_symbol_tradeable("TEST", datetime(2026, 5, 29, tzinfo=UTC)) is True


async def test_is_symbol_tradeable_post_only_status_blocks_reduce_only():
    client = make_stub_client(make_symbol(trading_status="post_only"))

    assert (
        await client.is_symbol_tradeable(
            "TEST", datetime(2026, 5, 29, tzinfo=UTC), reduce_only=True
        )
        is False
    )


async def test_is_symbol_tradeable_reduce_only_ignores_closed_market_hours():
    at = datetime(2026, 5, 29, 12, tzinfo=UTC)
    client = make_stub_client(
        make_symbol(
            market_hours=MarketHours(
                is_open=False,
                next_open=at + timedelta(hours=1),
            )
        )
    )

    assert await client.is_symbol_tradeable("TEST", at, reduce_only=True) is True


async def test_is_symbol_tradeable_crypto_without_market_hours_is_true():
    client = make_stub_client(make_symbol(market_hours=None))

    assert await client.is_symbol_tradeable("TEST", datetime(2026, 5, 29, tzinfo=UTC)) is True


async def test_is_symbol_tradeable_open_market_with_future_close_is_true():
    at = datetime(2026, 5, 29, 12, tzinfo=UTC)
    client = make_stub_client(
        make_symbol(
            market_hours=MarketHours(
                is_open=True,
                next_close=at + timedelta(hours=1),
            )
        )
    )

    assert await client.is_symbol_tradeable("TEST", at) is True


async def test_is_symbol_tradeable_open_market_ignores_future_next_open():
    at = datetime(2026, 6, 2, 5, tzinfo=UTC)
    client = make_stub_client(
        make_symbol(
            market_hours=MarketHours(
                is_open=True,
                next_close=datetime(2026, 6, 6, tzinfo=UTC),
                next_open=datetime(2026, 6, 8, 8, tzinfo=UTC),
            )
        )
    )

    assert await client.is_symbol_tradeable("TEST", at) is True


async def test_is_symbol_tradeable_open_market_at_exact_close_is_false():
    at = datetime(2026, 5, 29, 12, tzinfo=UTC)
    client = make_stub_client(
        make_symbol(
            market_hours=MarketHours(
                is_open=True,
                next_close=at,
            )
        )
    )

    assert await client.is_symbol_tradeable("TEST", at) is False


async def test_is_symbol_tradeable_reduce_only_open_market_at_exact_close_is_true():
    at = datetime(2026, 5, 29, 12, tzinfo=UTC)
    client = make_stub_client(
        make_symbol(
            market_hours=MarketHours(
                is_open=True,
                next_close=at,
            )
        )
    )

    assert await client.is_symbol_tradeable("TEST", at, reduce_only=True) is True


async def test_is_symbol_tradeable_future_closed_window_blocks_open_but_allows_reduce_only():
    at = datetime(2026, 6, 6, 12, tzinfo=UTC)
    client = make_stub_client(
        make_symbol(
            market_hours=MarketHours(
                is_open=True,
                next_close=datetime(2026, 6, 6, tzinfo=UTC),
                next_open=datetime(2026, 6, 8, 8, tzinfo=UTC),
            )
        )
    )

    assert await client.is_symbol_tradeable("TEST", at) is False
    assert await client.is_symbol_tradeable("TEST", at, reduce_only=True) is True


async def test_is_symbol_tradeable_open_market_with_past_close_is_false():
    at = datetime(2026, 5, 29, 12, tzinfo=UTC)
    client = make_stub_client(
        make_symbol(
            market_hours=MarketHours(
                is_open=True,
                next_close=at - timedelta(seconds=1),
            )
        )
    )

    assert await client.is_symbol_tradeable("TEST", at) is False


async def test_is_symbol_tradeable_closed_market_with_next_open_before_at_is_true():
    at = datetime(2026, 5, 29, 12, tzinfo=UTC)
    client = make_stub_client(
        make_symbol(
            market_hours=MarketHours(
                is_open=False,
                next_open=at - timedelta(seconds=1),
                next_close=at + timedelta(hours=1),
            )
        )
    )

    assert await client.is_symbol_tradeable("TEST", at) is True


async def test_is_symbol_tradeable_closed_market_with_next_open_before_at_but_past_close_is_false():
    at = datetime(2026, 5, 29, 12, tzinfo=UTC)
    client = make_stub_client(
        make_symbol(
            market_hours=MarketHours(
                is_open=False,
                next_open=at - timedelta(hours=2),
                next_close=at,
            )
        )
    )

    assert await client.is_symbol_tradeable("TEST", at) is False


async def test_is_symbol_tradeable_closed_market_with_next_open_before_at_without_close_is_true():
    at = datetime(2026, 5, 29, 12, tzinfo=UTC)
    client = make_stub_client(
        make_symbol(
            market_hours=MarketHours(
                is_open=False,
                next_open=at - timedelta(seconds=1),
            )
        )
    )

    assert await client.is_symbol_tradeable("TEST", at) is True


async def test_is_symbol_tradeable_closed_market_with_next_open_after_at_is_false():
    at = datetime(2026, 5, 29, 12, tzinfo=UTC)
    client = make_stub_client(
        make_symbol(
            market_hours=MarketHours(
                is_open=False,
                next_open=at + timedelta(seconds=1),
            )
        )
    )

    assert await client.is_symbol_tradeable("TEST", at) is False
