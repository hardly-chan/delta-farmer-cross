# delta-farmer | https://github.com/vladkens/delta-farmer
# Copyright (c) vladkens | MIT License | Code so clean it squeaks
import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from lib.errors import AppError
from lib.logger import logger
from lib.utils import format_duration, round_to_tick_size

from .models import (
    Order,
    OrderBook,
    OrderBookLevel,
    OrderStatus,
    Side,
    StrategyConfig,
    TradingClient,
)


@dataclass(frozen=True)
class EntryQuality:
    avg_bid_price: Decimal | None
    avg_ask_price: Decimal | None
    entry_spread_pct: Decimal | None
    bid_qty: Decimal = Decimal(0)
    ask_qty: Decimal = Decimal(0)
    bid_depth: Decimal = Decimal(0)
    ask_depth: Decimal = Decimal(0)

    @property
    def unavailable_reason(self) -> str | None:
        missing = []
        if self.bid_qty > 0 and self.avg_bid_price is None:
            missing.append(f"asks depth {self.ask_depth}/{self.bid_qty}")
        if self.ask_qty > 0 and self.avg_ask_price is None:
            missing.append(f"bids depth {self.bid_depth}/{self.ask_qty}")
        if missing:
            return "insufficient " + ", ".join(missing)
        if self.entry_spread_pct is None:
            return "spread unavailable"
        return None


@runtime_checkable
class EntryQualityEstimator(Protocol):
    async def estimate_entry_quality(
        self, symbol: str, legs: Sequence[tuple[Side, Decimal]]
    ) -> EntryQuality: ...


def _simulate_book_fill(levels: list[OrderBookLevel], qty: Decimal) -> Decimal | None:
    if qty <= 0:
        raise ValueError(f"qty must be positive, got {qty}")

    remaining = qty
    notional = Decimal(0)
    filled = Decimal(0)

    for level in levels:
        if remaining <= 0:
            break
        take = min(level.size, remaining)
        if take <= 0:
            continue
        notional += take * level.price
        filled += take
        remaining -= take

    return notional / filled if filled > 0 and filled == qty else None


def evaluate_entry_quality(
    order_book: OrderBook,
    legs: Sequence[tuple[Side, Decimal]],
) -> EntryQuality:
    bid_qty = sum((qty for side, qty in legs if side == "bid"), Decimal(0))
    ask_qty = sum((qty for side, qty in legs if side == "ask"), Decimal(0))
    bid_depth = sum((level.size for level in order_book.bids), Decimal(0))
    ask_depth = sum((level.size for level in order_book.asks), Decimal(0))

    avg_bid_price = _simulate_book_fill(order_book.asks, bid_qty) if bid_qty else None
    avg_ask_price = _simulate_book_fill(order_book.bids, ask_qty) if ask_qty else None
    entry_spread_pct = (
        abs(avg_ask_price - avg_bid_price) / avg_ask_price * 100
        if avg_bid_price is not None and avg_ask_price is not None and avg_ask_price > 0
        else None
    )

    return EntryQuality(
        avg_bid_price=avg_bid_price,
        avg_ask_price=avg_ask_price,
        entry_spread_pct=entry_spread_pct,
        bid_qty=bid_qty,
        ask_qty=ask_qty,
        bid_depth=bid_depth,
        ask_depth=ask_depth,
    )


