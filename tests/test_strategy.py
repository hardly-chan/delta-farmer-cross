"""Tests for current delta trade lifecycle and orchestration behavior."""

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

import pytest

from strategy.cycle import DeltaStrategy
from strategy.execution import fill_limit_order
from strategy.models import Order, OrderStatus, Position, Side, StrategyConfig, TradingClient
from strategy.trade import DeltaLeg, DeltaTrade, DeltaTradeSummary


async def _instant_sleep(_):
    """Replace asyncio.sleep with a no-op for fast tests."""


def make_order(id: str, symbol: str, side: Side, qty: Decimal) -> Order:
    return Order(
        id=id,
        symbol=symbol,
        side=side,
        size=qty,
        filled=qty,
        price=Decimal("50000"),
        status=OrderStatus.FILLED,
    )


def make_position(symbol: str, side: Side, size: str, entry: str = "50000") -> Position:
    return Position(
        id="p1", symbol=symbol, side=side, size=Decimal(size), entry_price=Decimal(entry)
    )


def make_cfg(**kw) -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "accounts": [{"name": "test", "privkey": "x" * 32}],
            "symbols": ["BTC"],
            "leverage": 10,
            "trade_size_usd": [100, 100],
            "trade_duration": [1, 1],
            "trade_cooldown": [1, 1],
            "trade_heartbeat": 1,
            "position_roi_limit": 0.8,
            "combined_roi_limit": 0.1,
            **kw,
        }
    )


class MockClient(TradingClient):
    exchange = "mock"

    def __init__(self, name: str, balance: float = 1000, side: Side = "bid", price: float = 50000):
        self._name = name
        self._balance = Decimal(str(balance))
        self._side: Side = side
        self._price = Decimal(str(price))
        self._positions: list[Position] | None = None
        self.calls: list[str] = []
        self._leverage: int | None = None
        self._min_trade_usd: Decimal = Decimal(10)

    @property
    def name(self) -> str:
        return self._name

    def _rec(self, name: str) -> None:
        self.calls.append(name)

    async def warmup(self):
        self._rec("warmup")

    async def registered(self):
        return True

    async def balance(self):
        self._rec("balance")
        return self._balance

    async def get_price(self, symbol: str):
        self._rec("get_price")
        return self._price

    async def get_bbo(self, symbol: str):
        return Decimal("49999"), Decimal("50001")

    async def get_lot_size(self, symbol: str):
        return Decimal("0.001")

    async def get_tick_size(self, symbol: str):
        return Decimal("1")

    async def get_min_trade_usd(self, symbol: str):
        return self._min_trade_usd

    async def get_leverage(self, symbol: str):
        self._rec("get_leverage")
        return self._leverage

    async def set_leverage(self, symbol: str, leverage: int):
        self._rec("set_leverage")
        self._leverage = leverage

    async def positions(self):
        self._rec("positions")
        if self._positions is not None:
            return self._positions
        return [make_position("BTC", self._side, "0.002")]

    async def close_position(self, position: Position):
        self._rec("close_position")
        return True

    async def market_order(self, symbol: str, side: Side, qty: Decimal, reduce_only=False):
        self._rec("market_order")
        return make_order("ord-m", symbol, side, qty)

    async def limit_order(
        self, symbol: str, side: Side, qty: Decimal, price: Decimal, reduce_only=False
    ):
        self._rec("limit_order")
        return make_order("ord-l", symbol, side, qty)

    async def cancel_order(self, order: Order):
        self._rec("cancel_order")
        return True

    async def get_order(self, order_id: str):
        self._rec("get_order")
        return None

    async def cancel_all_orders(self):
        self._rec("cancel_all_orders")
        return 0

    async def close_all_positions(self):
        self._rec("close_all_positions")
        return 1

    async def get_symbols(self):
        return ["BTC"]


