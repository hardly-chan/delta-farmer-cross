# delta-farmer | https://github.com/vladkens/delta-farmer
# Copyright (c) vladkens | MIT License | Refactoring is just future procrastination
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TypeVar

from clients.nado import NadoClient, NadoPoint, NadoTrade
from lib.cli import create_cli, run_app
from lib.models import AccountConfig
from lib.store import DataStore
from lib.table import AutoTable, Column, PeriodRow, render_stats
from lib.utils import gather_accs, parse_filter, short_addr
from strategy.delta import run_groups
from strategy.models import StrategyConfig, load_config
from strategy.trading import close_all

T = TypeVar("T")
DD = defaultdict[str, defaultdict[str, T]]

_EPOCH_PREFIX: dict[str, str] = {"Private Alpha": "ALP", "Off Season": "OFF"}


def _epoch_label(ep: NadoPoint) -> str:
    start = ep.since.strftime("%b%d")
    end = (ep.until - timedelta(seconds=1)).strftime("%b%d") if ep.until != ep.since else start
    desc = ep.description
    if desc.startswith("Week "):
        n = int(desc.split()[1])
        return f"W{n:02d} {start}-{end}"
    prefix = _EPOCH_PREFIX.get(desc, desc[:3].upper())
    return f"{prefix} {start}-{end}"


class Config(StrategyConfig):
    accounts: list[AccountConfig]

    @classmethod
    def load(cls, filepath: str):
        return load_config(cls, filepath)


# MARK: Storages


async def sync_trades(acc: NadoClient, ttl: int) -> list[NadoTrade]:
    store_path = f".cache/nado_{short_addr(acc.address)}_trades.pkl"
    store = DataStore(store_path, id_key="digest", model=NadoTrade)
    await store.sync(lambda since: acc.trades(since), ttl_sec=ttl)
    return store.get_all()


async def sync_points(acc: NadoClient, ttl: int) -> list[NadoPoint]:
    store_path = f".cache/nado_{short_addr(acc.address)}_points.pkl"
    store = DataStore(store_path, id_key="since", model=NadoPoint)
    await store.sync(lambda _: acc.points(), ttl_sec=ttl)
    return store.get_all()


# MARK: Reports


async def print_info(accs: list[NadoClient]):
    tbl = AutoTable(
        Column("", justify="left"),
        Column("Account", justify="left"),
        Column("Address", justify="left"),
        Column("Volume", "{:,.0f}", total=sum),
        Column("Burn", "{:,.2f}", total=sum),
        Column("Points", "{:,.2f}", total=sum),
        Column("P/Price", "{:,.4f}", compute=lambda r: r["Burn"] / r["Points"]),
        Column("Balance", "{:,.2f}", total=sum),
    )

    async def row(acc: NadoClient):
        await acc.warmup()
        p = await acc.profile() if await acc.registered() else None
        a = short_addr(acc.address)
        if not p:
            return ("✗", acc.name, a, 0, 0, 0, 0)
        return ("✓", acc.name, a, p.volume, -p.pnl, p.points, p.balance)

    for r in await asyncio.gather(*[row(acc) for acc in accs]):
        tbl.add_row(*r)

    tbl.print()


async def print_stats(accs: list[NadoClient], period="week", filter_period="all", force=False):
    gtrades: DD[list[NadoTrade]] = defaultdict(lambda: defaultdict(list))
    gpoints: DD[Decimal] = defaultdict(lambda: defaultdict(Decimal))
    ttl = 0 if force else 3600

    all_trades, all_points = await asyncio.gather(
        gather_accs(accs, lambda acc: sync_trades(acc, ttl)),
        gather_accs(accs, lambda acc: sync_points(acc, ttl)),
    )

    # Epoch boundaries come from the API — use them for both trades and points
    epochs = sorted(all_points[0] if all_points else [], key=lambda ep: ep.since)

    def period_fn(dt: datetime) -> str:
        if period == "day":
            return dt.strftime("%Y-%m-%d")
        for ep in epochs:
            if ep.since <= dt < ep.until:
                return _epoch_label(ep)
        return dt.strftime("%Y-%m-%d")  # fallback: unmatched (before/after all epochs)

    for acc, trades in zip(accs, all_trades):
        for t in trades:
            gtrades[period_fn(t.created_at)][acc.name].append(t)
    for acc, pts in zip(accs, all_points):
        for p in pts:
            gpoints[period_fn(p.since)][acc.name] = p.points

    epoch_order = {_epoch_label(ep): ep.since for ep in epochs}
    all_periods = sorted(
        gtrades.keys() | gpoints.keys(),
        key=lambda k: (
            epoch_order[k]
            if k in epoch_order
            else datetime.fromisoformat(k).replace(tzinfo=timezone.utc)
        ),
    )
    periods_to_show = parse_filter(filter_period, all_periods)
    all_names = [x.name for x in accs]

    periods_data: dict[str, list[PeriodRow]] = {}
    for pk in all_periods:
        acc_names = [n for n in all_names if n in (gtrades[pk].keys() | gpoints[pk].keys())]
        rows = []
        for acc_name in acc_names:
            trades = gtrades[pk].get(acc_name, [])
            points = gpoints[pk].get(acc_name, Decimal(0))
            vol = sum((t.amount * t.price for t in trades), Decimal(0))
            pnl = sum((t.realized_pnl - t.fee for t in trades), Decimal(0))
            fee = sum((t.fee for t in trades), Decimal(0))
            rows.append(PeriodRow(acc_name, len(trades), vol, -pnl, points, fee))
        periods_data[pk] = rows

    render_stats(periods_data, periods_to_show, pprice_fmt="{:,.2f}")


# MARK: Main


def client_from_config(cfg: AccountConfig) -> NadoClient:
    return NadoClient(name=cfg.name, privkey=cfg.privkey.get_secret_value(), proxy=cfg.proxy)


async def main():
    cli = await create_cli("nado", "configs/nado.toml", ["privkey"])
    cfg = Config.load(cli.config)

    accs = [(client_from_config(x), x.enabled) for x in cfg.accounts]
    all_accs, act_accs = [c for c, _ in accs], [c for c, e in accs if e]

    match cli.command:
        case "info":
            await print_info(all_accs)
        case "stats":
            await print_stats(all_accs, period=cli.group, filter_period=cli.filter, force=cli.force)
        case "close":
            await close_all(act_accs)
        case "trade":
            await run_groups(cfg, act_accs)


if __name__ == "__main__":
    run_app(main())
