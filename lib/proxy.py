import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from rich import print as rprint
from rich.box import SIMPLE
from rich.table import Table

from .http import AsyncHttp, parse_proxy
from .models import AccountConfig


def _format_proxy(proxy: str | None) -> str:
    proxy = parse_proxy(proxy)
    if not proxy:
        return "-"

    try:
        parts = urlsplit(proxy)
        host = parts.hostname or "?"
        port = f":{parts.port}" if parts.port else ""
        auth = " auth" if parts.username or parts.password else ""
        return f"{parts.scheme}://{host}{port}{auth}".strip()
    except Exception:
        return proxy


@dataclass
class ProxyCheckResult:
    ip: str
    status: str
    latency_ms: int | None = None
    error: str | None = None


async def _check_proxy(proxy: str | None) -> ProxyCheckResult:
    if not parse_proxy(proxy):
        return ProxyCheckResult(ip="-", status="missing")

    http = AsyncHttp(
        baseurl="https://api.ipify.org", headers={"accept": "application/json"}, proxy=proxy
    )
    started = time.perf_counter()
    try:
        rep = await http.request("GET", "/?format=json")
        ip = str(rep.json().get("ip", "-"))
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ProxyCheckResult(ip=ip, status="ok", latency_ms=latency_ms)
    except Exception as e:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return ProxyCheckResult(ip="-", status="failed", latency_ms=latency_ms, error=str(e))
    finally:
        await http.close()


async def print_proxies(accounts: Sequence[AccountConfig]) -> None:
    uniq: dict[str | None, asyncio.Task[ProxyCheckResult]] = {}
    for acc in accounts:
        key = parse_proxy(acc.proxy)
        if key not in uniq:
            uniq[key] = asyncio.create_task(_check_proxy(acc.proxy))

    results = {key: await task for key, task in uniq.items()}

    tbl = Table(box=SIMPLE)
    tbl.add_column("Account", justify="left")
    tbl.add_column("Proxy", justify="left")
    tbl.add_column("IP", justify="left")
    tbl.add_column("Latency", justify="right")
    tbl.add_column("Status", justify="left")

    for acc in accounts:
        res = results[parse_proxy(acc.proxy)]
        status = {
            "ok": "[green]ok[/green]",
            "missing": "[yellow]missing[/yellow]",
            "failed": "[red]failed[/red]",
        }[res.status]
        latency = f"{res.latency_ms} ms" if res.latency_ms is not None else "-"
        if res.error:
            status = f"{status} ({res.error})"
        tbl.add_row(acc.name, _format_proxy(acc.proxy), res.ip, latency, status)

    rprint(tbl)