def make_trade(
    symbol: str = "BTC",
    lead_client: MockClient | None = None,
    rest_clients: list[MockClient] | None = None,
) -> DeltaTrade:
    lead_client = lead_client or MockClient("lead")
    rest_clients = rest_clients or [MockClient("rest", side="ask")]
    lead = DeltaLeg(cast(TradingClient, lead_client), "bid", Decimal("100"), qty=Decimal("0.002"))
    rest = [
        DeltaLeg(cast(TradingClient, client), "ask", Decimal("100"), qty=Decimal("0.002"))
        for client in rest_clients
    ]
    return DeltaTrade(symbol=symbol, lead=lead, rest=rest)


@dataclass
class FakeTrade:
    symbol: str
    summary: DeltaTradeSummary
    calls: list[str]
    close_calls: list[bool]
    legs: list[DeltaLeg]

    async def load_qtys(self) -> None:
        self.calls.append(f"{self.symbol}:load_qtys")

    async def check_min_sizes(self) -> None:
        self.calls.append(f"{self.symbol}:check_min_sizes")

    async def check_leverage(self, leverage: int) -> None:
        self.calls.append(f"{self.symbol}:check_leverage:{leverage}")

    async def log_plan(self) -> None:
        self.calls.append(f"{self.symbol}:log_plan")

    async def open(self, cfg: StrategyConfig) -> None:
        self.calls.append(f"{self.symbol}:open")

    async def state(self, cfg: StrategyConfig) -> DeltaTradeSummary:
        self.calls.append(f"{self.symbol}:state")
        return self.summary

    async def close(self, cfg: StrategyConfig, use_limit=False) -> None:
        self.calls.append(f"{self.symbol}:close")
        self.close_calls.append(use_limit)


async def test_trade_check_leverage_sets_when_missing_or_wrong():
    a, b = MockClient("a"), MockClient("b")
    trade = make_trade(lead_client=a, rest_clients=[b])

    await trade.check_leverage(10)
    assert "set_leverage" in a.calls
    assert "set_leverage" in b.calls

    a.calls.clear()
    b.calls.clear()
    a._leverage = 10
    b._leverage = 10

    await trade.check_leverage(10)
    assert "set_leverage" not in a.calls
    assert "set_leverage" not in b.calls


async def test_trade_check_min_sizes_raises_for_failing_accounts():
    a, b = MockClient("a"), MockClient("b")
    trade = make_trade(lead_client=a, rest_clients=[b])
    a._min_trade_usd = Decimal(200)

    with pytest.raises(RuntimeError, match="a"):
        await trade.check_min_sizes()

    b._min_trade_usd = Decimal(200)
    with pytest.raises(RuntimeError) as exc:
        await trade.check_min_sizes()
    assert "a" in str(exc.value) and "b" in str(exc.value)


async def test_trade_open_market_mode():
    a, b = MockClient("a"), MockClient("b", side="ask")
    trade = make_trade(lead_client=a, rest_clients=[b])

    await trade.open(make_cfg(use_limit=False))
    assert a.calls.count("market_order") == 1
    assert b.calls.count("market_order") == 1
    assert "limit_order" not in a.calls


async def test_trade_open_limit_mode_fills(monkeypatch):
    a, b = MockClient("a"), MockClient("b", side="ask")
    trade = make_trade(lead_client=a, rest_clients=[b])
    filled = make_order("ord-l", "BTC", "bid", Decimal("0.002"))

    async def fake_limit(*args, **kwargs):
        return filled

    monkeypatch.setattr("strategy.trade._fill_limit_order", fake_limit)

    await trade.open(make_cfg(use_limit=True))
    assert "market_order" not in a.calls
    assert b.calls.count("market_order") == 1


async def test_trade_open_limit_mode_fails(monkeypatch):
    trade = make_trade()

    async def fake_limit_fail(*args, **kwargs):
        return None

    monkeypatch.setattr("strategy.trade._fill_limit_order", fake_limit_fail)

    with pytest.raises(RuntimeError, match="Limit order failed"):
        await trade.open(make_cfg(use_limit=True))