async def wait_for_entry_quality(
    client: TradingClient, symbol: str, legs: Sequence[tuple[Side, Decimal]], cfg: StrategyConfig
) -> EntryQuality | None:
    if cfg.max_entry_spread_pct is None:
        return EntryQuality(None, None, None)

    log = logger.bind(account=client.name)
    timeout = cfg.entry_gate_wait
    poll_interval = cfg.entry_gate_poll
    deadline = time.time() + timeout
    warned = False
    last_quality: EntryQuality | None = None

    def fmt(x: Decimal | None) -> str:
        return f"{x:.2f}%" if x is not None else "n/a"

    def gate_msg(prefix: str, quality: EntryQuality) -> str:
        reason = quality.unavailable_reason
        detail = f" reason={reason}" if reason else ""
        return f"{symbol} gate {prefix} spread={fmt(quality.entry_spread_pct)}{detail}"

    while True:
        if isinstance(client, EntryQualityEstimator):
            quality = await client.estimate_entry_quality(symbol, legs)
        else:
            quality = evaluate_entry_quality(await client.get_order_book(symbol), legs)
        last_quality = quality
        if (
            quality.entry_spread_pct is not None
            and quality.entry_spread_pct <= cfg.max_entry_spread_pct
        ):
            return quality

        if not warned:
            log.debug(gate_msg("waiting", quality))
            warned = True

        remaining = deadline - time.time()
        if remaining <= 0:
            msg = gate_msg("skip", last_quality) if last_quality else f"{symbol} gate skip"
            log.debug(f"{msg} timeout={timeout:.0f}s")
            return None

        await asyncio.sleep(min(poll_interval, remaining))


# MARK: Limit order

_LIMIT_PRICE_DRIFT_PCT = Decimal("0.0025")  # 0.25% BBO drift → give up waiting, go to fallback


@dataclass
class LimitOrderWaitState:
    order_started_at: float
    wait_window_started_at: float
    last_log_at: float
    max_wait_retries: int
    bbo_stable_extensions: int = 0
    last_stable_drift: Decimal | None = None

    @classmethod
    def start(cls, max_wait_retries: int) -> "LimitOrderWaitState":
        now = time.time()
        return cls(
            order_started_at=now,
            wait_window_started_at=now,
            last_log_at=now,
            max_wait_retries=max_wait_retries,
        )

    @property
    def can_extend_bbo_stable(self) -> bool:
        return self.bbo_stable_extensions < self.max_wait_retries

    @property
    def retry_progress(self) -> str:
        return f"{self.bbo_stable_extensions}/{self.max_wait_retries}"

    def timeout_started_at(self, filled_since: float | None) -> float:
        return filled_since or self.wait_window_started_at

    def mark_bbo_stable(self, drift: Decimal) -> None:
        self.bbo_stable_extensions += 1
        self.last_stable_drift = drift
        self.wait_window_started_at = time.time()

    def elapsed(self) -> float:
        return time.time() - self.order_started_at

    def waiting_log(
        self, order: Order, side: Side, qty: Decimal, symbol: str, *, force: bool = False
    ) -> str | None:
        now = time.time()
        if not force and now - self.last_log_at < 30:
            return None

        fill_pct = f" ({order.filled / order.size:.0%})" if order.filled > 0 else ""
        extra = ""
        if self.bbo_stable_extensions and self.last_stable_drift is not None:
            extra = f" (BBO stable drift={self.last_stable_drift:.3%}, retry {self.retry_progress})"

        self.last_log_at = now
        elapsed = format_duration(now - self.order_started_at)
        return f"Limit {side} {qty} {symbol}: waiting{fill_pct} elapsed={elapsed}{extra}"


async def _fetch_limit_price(
    client: TradingClient, symbol: str, side: Side, tick_size: Decimal
) -> Decimal:
    """Fetch BBO and return tick-rounded price for the given side."""
    bid, ask = await client.get_bbo(symbol)
    return round_to_tick_size(bid if side == "bid" else ask, tick_size)


async def _cancel_or_market_fallback(
    client: TradingClient,
    order: Order,
    side: Side,
    symbol: str,
    reduce_only: bool,
    use_market_fallback: bool,
    timeout,
) -> Order | None:
    log = logger.bind(account=client.name)
    await client.cancel_order(order)
    remaining = order.size - order.filled
    if use_market_fallback and remaining > 0:
        log.debug(f"Limit timeout → market fallback {side} {remaining} {symbol}")
        return await client.market_order(symbol, side, remaining, reduce_only)
    raise RuntimeError(f"Limit {symbol} timed out after {timeout}s, no fallback")


