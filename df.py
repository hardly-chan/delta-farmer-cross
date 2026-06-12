#!/usr/bin/env python3
# delta-farmer | https://github.com/vladkens/delta-farmer
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Exchange:
    name: str
    app: str
    tools: tuple[str, ...] = ()


EXCHANGES: dict[str, Exchange] = {
    "ethereal": Exchange("ethereal", "apps/ethereal.py"),
    "hyena": Exchange("hyena", "apps/hyena.py", ("migrate", "reward")),
    "nado": Exchange("nado", "apps/nado.py"),
    "omni": Exchange("omni", "apps/omni.py", ("competition",)),
    "onyx": Exchange("onyx", "apps/onyx.py", ("migrate",)),
    "pacifica": Exchange("pacifica", "apps/pacifica.py"),
    "rise": Exchange("rise", "apps/rise.py"),
    "zero1": Exchange("zero1", "apps/zero1.py"),
}

COMMON_COMMANDS = {
    "trade",
    "close",
    "positions",
    "info",
    "proxy",
    "stats",
    "clean",
}


def _usage() -> str:
    names = ", ".join(sorted(EXCHANGES))
    commands = ", ".join(sorted(COMMON_COMMANDS))
    return f"""delta-farmer control bridge

Usage:
  uv run df.py <command> <exchange> [args...]
  uv run df.py setup <exchange> [args...]
  uv run df.py config <exchange> <new|encrypt|decrypt> [args...]
  uv run df.py tool <exchange> <tool-command> [args...]
  uv run df.py exchanges

Common commands:
  {commands}

Exchanges:
  {names}

Examples:
  uv run df.py setup omni
  uv run df.py trade omni
  uv run df.py stats omni this
  uv run df.py tool omni competition --join
  uv run df.py tool hyena reward claim
"""


def _exchange(name: str) -> Exchange:
    try:
        return EXCHANGES[name]
    except KeyError:
        valid = ", ".join(sorted(EXCHANGES))
        raise SystemExit(f"Unknown exchange: {name}\nValid exchanges: {valid}")


def app_argv(argv: list[str]) -> list[str]:
    if not argv or argv[0] in {"-h", "--help", "help"}:
        raise SystemExit(_usage())

    cmd = argv[0]

    if cmd == "exchanges":
        for ex in EXCHANGES.values():
            tools = f" tools={','.join(ex.tools)}" if ex.tools else ""
            print(f"{ex.name}{tools}")
        raise SystemExit(0)

    if cmd == "setup":
        if len(argv) < 2:
            raise SystemExit("Usage: uv run df.py setup <exchange> [args...]")
        ex = _exchange(argv[1])
        return [str(ROOT / ex.app), "config", "new", *argv[2:]]

    if cmd == "config":
        if len(argv) < 3:
            raise SystemExit(
                "Usage: uv run df.py config <exchange> <new|encrypt|decrypt> [args...]"
            )
        ex = _exchange(argv[1])
        return [str(ROOT / ex.app), "config", argv[2], *argv[3:]]

    if cmd == "tool":
        if len(argv) < 3:
            raise SystemExit("Usage: uv run df.py tool <exchange> <tool-command> [args...]")
        ex = _exchange(argv[1])
        tool = argv[2]
        if ex.tools and tool not in ex.tools:
            valid = ", ".join(ex.tools)
            raise SystemExit(f"Unsupported {ex.name} tool: {tool}\nValid tools: {valid}")
        return [str(ROOT / ex.app), *argv[2:]]

    if cmd in COMMON_COMMANDS:
        if len(argv) < 2:
            raise SystemExit(f"Usage: uv run df.py {cmd} <exchange> [args...]")
        ex = _exchange(argv[1])
        return [str(ROOT / ex.app), cmd, *argv[2:]]

    raise SystemExit(_usage())


def main(argv: list[str] | None = None) -> None:
    app_args = app_argv(list(sys.argv[1:] if argv is None else argv))
    os.execv(sys.executable, [sys.executable, *app_args])


if __name__ == "__main__":
    main()