async def test_trade_close_use_limit_closes_single_symbol(monkeypatch):
    lead, rest = MockClient("lead"), MockClient("rest", side="ask")
    lead._positions = [make_position("ETH", "ask", "0.002"), make_position("BTC", "ask", "0.002")]
    rest._positions = [make_position("ETH", "bid", "0.002"), make_position("BTC", "bid", "0.002")]
    trade = make_trade(symbol="ETH", lead_client=lead, rest_clients=[rest])
    seen: list[tuple[str, str, bool]] = []

    async def fake_fill(client, symbol, side, qty, cfg, reduce_only=False):
        seen.append((client.name, symbol, reduce_only))
        return make_order("ord-l", symbol, side, qty)

    monkeypatch.setattr("strategy.trade._fill_limit_order", fake_fill)

    await trade.close(make_cfg(), use_limit=True)
    assert seen == [("lead", "ETH", True)]
    assert rest.calls.count("close_position") == 1


async def test_trade_state_missing_position_is_unhealthy():
    a, b = MockClient("a"), MockClient("b")
    trade = make_trade(lead_client=a, rest_clients=[b])
    a._positions = []
    b._positions = [make_position("BTC", "ask", "0.002")]

    summary = await trade.state(make_cfg())
    assert summary.healthy is False
    assert summary.open_leg_count == 1
    assert summary.close_reason == "missing positions (1/2)"


async def test_trade_state_detects_size_drift():
    a, b = MockClient("a"), MockClient("b")
    trade = make_trade(lead_client=a, rest_clients=[b])
    a._positions = [make_position("BTC", "bid", "0.001")]
    b._positions = [make_position("BTC", "ask", "0.002")]

    summary = await trade.state(make_cfg())
    assert summary.healthy is False
    assert summary.has_size_drift is True
    assert summary.close_reason == "position size drift"


async def test_trade_state_detects_roi_breach():
    a, b = MockClient("a", price=95000), MockClient("b", side="ask", price=5000)
    trade = make_trade(lead_client=a, rest_clients=[b])
    a._positions = [make_position("BTC", "bid", "0.002", "50000")]
    b._positions = [make_position("BTC", "ask", "0.002", "50000")]

    summary = await trade.state(make_cfg(position_roi_limit=0.8))
    assert summary.healthy is False
    assert summary.roi_breach is True
    assert summary.close_reason == "leg ROI hit 90.00%"


async def test_trade_state_aggregates_totals():
    a, b = MockClient("a", price=55000), MockClient("b", side="ask", price=45000)
    trade = make_trade(lead_client=a, rest_clients=[b])
    a._positions = [make_position("BTC", "bid", "0.002", "50000")]
    b._positions = [make_position("BTC", "ask", "0.002", "50000")]

    summary = await trade.state(make_cfg())
    assert summary.total_pnl == Decimal("20")
    assert summary.total_entry_cost == Decimal("200")
    assert summary.combined_roi == Decimal("0.1")
    assert summary.healthy is True


async def test_monitor_trades_stop_event_exits_early():
    stop = asyncio.Event()
    stop.set()
    strategy = DeltaStrategy(make_cfg(trade_duration=[5, 5]), [MockClient("a")], stop_event=stop)

    result = await strategy.monitor_trades([])
    assert result is False


async def test_monitor_trades_exits_on_unhealthy_summary(monkeypatch):
    strategy = DeltaStrategy(make_cfg(trade_duration=[5, 5]), [MockClient("a")])
    monkeypatch.setattr("strategy.cycle.asyncio.sleep", _instant_sleep)
    summary = DeltaTradeSummary(
        symbol="BTC",
        total_pnl=Decimal(0),
        total_entry_cost=Decimal(100),
        combined_roi=Decimal(0),
        max_abs_leg_roi=Decimal("0.9"),
        leg_count=2,
        open_leg_count=2,
        has_size_drift=False,
        roi_breach=True,
        healthy=False,
    )
    trade = FakeTrade("BTC", summary, [], [], [])

    result = await strategy.monitor_trades([trade])
    assert result is False


