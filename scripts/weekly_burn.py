#!/usr/bin/env python3
"""Weekly burn (spending) report across all exchanges from local cache."""

import os
import pickle
import sys
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from lib.table import AutoTable, Column

CACHE = os.path.join(os.path.dirname(__file__), "..", ".cache")


def load_pkl(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, "rb") as fh:
        data = pickle.load(fh)
    return list(data.get("records", {}).values())


def glob_cache(prefix: str, suffix: str) -> list[str]:
    try:
        files = os.listdir(CACHE)
    except FileNotFoundError:
        return []
    return [os.path.join(CACHE, f) for f in files if f.startswith(prefix) and f.endswith(suffix)]


def to_week(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def parse_dt(s) -> datetime:
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(s).rstrip("Z")).replace(tzinfo=UTC)


# MARK: Per-exchange extractors — same formulas as apps/*.py


def ethereal_weeks() -> defaultdict:
    weeks: defaultdict[str, Decimal] = defaultdict(Decimal)
    for path in glob_cache("ethereal_", "_trades.pkl"):
        for r in load_pkl(path):
            dt = parse_dt(r["created_at"])
            pnl = (
                Decimal(str(r["realized_pnl"]))
                - Decimal(str(r["fees_usd"]))
                - Decimal(str(r["funding_usd"]))
            )
            weeks[to_week(dt)] -= pnl
    return weeks


def hyena_weeks() -> defaultdict:
    weeks: defaultdict[str, Decimal] = defaultdict(Decimal)
    for path in glob_cache("hyena_", "_fills.pkl"):
        for r in load_pkl(path):
            if not r.get("coin", "").startswith("hyna:"):
                continue
            dt = datetime.fromtimestamp(r["time"] / 1000, tz=UTC)
            weeks[to_week(dt)] -= Decimal(str(r.get("closedPnl", 0)))
    return weeks


def nado_weeks() -> defaultdict:
    weeks: defaultdict[str, Decimal] = defaultdict(Decimal)
    for path in glob_cache("nado_", "_trades.pkl"):
        for r in load_pkl(path):
            dt = parse_dt(r["created_at"])
            pnl = Decimal(str(r["realized_pnl"])) - Decimal(str(r["fee"]))
            weeks[to_week(dt)] -= pnl
    return weeks


def omni_weeks() -> defaultdict:
    weeks: defaultdict[str, Decimal] = defaultdict(Decimal)
    for path in glob_cache("omni_", "_transfers.pkl"):
        for r in load_pkl(path):
            if r.get("status") != "confirmed":
                continue
            if r.get("transfer_type") not in ("funding", "realized_pnl"):
                continue
            dt = parse_dt(r["created_at"])
            weeks[to_week(dt)] -= Decimal(str(r["qty"]))
    return weeks


ONYX_SINCE = datetime(2026, 3, 1, tzinfo=UTC)


def onyx_weeks() -> defaultdict:
    weeks: defaultdict[str, Decimal] = defaultdict(Decimal)
    for path in glob_cache("onyx_", "_fills.pkl"):
        for r in load_pkl(path):
            if ":" in r.get("coin", ""):  # exclude HIP-3 markets
                continue
            dt = datetime.fromtimestamp(r["time"] / 1000, tz=UTC)
            if dt < ONYX_SINCE:
                continue
            weeks[to_week(dt)] -= Decimal(str(r.get("closedPnl", 0)))
    return weeks


def pacifica_weeks() -> defaultdict:
    weeks: defaultdict[str, Decimal] = defaultdict(Decimal)
    for path in glob_cache("pacifica_", "_trades.pkl"):
        for r in load_pkl(path):
            dt = parse_dt(r["created_at"])
            weeks[to_week(dt)] -= Decimal(str(r["pnl"]))
    return weeks


def zero1_weeks() -> defaultdict:
    # fees not in cache for zero1 — minor undercount
    weeks: defaultdict[str, Decimal] = defaultdict(Decimal)
    for path in glob_cache("zero1_", "_history_pnl.pkl"):
        for r in load_pkl(path):
            dt = parse_dt(r["time"])
            weeks[to_week(dt)] -= Decimal(str(r["tradingPnl"]))
    for path in glob_cache("zero1_", "_history_funding.pkl"):
        for r in load_pkl(path):
            dt = parse_dt(r["time"])
            weeks[to_week(dt)] -= Decimal(str(r["fundingPnl"]))
    return weeks


EXCHANGES = [
    ("Ethereal", ethereal_weeks),
    ("Hyena", hyena_weeks),
    ("Nado", nado_weeks),
    ("Omni", omni_weeks),
    ("Onyx", onyx_weeks),
    ("Pacifica", pacifica_weeks),
    ("Zero1", zero1_weeks),
]


def main() -> int:
    data = {name: fn() for name, fn in EXCHANGES}

    all_weeks = sorted({w for d in data.values() for w in d})
    if not all_weeks:
        print("No cached data found.", file=sys.stderr)
        return 1

    # Only render exchanges that have any non-zero data
    active = [name for name, _ in EXCHANGES if any(v != 0 for v in data[name].values())]

    tbl = AutoTable(
        Column("Week", justify="left"),
        *[Column(name, "{:,.2f}", total=sum) for name in active],
        Column("Total", "{:,.2f}", compute=lambda r: sum(r[n] for n in active)),
    )

    for week in all_weeks:
        row_vals = [data[name].get(week, Decimal(0)) for name in active]
        tbl.add_row(week, *row_vals)

    tbl.print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
