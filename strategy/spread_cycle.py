import asyncio
from decimal import Decimal

from lib import telegram as tg
from lib import utils
from lib.logger import logger

from .models import TradingClient
from .spread_models import SpreadConfig
from .spread_trade import (
    SpreadPlan,
    aligned_qty,
    calc_cross_spread_pct,
    close_spread,
    detect_spread_direction,
    ensure_min_trade_notional,
    open_spread,
)
from .symbols import ensure_exchange_symbols

SAFE_PCT = Decimal("0.96")


class SpreadStrategy:
    def __init__(
        self,
        cfg: SpreadConfig,
        omni: TradingClient,
        nado: TradingClient,
        stop_event: asyncio.Event | None = None,
    ):
        self.cfg = cfg
        self.omni = omni
        self.nado = nado
        self.stop_event = stop_event

    async def run(self) -> None:
        await self._startup_checks()
        tg.start()

        failures = 0
        active_plan: SpreadPlan | None = None
        hold_ready = False

        while True:
            if self.stop_event and self.stop_event.is_set():
                if active_plan is not None:
                    await close_spread(active_plan)
                return

            try:
                if active_plan is None:
                    active_plan = await self._try_open_trade()
                    if active_plan is None:
                        await self._wait(self.cfg.poll_interval)
                        continue

                    failures = 0
                    hold_ready = False

                    # Keep the spread open for a minimum hold window before checking close conditions.
                    await self._wait(self.cfg.min_open_time)
                    hold_ready = True
                    continue

                if not hold_ready:
                    await self._wait(self.cfg.position_check_interval)
                    continue

                now_spread = await self._plan_spread(active_plan)
                if now_spread <= self.cfg.min_close_spread_pct:
                    logger.info(
                        f"Spread mean-reverted to {now_spread:.3f}% (<= {self.cfg.min_close_spread_pct:.3f}%), closing"
                    )
                    await close_spread(active_plan)
                    active_plan = None
                    hold_ready = False
                    await self._wait(self.cfg.cooldown_after_close)
                    continue

                await self._wait(self.cfg.position_check_interval)
            except asyncio.CancelledError:
                if active_plan is not None:
                    await close_spread(active_plan)
                return
            except Exception as e:
                if active_plan is not None:
                    try:
                        await close_spread(active_plan)
                    finally:
                        active_plan = None
                        hold_ready = False

                failures += 1
                logger.warning(f"Spread cycle failed ({failures}): {type(e).__name__}: {e}")
                await tg.on_error(f"{type(e).__name__}: {e}", failures, 10)
                if self.cfg.max_failures > 0 and failures >= self.cfg.max_failures:
                    await tg.on_crash(f"Too many failures: {type(e).__name__}: {e}")
                    return
                await self._wait(10)

    async def _startup_checks(self) -> None:
        await asyncio.gather(self.omni.warmup(), self.nado.warmup())

        regs = await asyncio.gather(self.omni.registered(), self.nado.registered())
        failed = [c.name for c, ok in zip((self.omni, self.nado), regs) if not ok]
        if failed:
            raise RuntimeError(f"Not registered: {', '.join(failed)}")

        async def _has_symbol(client: TradingClient, symbol: str) -> bool:
            await client.get_lot_size(symbol)
            return True

        await ensure_exchange_symbols(
            [self.omni, self.nado],
            [self.cfg.symbol],
            _has_symbol,
        )

    async def _try_open_trade(self) -> SpreadPlan | None:
        omni_bid, omni_ask, nado_bid, nado_ask = await self._bbo()
        signal, omni_val, nado_val = detect_spread_direction(
            omni_bid, omni_ask, nado_bid, nado_ask, self.cfg.min_open_spread_pct
        )
        if signal is None:
            logger.debug(
                f"No signal {self.cfg.symbol}: omni=({omni_bid}/{omni_ask}) nado=({nado_bid}/{nado_ask}) "
		f"max_spread = {max(omni_val, nado_val):.3f}%"
            )
            return None

        direction, spread = signal
        if direction == "omni_long":
            long_client, short_client = self.omni, self.nado
            long_price, short_price = omni_ask, nado_bid
        else:
            long_client, short_client = self.nado, self.omni
            long_price, short_price = nado_ask, omni_bid

        await asyncio.gather(
            long_client.set_leverage(self.cfg.symbol, self.cfg.leverage),
            short_client.set_leverage(self.cfg.symbol, self.cfg.leverage),
        )

        size_usd = await self._trade_size_usd()
        qty = await aligned_qty(
            self.cfg.symbol,
            long_client,
            short_client,
            size_usd,
            long_price,
            short_price,
        )
        await ensure_min_trade_notional(
            self.cfg.symbol,
            long_client,
            short_client,
            qty,
            long_price,
            short_price,
        )

        plan = SpreadPlan(
            symbol=self.cfg.symbol,
            direction=direction,
            long_client=long_client,
            short_client=short_client,
            long_entry_price=long_price,
            short_entry_price=short_price,
            spread_pct=spread,
            qty=qty,
        )

        await open_spread(plan)
        msgid = await tg.on_trade_start(
            [self.cfg.symbol],
            float(plan.notional_usd),
            [plan.long_client.name, plan.short_client.name],
        )
        if msgid is not None:
            logger.debug(f"Telegram message id: {msgid}")

        return plan

    async def _trade_size_usd(self) -> Decimal:
        usd, pct = self.cfg.trade_size_usd, self.cfg.trade_size_pct
        if (usd is None) == (pct is None):
            raise RuntimeError("configure exactly one of trade_size_usd or trade_size_pct")

        if usd is not None:
            return Decimal(str(usd.sample()))

        balances = await asyncio.gather(self.omni.balance(), self.nado.balance())
        weakest = min(balances)
        return weakest * self.cfg.leverage * SAFE_PCT * Decimal(str(pct))

    async def _bbo(self) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        (omni_bid, omni_ask), (nado_bid, nado_ask) = await asyncio.gather(
            self.omni.get_bbo(self.cfg.symbol), self.nado.get_bbo(self.cfg.symbol)
        )
        return omni_bid, omni_ask, nado_bid, nado_ask

    async def _plan_spread(self, plan: SpreadPlan) -> Decimal:
        omni_bid, omni_ask, nado_bid, nado_ask = await self._bbo()
        if plan.direction == "omni_long":
            return calc_cross_spread_pct(omni_ask, nado_bid)
        return calc_cross_spread_pct(nado_ask, omni_bid)

    async def _wait(self, wait_sec: int) -> None:
        logger.info(utils.wait_msg(wait_sec))
        await utils.interruptible_sleep(wait_sec, self.stop_event)
