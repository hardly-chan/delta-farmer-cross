#!/usr/bin/env python3
"""Weekly trading report — reads from local cache (.cache/).

To refresh data for an exchange, run its stats command first:
  uv run apps/hyena.py stats
  uv run apps/nado.py stats
  ... (same for other exchanges)

Usage:
  uv run scripts/weekly.py              # snapshot: all exchanges, latest week
  uv run scripts/weekly.py -1           # snapshot: one week back
  uv run scripts/weekly.py -e Hyena     # Hyena: all periods (vol/burn/pts)
  uv run scripts/weekly.py --burn       # burn pivot: all exchanges × ISO weeks
"""

import argparse
import os
import pickle
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from clients.ethereal import EtherealClient
from clients.hyena import HyenaClient, HyenaHistoryItem
from clients.nado import NadoClient
from clients.omni import OmniClient
from clients.onyx import OnyxClient
from clients.pacifica import PacificaClient
from clients.zero1 import ZeroOneClient
from lib.table import AutoTable, Column

CACHE = os.path.join(os.path.dirname(__file__), "..", ".cache")


# MARK: Shared utils


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


def parse_dt(s) -> datetime:
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(s).rstrip("Z")).replace(tzinfo=UTC)


def _to_iso_week(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


# MARK: Stats extractors (vol + burn, native periods)

Stats = dict[str, tuple[Decimal, Decimal]]  # {period_label: (vol, burn)}
Pts = dict[str, Decimal]  # {period_label: points}


def ethereal_stats() -> Stats:
    out: Stats = {}
    for path in glob_cache("ethereal_", "_trades.pkl"):
        for r in load_pkl(path):
            lbl = EtherealClient.to_week_label(parse_dt(r["created_at"]))
            vol, burn = out.get(lbl, (Decimal(0), Decimal(0)))
            vol += Decimal(str(r["total_inc"])) + Decimal(str(r["total_dec"]))
            pnl = (
                Decimal(str(r["realized_pnl"]))
                - Decimal(str(r["fees_usd"]))
                - Decimal(str(r["funding_usd"]))
            )
            out[lbl] = (vol, burn - pnl)
    return out


def nado_stats() -> Stats:
    out: Stats = {}
    for path in glob_cache("nado_", "_trades.pkl"):
        for r in load_pkl(path):
            lbl = NadoClient.to_week_label(parse_dt(r["created_at"]))
            vol, burn = out.get(lbl, (Decimal(0), Decimal(0)))
            vol += Decimal(str(r["amount"])) * Decimal(str(r["price"]))
            pnl = Decimal(str(r["realized_pnl"])) - Decimal(str(r["fee"]))
            out[lbl] = (vol, burn - pnl)
    return out


def omni_stats() -> Stats:
    vols: defaultdict[str, Decimal] = defaultdict(Decimal)
    burns: defaultdict[str, Decimal] = defaultdict(Decimal)
    for path in glob_cache("omni_", "_trades.pkl"):
        for r in load_pkl(path):
            if r.get("status") != "confirmed":
                continue
            lbl = OmniClient.to_week_label(parse_dt(r["created_at"]))
            vols[lbl] += Decimal(str(r["price"])) * Decimal(str(r["qty"]))
    for path in glob_cache("omni_", "_transfers.pkl"):
        for r in load_pkl(path):
            if r.get("status") != "confirmed" or r.get("transfer_type") not in (
                "funding",
                "realized_pnl",
            ):
                continue
            lbl = OmniClient.to_week_label(parse_dt(r["created_at"]))
            burns[lbl] -= Decimal(str(r["qty"]))
    all_lbls = set(vols) | set(burns)
    return {lbl: (vols[lbl], burns[lbl]) for lbl in all_lbls}


def pacifica_stats() -> Stats:
    out: Stats = {}
    for path in glob_cache("pacifica_", "_trades.pkl"):
        for r in load_pkl(path):
            lbl = PacificaClient.to_week_label(parse_dt(r["created_at"]))
            vol, burn = out.get(lbl, (Decimal(0), Decimal(0)))
            vol += Decimal(str(r["amount"])) * Decimal(str(r["price"]))
            out[lbl] = (vol, burn - Decimal(str(r["pnl"])))
    return out


def zero1_stats() -> Stats:
    vols: defaultdict[str, Decimal] = defaultdict(Decimal)
    burns: defaultdict[str, Decimal] = defaultdict(Decimal)
    seen: set[str] = set()
    for path in glob_cache("zero1_", "_trades_maker.pkl") + glob_cache(
        "zero1_", "_trades_taker.pkl"
    ):
        for r in load_pkl(path):
            tid = str(r.get("tradeId", r.get("uid", "")))
            if tid in seen:
                continue
            seen.add(tid)
            lbl = ZeroOneClient.to_week_label(parse_dt(r["time"]))
            vols[lbl] += Decimal(str(r["price"])) * Decimal(str(r["baseSize"]))
            if "fee" in r:
                burns[lbl] += Decimal(str(r["fee"]))
    for path in glob_cache("zero1_", "_history_pnl.pkl"):
        for r in load_pkl(path):
            lbl = ZeroOneClient.to_week_label(parse_dt(r["time"]))
            burns[lbl] -= Decimal(str(r["tradingPnl"]))
    for path in glob_cache("zero1_", "_history_funding.pkl"):
        for r in load_pkl(path):
            lbl = ZeroOneClient.to_week_label(parse_dt(r["time"]))
            burns[lbl] -= Decimal(str(r["fundingPnl"]))
    all_lbls = set(vols) | set(burns)
    return {lbl: (vols[lbl], burns[lbl]) for lbl in all_lbls}


def hyena_stats() -> Stats:
    out: Stats = {}
    for path in glob_cache("hyena_", "_fills.pkl"):
        for r in load_pkl(path):
            dt = datetime.fromtimestamp(r["time"] / 1000, tz=UTC)
            lbl = HyenaClient.to_week_label(dt)
            vol, burn = out.get(lbl, (Decimal(0), Decimal(0)))
            vol += Decimal(str(r["px"])) * Decimal(str(r["sz"]))
            out[lbl] = (vol, burn - Decimal(str(r.get("closedPnl", 0))))
    return out


def onyx_stats() -> Stats:
    out: Stats = {}
    for path in glob_cache("onyx_", "_fills.pkl"):
        for r in load_pkl(path):
            dt = datetime.fromtimestamp(r["time"] / 1000, tz=UTC)
            lbl = OnyxClient.to_week_label(dt)
            vol, burn = out.get(lbl, (Decimal(0), Decimal(0)))
            vol += Decimal(str(r["px"])) * Decimal(str(r["sz"]))
            out[lbl] = (vol, burn - Decimal(str(r.get("closedPnl", 0))))
    return out


# MARK: Points extractors


def _pts_by_period(prefix: str, suffix: str, dt_key: str, pts_keys: list[str], period_fn) -> Pts:
    out: defaultdict[str, Decimal] = defaultdict(Decimal)
    for path in glob_cache(prefix, suffix):
        for r in load_pkl(path):
            lbl = period_fn(parse_dt(r[dt_key]))
            out[lbl] += sum(Decimal(str(r[k])) for k in pts_keys if k in r)
    return dict(out)


def ethereal_pts() -> Pts:
    return _pts_by_period(
        "ethereal_",
        "_points.pkl",
        "started_at",
        ["points", "referral_points"],
        EtherealClient.to_week_label,
    )


def nado_pts() -> Pts:
    return _pts_by_period("nado_", "_points.pkl", "since", ["points"], NadoClient.to_week_label)


def omni_pts() -> Pts:
    return _pts_by_period(
        "omni_",
        "_points.pkl",
        "start_window",
        ["total_points"],
        OmniClient.to_week_label,
    )


def pacifica_pts() -> Pts:
    return _pts_by_period(
        "pacifica_",
        "_points.pkl",
        "start_window",
        ["total_points"],
        PacificaClient.to_week_label,
    )


def zero1_pts() -> Pts:
    return _pts_by_period(
        "zero1_",
        "_points.pkl",
        "start_window",
        ["points"],
        ZeroOneClient.to_week_label,
    )


def hyena_pts() -> Pts:
    out: defaultdict[str, Decimal] = defaultdict(Decimal)
    for path in glob_cache("hyena_", "_rewards.pkl"):
        for r in load_pkl(path):
            out[r["period"]] += Decimal(str(r["enaxPoints"]))
    return dict(out)


def onyx_pts() -> Pts:
    return _pts_by_period(
        "onyx_", "_points.pkl", "start_window", ["points"], OnyxClient.to_week_label
    )


# MARK: ISO-week extractors (vol + burn + pts)

ISOData = dict[str, tuple[Decimal, Decimal, Decimal]]  # {iso_week: (vol, burn, pts)}


def _isopts(prefix: str, dt_key: str, *pts_keys: str) -> dict[str, Decimal]:
    return _pts_by_period(prefix, "_points.pkl", dt_key, list(pts_keys), _to_iso_week)


def _isoout() -> defaultdict[str, list[Decimal]]:
    return defaultdict(lambda: [Decimal(0), Decimal(0), Decimal(0)])


def ethereal_burn_weeks() -> ISOData:
    out = _isoout()
    for path in glob_cache("ethereal_", "_trades.pkl"):
        for r in load_pkl(path):
            w = _to_iso_week(parse_dt(r["created_at"]))
            out[w][0] += Decimal(str(r["total_inc"])) + Decimal(str(r["total_dec"]))
            out[w][1] -= (
                Decimal(str(r["realized_pnl"]))
                - Decimal(str(r["fees_usd"]))
                - Decimal(str(r["funding_usd"]))
            )
    for w, pts in _isopts("ethereal_", "started_at", "points", "referral_points").items():
        out[w][2] += pts
    return {k: (v[0], v[1], v[2]) for k, v in out.items()}


def hyena_burn_weeks() -> ISOData:
    out = _isoout()
    for path in glob_cache("hyena_", "_fills.pkl"):
        for r in load_pkl(path):
            if not r.get("coin", "").startswith("hyna:"):
                continue
            dt = datetime.fromtimestamp(r["time"] / 1000, tz=UTC)
            w = _to_iso_week(dt)
            out[w][0] += Decimal(str(r["px"])) * Decimal(str(r["sz"]))
            out[w][1] -= Decimal(str(r.get("closedPnl", 0)))
    for path in glob_cache("hyena_", "_rewards.pkl"):
        for r in load_pkl(path):
            dt = (
                parse_dt(r["start_window"])
                if "start_window" in r
                else HyenaHistoryItem(**r).start_window
            )
            out[_to_iso_week(dt)][2] += Decimal(str(r.get("enaxPoints", 0)))
    return {k: (v[0], v[1], v[2]) for k, v in out.items()}


def nado_burn_weeks() -> ISOData:
    out = _isoout()
    for path in glob_cache("nado_", "_trades.pkl"):
        for r in load_pkl(path):
            w = _to_iso_week(parse_dt(r["created_at"]))
            out[w][0] += Decimal(str(r["amount"])) * Decimal(str(r["price"]))
            out[w][1] -= Decimal(str(r["realized_pnl"])) - Decimal(str(r["fee"]))
    for w, pts in _isopts("nado_", "since", "points").items():
        out[w][2] += pts
    return {k: (v[0], v[1], v[2]) for k, v in out.items()}


def omni_burn_weeks() -> ISOData:
    out = _isoout()
    for path in glob_cache("omni_", "_trades.pkl"):
        for r in load_pkl(path):
            if r.get("status") != "confirmed":
                continue
            w = _to_iso_week(parse_dt(r["created_at"]))
            out[w][0] += Decimal(str(r["price"])) * Decimal(str(r["qty"]))
    for path in glob_cache("omni_", "_transfers.pkl"):
        for r in load_pkl(path):
            if r.get("status") != "confirmed":
                continue
            if r.get("transfer_type") not in ("funding", "realized_pnl"):
                continue
            w = _to_iso_week(parse_dt(r["created_at"]))
            out[w][1] -= Decimal(str(r["qty"]))
    for w, pts in _isopts("omni_", "start_window", "total_points").items():
        out[w][2] += pts
    return {k: (v[0], v[1], v[2]) for k, v in out.items()}


def onyx_burn_weeks() -> ISOData:
    out = _isoout()
    for path in glob_cache("onyx_", "_fills.pkl"):
        for r in load_pkl(path):
            dt = datetime.fromtimestamp(r["time"] / 1000, tz=UTC)
            w = _to_iso_week(dt)
            out[w][0] += Decimal(str(r["px"])) * Decimal(str(r["sz"]))
            out[w][1] -= Decimal(str(r.get("closedPnl", 0))) - Decimal(str(r.get("fee", 0)))
    for w, pts in _isopts("onyx_", "start_window", "points").items():
        out[w][2] += pts
    return {k: (v[0], v[1], v[2]) for k, v in out.items()}


def pacifica_burn_weeks() -> ISOData:
    out = _isoout()
    for path in glob_cache("pacifica_", "_trades.pkl"):
        for r in load_pkl(path):
            w = _to_iso_week(parse_dt(r["created_at"]))
            out[w][0] += Decimal(str(r["amount"])) * Decimal(str(r["price"]))
            out[w][1] -= Decimal(str(r["pnl"]))
    for w, pts in _isopts("pacifica_", "start_window", "total_points").items():
        out[w][2] += pts
    return {k: (v[0], v[1], v[2]) for k, v in out.items()}


def zero1_burn_weeks() -> ISOData:
    out = _isoout()
    seen: set[str] = set()
    for path in glob_cache("zero1_", "_trades_maker.pkl") + glob_cache(
        "zero1_", "_trades_taker.pkl"
    ):
        for r in load_pkl(path):
            tid = str(r.get("tradeId", r.get("uid", "")))
            if tid in seen:
                continue
            seen.add(tid)
            w = _to_iso_week(parse_dt(r["time"]))
            out[w][0] += Decimal(str(r["price"])) * Decimal(str(r["baseSize"]))
            out[w][1] += Decimal(str(r.get("fee", 0)))
    for path in glob_cache("zero1_", "_history_pnl.pkl"):
        for r in load_pkl(path):
            w = _to_iso_week(parse_dt(r["time"]))
            out[w][1] -= Decimal(str(r["tradingPnl"]))
    for path in glob_cache("zero1_", "_history_funding.pkl"):
        for r in load_pkl(path):
            w = _to_iso_week(parse_dt(r["time"]))
            out[w][1] -= Decimal(str(r["fundingPnl"]))
    for w, pts in _isopts("zero1_", "start_window", "points").items():
        out[w][2] += pts
    return {k: (v[0], v[1], v[2]) for k, v in out.items()}


# MARK: Exchange registry

# current_period_fn=None → use pts > 0 as "completed" signal
# current_period_fn=callable → no pts, filter out current/incomplete period
EXCHANGES: list[tuple[str, Any, Any, Any | None]] = [
    ("Ethereal", ethereal_stats, ethereal_pts, None),
    ("Hyena", hyena_stats, hyena_pts, HyenaClient.to_week_label),
    ("Nado", nado_stats, nado_pts, None),
    ("Omni", omni_stats, omni_pts, None),
    ("Onyx", onyx_stats, onyx_pts, OnyxClient.to_week_label),
    ("Pacifica", pacifica_stats, pacifica_pts, None),
    ("Zero1", zero1_stats, zero1_pts, None),
]

BURN_EXCHANGES = [
    ("Ethereal", ethereal_burn_weeks),
    ("Hyena", hyena_burn_weeks),
    ("Nado", nado_burn_weeks),
    ("Omni", omni_burn_weeks),
    ("Onyx", onyx_burn_weeks),
    ("Pacifica", pacifica_burn_weeks),
    ("Zero1", zero1_burn_weeks),
]


def _iso_label(iso_week: str) -> str:
    year, week = int(iso_week[:4]), int(iso_week[6:])
    jan4 = datetime(year, 1, 4, tzinfo=UTC)
    monday = jan4 - timedelta(days=jan4.weekday()) + timedelta(weeks=week - 1)
    sunday = monday + timedelta(days=6)
    return f"W{week:02d} {monday.strftime('%b%d')}-{sunday.strftime('%b%d')}"


def _parse_week_arg(s: str) -> str:
    s = s.strip()
    if re.match(r"^\d{4}-W\d{1,2}$", s):
        year, w = s.split("-W")
        return f"{year}-W{int(w):02d}"
    if re.match(r"^W\d{1,2}$", s, re.IGNORECASE):
        return f"{datetime.now(UTC).year}-W{int(s[1:]):02d}"
    raise argparse.ArgumentTypeError(f"Invalid week: {s!r}. Use W14 or 2026-W14.")


def _parse_period_arg(s: str) -> int | str:
    if re.match(r"^-?\d+$", s):
        return int(s)
    return _parse_week_arg(s)


def _offset_week(week_arg: int) -> str | None:
    weeks = sorted({w for _, fn in BURN_EXCHANGES for w in fn()})
    if not weeks:
        return None
    idx = len(weeks) - 1 + week_arg
    return weeks[idx] if idx >= 0 else None


# MARK: Views


def _select_label(labels: list[str], week_arg: int) -> str | None:
    if not labels:
        return None
    sorted_lbls = sorted(labels)
    idx = len(sorted_lbls) - 1 + week_arg  # 0→last, -1→second-to-last
    return sorted_lbls[idx] if idx >= 0 else None


def _available_labels(
    periods: Stats, pts_map: Pts, current_period_fn: Any | None, now: datetime
) -> list[str]:
    if pts_map:
        with_pts = {k: v for k, v in pts_map.items() if v > 0}
        return sorted(with_pts) if with_pts else sorted(pts_map)
    if periods and current_period_fn:
        current = current_period_fn(now)
        completed = [k for k in periods if not k.startswith("OFF ") and k < current]
        return sorted(completed) if completed else sorted(periods)
    return sorted(periods)


def _selected_weeks(
    week_arg: int | None, from_week: str | None, to_week: str | None
) -> tuple[str | None, str | None]:
    if from_week or to_week:
        return from_week, to_week
    if week_arg is None:
        return None, None
    week = _offset_week(week_arg)
    return week, week


def _report_columns(*, grouped: bool) -> list[Column]:
    return [
        Column("Period", justify="left") if grouped else Column("Exchange", justify="left"),
        Column("Volume", "{:,.0f}", total=sum),
        Column("Burn", "{:,.2f}", total=sum),
        Column("Points", "{:,.1f}", total=sum, grand_total=False),
        Column(
            "$/pt",
            "{:,.4f}",
            compute=lambda r: r["Burn"] / r["Points"],
            guard=lambda r: r["Points"] > 0,
            grand_total=False,
        ),
    ]


def report_view(
    week_arg: int | None = None,
    from_week: str | None = None,
    to_week: str | None = None,
    *,
    detail: bool = False,
) -> int:
    """Project report over selected ISO-week range."""
    from_week, to_week = _selected_weeks(week_arg, from_week, to_week)
    tbl = AutoTable(
        *_report_columns(grouped=detail),
        gtitle="Exchange",
    )
    any_data = False

    for name, fn in BURN_EXCHANGES:
        data = fn()
        weeks = sorted(
            w
            for w in data
            if (not from_week or w >= from_week)
            and (not to_week or w <= to_week)
            and any(v != 0 for v in data[w])
        )
        if not weeks:
            continue

        if detail:
            tbl.subgroup(name)
            for w in weeks:
                tbl.add_row(_iso_label(w), *data[w])
        else:
            vol = sum((data[w][0] for w in weeks), Decimal(0))
            burn = sum((data[w][1] for w in weeks), Decimal(0))
            pts = sum((data[w][2] for w in weeks), Decimal(0))
            tbl.add_row(name, vol, burn, pts)
        any_data = True

    if not any_data:
        print("No cached data found.", file=sys.stderr)
        return 1
    tbl.print()
    return 0


def exchange_view(name: str) -> int:
    """One exchange, all available periods."""
    now = datetime.now(UTC)
    match = next((e for e in EXCHANGES if e[0].lower() == name.lower()), None)
    if match is None:
        names = ", ".join(e[0] for e in EXCHANGES)
        print(f"Unknown exchange {name!r}. Available: {names}", file=sys.stderr)
        return 1
    exch_name, stats_fn, pts_fn, current_period_fn = match
    periods = stats_fn()
    pts_map = pts_fn()
    if not periods and not pts_map:
        print("No cached data found.", file=sys.stderr)
        return 1
    available = _available_labels(periods, pts_map, current_period_fn, now)
    tbl = AutoTable(
        Column("Period", justify="left"),
        Column("Volume", "{:,.0f}", total=sum),
        Column("Burn", "{:,.2f}", total=sum),
        Column("Points", "{:,.1f}", total=sum),
        Column(
            "$/pt",
            "{:,.4f}",
            compute=lambda r: r["Burn"] / r["Points"],
            guard=lambda r: r["Points"] > 0,
            grand_total=False,
        ),
    )
    any_data = False
    for lbl in available:
        vol, burn = periods.get(lbl, (Decimal(0), Decimal(0)))
        pts = pts_map.get(lbl, Decimal(0))
        if not vol and not burn and not pts:
            continue
        tbl.add_row(lbl, vol, burn, pts)
        any_data = True
    if not any_data:
        print("No cached data found.", file=sys.stderr)
        return 1
    print(f"{exch_name} — all periods")
    tbl.print()
    return 0


def burn_view() -> int:
    """Burn pivot: rows=ISO weeks, cols=exchanges."""
    data = {name: fn() for name, fn in BURN_EXCHANGES}
    all_weeks = sorted({w for d in data.values() for w in d})
    if not all_weeks:
        print("No cached data found.", file=sys.stderr)
        return 1
    active = [name for name, _ in BURN_EXCHANGES if any(v[1] != 0 for v in data[name].values())]
    tbl = AutoTable(
        Column("Week", justify="left"),
        *[Column(name, "{:,.2f}", total=sum) for name in active],
        Column("Total", "{:,.2f}", compute=lambda r: sum(r[n] for n in active)),
    )
    for week in all_weeks:
        tbl.add_row(
            week,
            *[data[name].get(week, (Decimal(0), Decimal(0), Decimal(0)))[1] for name in active],
        )
    tbl.print()
    return 0


# MARK: Main


def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly trading report")
    parser.add_argument(
        "week",
        nargs="?",
        type=_parse_period_arg,
        default=None,
        help="0=latest; -N=N weeks back; W14/2026-W14=specific ISO week; omit=all time",
    )
    parser.add_argument(
        "-e", "--exchange", metavar="NAME", help="show all periods for one exchange"
    )
    parser.add_argument(
        "-P",
        "--detail",
        "--projects",
        action="store_true",
        help="show weekly detail inside selected range",
    )
    parser.add_argument(
        "--from",
        dest="from_week",
        metavar="WEEK",
        type=_parse_week_arg,
        help="from ISO week, e.g. W14 or 2026-W14",
    )
    parser.add_argument(
        "--to",
        dest="to_week",
        metavar="WEEK",
        type=_parse_week_arg,
        help="to ISO week, e.g. W21",
    )
    parser.add_argument("--burn", action="store_true", help="burn pivot: all exchanges × ISO weeks")
    args = parser.parse_args()

    if args.burn:
        rc = burn_view()
    elif args.exchange:
        rc = exchange_view(args.exchange)
    else:
        week_arg = args.week if isinstance(args.week, int) else None
        from_week = args.from_week or (args.week if isinstance(args.week, str) else None)
        to_week = args.to_week or (args.week if isinstance(args.week, str) else None)
        rc = report_view(week_arg, from_week, to_week, detail=args.detail)

    if rc == 0:
        print("\033[2m  · cached data — run stats <exchange> to refresh\033[0m")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