async def test_monitor_trades_exits_on_combined_roi(monkeypatch):
    strategy = DeltaStrategy(
        make_cfg(trade_duration=[5, 5], combined_roi_limit=0.1),
        [MockClient("a")],
    )
    monkeypatch.setattr("strategy.cycle.asyncio.sleep", _instant_sleep)
    summary = DeltaTradeSummary(
        symbol="BTC",
        total_pnl=Decimal("20"),
        total_entry_cost=Decimal("100"),
        combined_roi=Decimal("0.2"),
        max_abs_leg_roi=Decimal("0.2"),
        leg_count=2,
        open_leg_count=2,
        has_size_drift=False,
        roi_breach=False,
        healthy=True,
    )
    trade = FakeTrade("BTC", summary, [], [], [])

    result = await strategy.monitor_trades([trade])
    assert result is False


async def test_cycle_skips_when_no_valid_pair():
    a, b = MockClient("a", balance=0.01), MockClient("b", balance=1000)
    strategy = DeltaStrategy(make_cfg(), [a, b])
    strategy.initial_bal = Decimal("1000.01")

    await strategy.trade_cycle()
    assert "market_order" not in a.calls
    assert "market_order" not in b.calls


async def test_cycle_uses_trade_objects_and_limit_close(monkeypatch):
    accs = [MockClient("prime"), MockClient("acc2")]
    strategy = DeltaStrategy(make_cfg(use_limit=True), accs)
    strategy.initial_bal = Decimal("2000")
    calls: list[str] = []
    close_calls: list[bool] = []
    legs = [
        DeltaLeg(cast(TradingClient, accs[0]), "bid", Decimal("50")),
        DeltaLeg(cast(TradingClient, accs[1]), "ask", Decimal("50")),
    ]
    summary = DeltaTradeSummary(
        symbol="BTC",
        total_pnl=Decimal("1"),
        total_entry_cost=Decimal("100"),
        combined_roi=Decimal("0.01"),
        max_abs_leg_roi=Decimal("0.01"),
        leg_count=2,
        open_leg_count=2,
        has_size_drift=False,
        roi_breach=False,
        healthy=True,
    )
    trade = FakeTrade("BTC", summary, calls, close_calls, legs)

    async def fake_plan(*args, **kwargs):
        return [trade]

    async def fake_monitor(self, trades):
        calls.append("monitor")
        return True

    monkeypatch.setattr("strategy.cycle.plan_delta_trades", fake_plan)
    monkeypatch.setattr("strategy.cycle.random.sample", lambda seq, n: list(seq)[:n])
    monkeypatch.setattr(DeltaStrategy, "monitor_trades", fake_monitor)

    await strategy.trade_cycle()
    assert calls == [
        "BTC:load_qtys",
        "BTC:check_min_sizes",
        "BTC:check_leverage:10",
        "BTC:log_plan",
        "BTC:open",
        "monitor",
        "BTC:close",
    ]
    assert close_calls == [True]


async def test_cycle_aborts_before_open_when_trade_check_fails(monkeypatch):
    accs = [MockClient("prime"), MockClient("acc2")]
    strategy = DeltaStrategy(make_cfg(), accs)
    strategy.initial_bal = Decimal("2000")
    trade = FakeTrade(
        "BTC",
        DeltaTradeSummary(
            symbol="BTC",
            total_pnl=Decimal(0),
            total_entry_cost=Decimal(0),
            combined_roi=Decimal(0),
            max_abs_leg_roi=Decimal(0),
            leg_count=2,
            open_leg_count=2,
            has_size_drift=False,
            roi_breach=False,
            healthy=True,
        ),
        [],
        [],
        [
            DeltaLeg(cast(TradingClient, accs[0]), "bid", Decimal("50")),
            DeltaLeg(cast(TradingClient, accs[1]), "ask", Decimal("50")),
        ],
    )

    async def fake_plan(*args, **kwargs):
        return [trade]

    async def boom():
        raise RuntimeError("min size fail")

    trade.check_min_sizes = boom  # type: ignore[method-assign]
    monkeypatch.setattr("strategy.cycle.plan_delta_trades", fake_plan)
    monkeypatch.setattr("strategy.cycle.random.sample", lambda seq, n: list(seq)[:n])

    with pytest.raises(RuntimeError, match="min size fail"):
        await strategy.trade_cycle()
    assert "BTC:open" not in trade.calls


