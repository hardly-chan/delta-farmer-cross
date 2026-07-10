import asyncio
import time
from decimal import Decimal

from lib import telegram as tg
from lib import telemetry
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
        telemetry.track(
            "spread_trade_started",
            {
                "symbol": self.cfg.symbol,
                "leverage": self.cfg.leverage,
                "trade_size_mode": "pct" if self.cfg.trade_size_pct is not None else "usd",
                "min_open_spread_pct": float(self.cfg.min_open_spread_pct),
                "min_close_spread_pct": float(self.cfg.min_close_spread_pct),
                "min_open_time": int(self.cfg.min_open_time),
                "max_abs_pnl_usd_enabled": self.cfg.max_abs_pnl_usd is not None,
                "max_abs_roi_enabled": self.cfg.max_abs_roi is not None,
                "telegram_enabled": tg.enabled(),
            },
        )

        failures = 0
        active_plan: SpreadPlan | None = None
        active_msgid: int | None = None
        opened_at: float | None = None
        hold_ready = False

        while True:
            if self.stop_event and self.stop_event.is_set():
                if active_plan is not None:
                    await close_spread(active_plan)
                return

            try:
                if active_plan is None:
                    opened = await self._try_open_trade()
                    if opened is None:
                        await self._wait(self.cfg.poll_interval)
                        continue

                    active_plan, active_msgid = opened
                    opened_at = time.time()

                    failures = 0
                    hold_ready = False

                    # Keep the spread open for a minimum hold window before checking close conditions.
                    await self._wait(self.cfg.min_open_time)
                    hold_ready = True
                    continue

                if not hold_ready:
                    await self._wait(self.cfg.position_check_interval)
                    continue

                now_spread, now_pnl, now_roi = await self._plan_metrics(active_plan)
                logger.info(
                    f"Close check {active_plan.symbol}: spread={now_spread:.3f}% "
                    f"target<={self.cfg.min_close_spread_pct:.3f}%"
                )
                if now_spread <= self.cfg.min_close_spread_pct:
                    logger.info(
                        f"Spread mean-reverted to {now_spread:.3f}% (<= {self.cfg.min_close_spread_pct:.3f}%), closing"
                    )
                    await self._close_with_report(
                        active_plan,
                        pnl=now_pnl,
                        msgid=active_msgid,
                        opened_at=opened_at,
                        reason="spread_reverted",
                    )
                    active_plan = None
                    active_msgid = None
                    opened_at = None
                    hold_ready = False
                    await self._wait(self.cfg.cooldown_after_close)
                    continue

                if self.cfg.max_abs_pnl_usd is not None and abs(now_pnl) >= self.cfg.max_abs_pnl_usd:
                    logger.warning(
                        f"Combined PnL hit {now_pnl:+.2f} USD (limit {self.cfg.max_abs_pnl_usd:.2f}), closing"
                    )
                    await self._close_with_report(
                        active_plan,
                        pnl=now_pnl,
                        msgid=active_msgid,
                        opened_at=opened_at,
                        reason="pnl_limit",
                    )
                    active_plan = None
                    active_msgid = None
                    opened_at = None
                    hold_ready = False
                    await self._wait(self.cfg.cooldown_after_close)
                    continue

                if self.cfg.max_abs_roi is not None and abs(now_roi) >= self.cfg.max_abs_roi:
                    logger.warning(
                        f"Combined ROI hit {now_roi:+.2%} (limit {self.cfg.max_abs_roi:.2%}), closing"
                    )
                    await self._close_with_report(
                        active_plan,
                        pnl=now_pnl,
                        msgid=active_msgid,
                        opened_at=opened_at,
                        reason="roi_limit",
                    )
                    active_plan = None
                    active_msgid = None
                    opened_at = None
                    hold_ready = False
                    await self._wait(self.cfg.cooldown_after_close)
                    continue

                await self._wait(self.cfg.position_check_interval)
            except asyncio.CancelledError:
                if active_plan is not None:
                    await self._close_with_report(
                        active_plan,
                        pnl=Decimal(0),
                        msgid=active_msgid,
                        opened_at=opened_at,
                        reason="cancelled",
                    )
                return
            except Exception as e:
                if active_plan is not None:
                    try:
                        pnl = Decimal(0)
                        try:
                            _spread, pnl, _roi = await self._plan_metrics(active_plan)
                        except Exception:
                            pass
                        await self._close_with_report(
                            active_plan,
                            pnl=pnl,
                            msgid=active_msgid,
                            opened_at=opened_at,
                            reason="exception",
                        )
                    finally:
                        active_plan = None
                        active_msgid = None
                        opened_at = None
                        hold_ready = False

                failures += 1
                logger.warning(f"Spread cycle failed ({failures}): {type(e).__name__}: {e}")
                telemetry.track(
                    "spread_cycle_failed",
                    {
                        "failures": failures,
                        "error_type": type(e).__name__,
                    },
                )
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

    async def _try_open_trade(self) -> tuple[SpreadPlan, int | None] | None:
        omni_bid, omni_ask, nado_bid, nado_ask = await self._bbo()
        signal = detect_spread_direction(
            omni_bid, omni_ask, nado_bid, nado_ask, self.cfg.min_open_spread_pct
        )
        omni_long_spread = calc_cross_spread_pct(omni_ask, nado_bid)
        nado_long_spread = calc_cross_spread_pct(nado_ask, omni_bid)
        logger.info(
            f"Open check {self.cfg.symbol}: omni_long={omni_long_spread:.3f}% "
            f"nado_long={nado_long_spread:.3f}% target>={self.cfg.min_open_spread_pct:.3f}%"
        )
        if signal is None:
            logger.debug(
                f"No signal {self.cfg.symbol}: omni=({omni_bid}/{omni_ask}) nado=({nado_bid}/{nado_ask})"
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
        telemetry.track(
            "spread_trade_opened",
            {
                "symbol": plan.symbol,
                "direction": plan.direction,
                "spread_pct": float(plan.spread_pct),
                "qty": float(plan.qty),
                "notional_usd": float(plan.notional_usd),
            },
        )
        if msgid is not None:
            logger.debug(f"Telegram message id: {msgid}")

        return plan, msgid

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

    async def _plan_metrics(self, plan: SpreadPlan) -> tuple[Decimal, Decimal, Decimal]:
        omni_bid, omni_ask, nado_bid, nado_ask = await self._bbo()
        # Exit spread uses CLOSE prices — the opposite side of the book from entry:
        #   omni_long: opened by buying at omni_ask / selling at nado_bid;
        #              closed by selling at omni_bid / buying back at nado_ask.
        #   nado_long: opened by buying at nado_ask / selling at omni_bid;
        #              closed by selling at nado_bid / buying back at omni_ask.
        if plan.direction == "omni_long":
            spread = calc_cross_spread_pct(omni_bid, nado_ask)
        else:
            spread = calc_cross_spread_pct(nado_bid, omni_ask)

        long_price, short_price = await asyncio.gather(
            plan.long_client.get_price(plan.symbol),
            plan.short_client.get_price(plan.symbol),
        )
        pnl = plan.qty * (long_price - plan.long_entry_price)
        pnl += plan.qty * (plan.short_entry_price - short_price)

        entry_notional = plan.qty * (plan.long_entry_price + plan.short_entry_price)
        roi = pnl / entry_notional if entry_notional else Decimal(0)
        return spread, pnl, roi

    async def _close_with_report(
        self,
        plan: SpreadPlan,
        *,
        pnl: Decimal,
        msgid: int | None,
        opened_at: float | None,
        reason: str,
    ) -> None:
        await close_spread(plan)

        balances = await asyncio.gather(plan.long_client.balance(), plan.short_client.balance())
        bal_rows = [
            (plan.long_client.name, float(balances[0])),
            (plan.short_client.name, float(balances[1])),
        ]
        duration = max(0.0, time.time() - opened_at) if opened_at is not None else 0.0
        await tg.on_trade_stop(float(pnl), duration, float(plan.notional_usd), bal_rows, msgid)
        telemetry.track(
            "spread_trade_closed",
            {
                "symbol": plan.symbol,
                "direction": plan.direction,
                "pnl_usd": float(pnl),
                "duration_sec": duration,
                "reason": reason,
            },
        )

    async def _wait(self, wait_sec: int) -> None:
        logger.info(utils.wait_msg(wait_sec))
        await utils.interruptible_sleep(wait_sec, self.stop_event)




