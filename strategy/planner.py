# delta-farmer | https://github.com/vladkens/delta-farmer
# Copyright (c) vladkens | MIT License | Small plans, fewer surprises
import random
from decimal import Decimal
from typing import Sequence

from lib.utils import find_safe_pair, round_to_tick_size
from strategy.execution import TradeAction
from strategy.trading import Side, TradingClient, opposite_side

_MARKET_SIZE_TICK = Decimal("0.001")
_USD_TICK = Decimal("0.01")
SAFE_PCT = Decimal("0.96")  # leave 4% margin to avoid liquidation on leverage rounding


def calc_total_from_pct(balances: list[tuple[str, float]], leverage: int, pct: float) -> Decimal:
    """Compute max safe total trade size from account balances in execution order.

    ordered_balances[0] is main (gets 50% of total).
    ordered_balances[1:] are hedge accounts (split the remaining 50% equally).
    The binding constraint is the account whose balance is smallest relative to its share.
    """
    n = len(balances)
    n_hedge = n - 1
    if n_hedge > 0:
        shares = [Decimal("0.5")] + [Decimal("0.5") / n_hedge] * n_hedge
    else:
        shares = [Decimal("1")]

    max_totals = [
        Decimal(str(bal)) * leverage * SAFE_PCT * Decimal(str(pct)) / share
        for (_, bal), share in zip(balances, shares)
    ]
    return round_to_tick_size(min(max_totals), _USD_TICK)


def calc_symbol_sizes(
    total: Decimal,
    symbols: Sequence[str],
    main_side: Side,
) -> dict[str, tuple[Decimal, Side]]:
    if not symbols:
        return {}

    if len(symbols) > 4:
        raise ValueError("up to 4 symbols are supported")

    if len(symbols) == 1:
        return {symbols[0]: (total, main_side)}

    n_main = len(symbols) // 2
    n_rest = len(symbols) - n_main
    half = total * Decimal("0.5")
    out: dict[str, tuple[Decimal, Side]] = {}

    for i, symbol in enumerate(symbols):
        if i < n_main:
            size = round_to_tick_size(half / n_main, _MARKET_SIZE_TICK)
            side = main_side
        else:
            size = round_to_tick_size(half / n_rest, _MARKET_SIZE_TICK)
            side = opposite_side(main_side)
        out[symbol] = (size, side)

    return out


async def plan_symbol_actions(
    accounts: Sequence[TradingClient],
    symbols: Sequence[str],
    total_size_usd: Decimal,
    leverage: int,
    balances: list[tuple[str, float]],
) -> dict[str, list[TradeAction]] | None:
    pairs = find_safe_pair(balances, float(total_size_usd), leverage)
    if pairs is None:
        return None

    accounts_map = {acc.name: acc for acc in accounts}
    total_size = Decimal(sum(size for _, size in pairs))  # todo: to_tick
    main_side: Side = random.choice(["bid", "ask"])
    symbol_sizes = calc_symbol_sizes(total_size, symbols, main_side)
    plan: dict[str, list[TradeAction]] = {}

    for symbol, (symbol_size, symbol_main_side) in symbol_sizes.items():
        ratio = symbol_size / total_size
        actions: list[TradeAction] = []

        for j, (name, size) in enumerate(pairs):
            actions.append(
                TradeAction(
                    client=accounts_map[name],
                    side=symbol_main_side if j == 0 else opposite_side(symbol_main_side),
                    size_usd=Decimal(str(size)) * ratio,
                )
            )

        plan[symbol] = actions

    return plan