async def test_loop_closes_all_on_startup():
    a, b = MockClient("a"), MockClient("b")
    stop = asyncio.Event()
    strategy = DeltaStrategy(make_cfg(), [a, b], stop_event=stop)

    async def stop_after_cleanup():
        for _ in range(50):
            if "cancel_all_orders" in a.calls:
                stop.set()
                return
            await asyncio.sleep(0.05)

    task = asyncio.create_task(strategy.run())
    await stop_after_cleanup()
    try:
        await asyncio.wait_for(task, timeout=3)
    except (TimeoutError, asyncio.CancelledError):
        pass

    assert "cancel_all_orders" in a.calls


async def test_loop_closes_all_on_exception():
    a, b = MockClient("a"), MockClient("b")
    strategy = DeltaStrategy(make_cfg(max_failures=3), [a, b])
    strategy._wait = lambda _sec: asyncio.sleep(0)  # type: ignore[method-assign]

    async def boom():
        raise RuntimeError("exchange down")

    strategy.trade_cycle = boom  # type: ignore[method-assign]
    await strategy.run()
    assert "cancel_all_orders" in a.calls


async def test_limit_filled_immediately_no_polling():
    a = MockClient("a")
    result = await fill_limit_order(a, "BTC", "bid", Decimal("0.002"))
    assert result is not None
    assert result.status == OrderStatus.FILLED
    assert "get_order" not in a.calls


async def test_limit_open_get_order_none_raises_fatal(monkeypatch):
    from lib.errors import AppError

    a = MockClient("a")
    monkeypatch.setattr("strategy.execution.asyncio.sleep", _instant_sleep)

    async def open_limit(s, side, qty, price, reduce_only=False):
        return Order(
            id="ord-l",
            symbol=s,
            side=side,
            size=qty,
            filled=Decimal(0),
            price=price,
            status=OrderStatus.OPEN,
        )

    a.limit_order = open_limit  # type: ignore[method-assign]

    with pytest.raises(AppError, match="never appeared"):
        await fill_limit_order(a, "BTC", "bid", Decimal("0.002"), timeout=0)


async def test_limit_open_polls_until_filled(monkeypatch):
    a = MockClient("a")
    monkeypatch.setattr("strategy.execution.asyncio.sleep", _instant_sleep)
    call_count = 0

    async def open_limit(s, side, qty, price, reduce_only=False):
        return Order(
            id="ord-l",
            symbol=s,
            side=side,
            size=qty,
            filled=Decimal(0),
            price=price,
            status=OrderStatus.OPEN,
        )

    async def eventually_filled(oid):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return Order(
                id=oid,
                symbol="BTC",
                side="bid",
                size=Decimal("0.002"),
                filled=Decimal(0),
                price=Decimal("50000"),
                status=OrderStatus.OPEN,
            )
        return make_order(oid, "BTC", "bid", Decimal("0.002"))

    a.limit_order = open_limit  # type: ignore[method-assign]
    a.get_order = eventually_filled  # type: ignore[method-assign]

    result = await fill_limit_order(a, "BTC", "bid", Decimal("0.002"), timeout=60)
    assert result is not None
    assert result.status == OrderStatus.FILLED
    assert call_count >= 3


async def test_limit_canceled_by_exchange_raises(monkeypatch):
    a = MockClient("a")
    monkeypatch.setattr("strategy.execution.asyncio.sleep", _instant_sleep)
    qty = Decimal("0.002")

    async def open_limit(s, side, q, price, reduce_only=False):
        return Order(
            id="ord-l",
            symbol=s,
            side=side,
            size=q,
            filled=Decimal(0),
            price=price,
            status=OrderStatus.OPEN,
        )

    async def get_canceled(oid):
        return Order(
            id=oid,
            symbol="BTC",
            side="bid",
            size=qty,
            filled=Decimal(0),
            price=Decimal("50000"),
            status=OrderStatus.CANCELED,
        )

    a.limit_order = open_limit  # type: ignore[method-assign]
    a.get_order = get_canceled  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="canceled by exchange"):
        await fill_limit_order(a, "BTC", "bid", qty, use_market_fallback=True)
    assert "market_order" not in a.calls


