import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from lib.logger import logger
from lib.utils import round_to_tick_size

from .models import Side, TradingClient

SpreadDirection = Literal["omni_long", "nado_long"]


@dataclass
class SpreadPlan:
    symbol: str
    direction: SpreadDirection
    long_client: TradingClient
    short_client: TradingClient
    long_entry_price: Decimal
    short_entry_price: Decimal
    spread_pct: Decimal
    qty: Decimal

    @property
    def notional_usd(self) -> Decimal:
        return self.qty * (self.long_entry_price + self.short_entry_price)


def calc_cross_spread_pct(long_entry_price: Decimal, short_entry_price: Decimal) -> Decimal:
    if long_entry_price <= 0:
        raise ValueError("long_entry_price must be positive")
    return (short_entry_price - long_entry_price) / long_entry_price * 100


def detect_spread_direction(
    omni_bid: Decimal,
    omni_ask: Decimal,
    nado_bid: Decimal,
    nado_ask: Decimal,
    min_open_spread_pct: Decimal,
) -> tuple[SpreadDirection, Decimal] | None:
    omni_long = calc_cross_spread_pct(omni_ask, nado_bid)
    nado_long = calc_cross_spread_pct(nado_ask, omni_bid)

    candidates: list[tuple[SpreadDirection, Decimal]] = []
    if omni_long >= min_open_spread_pct:
        candidates.append(("omni_long", omni_long))
    if nado_long >= min_open_spread_pct:
        candidates.append(("nado_long", nado_long))

    if not candidates:
        return None

    return max(candidates, key=lambda x: x[1])


async def aligned_qty(
    symbol: str,
    long_client: TradingClient,
    short_client: TradingClient,
    trade_size_usd: Decimal,
    long_entry_price: Decimal,
    short_entry_price: Decimal,
) -> Decimal:
    long_lot, short_lot = await asyncio.gather(
        long_client.get_lot_size(symbol), short_client.get_lot_size(symbol)
    )
    ref_price = (long_entry_price + short_entry_price) / 2
    raw_qty = trade_size_usd / ref_price
    long_qty = round_to_tick_size(raw_qty, long_lot)
    short_qty = round_to_tick_size(raw_qty, short_lot)
    qty = min(long_qty, short_qty)

    if qty <= 0:
        raise RuntimeError(f"Computed qty is zero for {symbol}; increase trade_size_usd")

    return qty


async def ensure_min_trade_notional(
    symbol: str,
    long_client: TradingClient,
    short_client: TradingClient,
    qty: Decimal,
    long_entry_price: Decimal,
    short_entry_price: Decimal,
) -> None:
    long_min, short_min = await asyncio.gather(
        long_client.get_min_trade_usd(symbol), short_client.get_min_trade_usd(symbol)
    )

    long_notional = qty * long_entry_price
    short_notional = qty * short_entry_price

    failed: list[str] = []
    if long_notional < long_min:
        failed.append(
            f"{long_client.name}: {long_notional:.2f} < min {long_min:.2f} USD for {symbol}"
        )
    if short_notional < short_min:
        failed.append(
            f"{short_client.name}: {short_notional:.2f} < min {short_min:.2f} USD for {symbol}"
        )

    if failed:
        raise RuntimeError("; ".join(failed))


async def open_spread(plan: SpreadPlan) -> None:
    long_task = plan.long_client.market_order(plan.symbol, "bid", plan.qty)
    short_task = plan.short_client.market_order(plan.symbol, "ask", plan.qty)
    long_res, short_res = await asyncio.gather(long_task, short_task, return_exceptions=True)

    long_ok = not isinstance(long_res, Exception)
    short_ok = not isinstance(short_res, Exception)

    if long_ok and short_ok:
        logger.info(
            f"Spread open {plan.symbol}: long={plan.long_client.name} short={plan.short_client.name} "
            f"qty={plan.qty} spread={plan.spread_pct:.3f}%"
        )
        return

    if long_ok:
        await _rollback_leg(plan.long_client, plan.symbol, "ask", plan.qty)
    if short_ok:
        await _rollback_leg(plan.short_client, plan.symbol, "bid", plan.qty)

    errors = []
    if isinstance(long_res, Exception):
        errors.append(f"long leg failed: {type(long_res).__name__}: {long_res}")
    if isinstance(short_res, Exception):
        errors.append(f"short leg failed: {type(short_res).__name__}: {short_res}")
    raise RuntimeError(" | ".join(errors))


async def close_spread(plan: SpreadPlan) -> None:
    long_close = plan.long_client.market_order(plan.symbol, "ask", plan.qty, reduce_only=True)
    short_close = plan.short_client.market_order(plan.symbol, "bid", plan.qty, reduce_only=True)
    long_res, short_res = await asyncio.gather(long_close, short_close, return_exceptions=True)

    errors = []
    if isinstance(long_res, Exception):
        errors.append(f"long close failed: {type(long_res).__name__}: {long_res}")
    if isinstance(short_res, Exception):
        errors.append(f"short close failed: {type(short_res).__name__}: {short_res}")

    if errors:
        raise RuntimeError(" | ".join(errors))

    logger.info(
        f"Spread close {plan.symbol}: long={plan.long_client.name} short={plan.short_client.name} qty={plan.qty}"
    )


async def _rollback_leg(client: TradingClient, symbol: str, side: Side, qty: Decimal) -> None:
    try:
        await client.market_order(symbol, side, qty, reduce_only=True)
        logger.warning(f"Rollback completed on {client.name}: {side} {qty} {symbol}")
    except Exception as e:
        logger.error(f"Rollback failed on {client.name}: {type(e).__name__}: {e}")