async def fill_limit_order(
    client: TradingClient,
    symbol: str,
    side: Side,
    qty: Decimal,
    reduce_only=False,
    timeout=60,
    use_market_fallback=True,
    max_wait_retries: int = 9,
) -> Order | None:
    """Place limit order and wait for fill with optional market fallback."""
    tick_size = await client.get_tick_size(symbol)
    price = await _fetch_limit_price(client, symbol, side, tick_size)

    log = logger.bind(account=client.name)
    log.debug(f"Limit {side} {qty} {symbol} @ {price}")
    order = await client.limit_order(symbol, side, qty, price, reduce_only)
    if order.status == OrderStatus.FILLED:
        return order  # already filled (e.g. exchange falls back to market internally)

    order_id = order.id
    wait = LimitOrderWaitState.start(max_wait_retries)
    filled_since = None
    poll_delay = 0.25  # starts at 250ms, grows to ~3s

    while True:
        await asyncio.sleep(poll_delay)
        poll_delay = min(poll_delay * 2.5, 3.0)

        order = await client.get_order(order_id)
        if order is None:
            if wait.elapsed() > timeout:
                raise AppError(f"Limit order {order_id} never appeared — unknown state, aborting")
            continue  # archive lag — keep polling

        if order.status == OrderStatus.FILLED:
            log.debug(f"Limit {side} {qty} {symbol} filled in {format_duration(wait.elapsed())}")
            return order

        if order.status == OrderStatus.CANCELED:
            elapsed = wait.elapsed()
            raise RuntimeError(
                f"Limit {symbol} canceled by exchange after {elapsed:.0f}s"
                f" (filled {order.filled}/{order.size})"
            )

        if order.filled > 0 and filled_since is None:
            filled_since = time.time()

        check_time = wait.timeout_started_at(filled_since)
        if (time.time() - check_time) > timeout:
            current_price = await _fetch_limit_price(client, symbol, side, tick_size)
            drift = abs(current_price - price) / price
            if drift <= _LIMIT_PRICE_DRIFT_PCT:
                if not wait.can_extend_bbo_stable:
                    log.debug(
                        f"Limit order timeout after {timeout}s "
                        f"(BBO stable drift {drift:.3%}, "
                        f"retries exhausted {wait.retry_progress})"
                    )
                    return await _cancel_or_market_fallback(
                        client, order, side, symbol, reduce_only, use_market_fallback, timeout
                    )

                wait.mark_bbo_stable(drift)
                if msg := wait.waiting_log(order, side, qty, symbol, force=True):
                    log.debug(msg)
                filled_since = None
                continue

            log.debug(f"Limit order timeout after {timeout}s (BBO drift {drift:.3%})")
            return await _cancel_or_market_fallback(
                client, order, side, symbol, reduce_only, use_market_fallback, timeout
            )


# MARK: Execution primitives


async def close_all(accs: Sequence[TradingClient], _attempts: int = 3) -> None:
    """Best-effort cleanup — retries on failure, never raises. cancel/close are idempotent."""
    n_failed = 0
    for attempt in range(_attempts):
        rs1 = await asyncio.gather(*[a.cancel_all_orders() for a in accs], return_exceptions=True)
        rs2 = await asyncio.gather(*[a.close_all_positions() for a in accs], return_exceptions=True)

        n_orders = sum(r for r in rs1 if isinstance(r, int))
        n_positions = sum(r for r in rs2 if isinstance(r, int))
        n_failed = sum(1 for r in [*rs1, *rs2] if isinstance(r, Exception))
        if n_orders + n_positions > 0:
            logger.info(f"Closed {n_orders} orders and {n_positions} positions")
        if not n_failed:
            return

        if attempt < _attempts - 1:
            logger.warning(f"close_all: {n_failed} failed, retrying ({attempt + 1}/{_attempts})...")
            await asyncio.sleep(2.0 * 2**attempt)

    logger.warning(f"close_all: still {n_failed} account(s) failed after {_attempts} attempts")