async def test_limit_timeout_uses_market_fallback(monkeypatch):
    a = MockClient("a")
    monkeypatch.setattr("strategy.execution.asyncio.sleep", _instant_sleep)
    qty = Decimal("0.002")

    async def open_limit(s, side, q, price, reduce_only=False):
        return Order(
            id="ord-l",
            symbol=s,
            side=side,
            size=q,
            filled=Decimal(0),
            price=price,
            status=OrderStatus.OPEN,
        )

    async def still_open(oid):
        return Order(
            id=oid,
            symbol="BTC",
            side="bid",
            size=qty,
            filled=Decimal(0),
            price=Decimal("49999"),
            status=OrderStatus.OPEN,
        )

    bbo_calls = 0

    async def drifted_bbo(symbol):
        nonlocal bbo_calls
        bbo_calls += 1
        if bbo_calls == 1:
            return Decimal("49999"), Decimal("50001")
        return Decimal("49860"), Decimal("50140")

    a.limit_order = open_limit  # type: ignore[method-assign]
    a.get_order = still_open  # type: ignore[method-assign]
    a.get_bbo = drifted_bbo  # type: ignore[method-assign]

    result = await fill_limit_order(a, "BTC", "bid", qty, timeout=0, use_market_fallback=True)
    assert result is not None
    assert result.status == OrderStatus.FILLED
    assert "cancel_order" in a.calls
    assert "market_order" in a.calls


async def test_limit_timeout_no_fallback_raises(monkeypatch):
    a = MockClient("a")
    monkeypatch.setattr("strategy.execution.asyncio.sleep", _instant_sleep)
    qty = Decimal("0.002")

    async def open_limit(s, side, q, price, reduce_only=False):
        return Order(
            id="ord-l",
            symbol=s,
            side=side,
            size=q,
            filled=Decimal(0),
            price=price,
            status=OrderStatus.OPEN,
        )

    async def still_open(oid):
        return Order(
            id=oid,
            symbol="BTC",
            side="bid",
            size=qty,
            filled=Decimal(0),
            price=Decimal("49999"),
            status=OrderStatus.OPEN,
        )

    bbo_calls = 0

    async def drifted_bbo(symbol):
        nonlocal bbo_calls
        bbo_calls += 1
        if bbo_calls == 1:
            return Decimal("49999"), Decimal("50001")
        return Decimal("49860"), Decimal("50140")

    a.limit_order = open_limit  # type: ignore[method-assign]
    a.get_order = still_open  # type: ignore[method-assign]
    a.get_bbo = drifted_bbo  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="timed out"):
        await fill_limit_order(a, "BTC", "bid", qty, timeout=0, use_market_fallback=False)
    assert "cancel_order" in a.calls
    assert "market_order" not in a.calls


async def test_limit_stable_bbo_resets_timer(monkeypatch):
    a = MockClient("a")
    monkeypatch.setattr("strategy.execution.asyncio.sleep", _instant_sleep)
    qty = Decimal("0.002")
    get_order_calls = 0

    async def open_limit(s, side, q, price, reduce_only=False):
        return Order(
            id="ord-l",
            symbol=s,
            side=side,
            size=q,
            filled=Decimal(0),
            price=price,
            status=OrderStatus.OPEN,
        )

    async def fills_on_second(oid):
        nonlocal get_order_calls
        get_order_calls += 1
        if get_order_calls == 1:
            return Order(
                id=oid,
                symbol="BTC",
                side="bid",
                size=qty,
                filled=Decimal(0),
                price=Decimal("49999"),
                status=OrderStatus.OPEN,
            )
        return make_order(oid, "BTC", "bid", qty)

    a.limit_order = open_limit  # type: ignore[method-assign]
    a.get_order = fills_on_second  # type: ignore[method-assign]

    result = await fill_limit_order(a, "BTC", "bid", qty, timeout=0, use_market_fallback=True)
    assert result is not None
    assert result.status == OrderStatus.FILLED
    assert "cancel_order" not in a.calls
    assert "market_order" not in a.calls
