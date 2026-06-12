# Delta Farmer Control Plane Draft

## Problem

Current `delta-farmer` works as a collection of per-exchange Python entrypoints:

```bash
uv run apps/omni.py trade
uv run apps/omni.py stats
uv run apps/hyena.py reward claim
uv run apps/onyx.py migrate
```

This is workable for the author, but not for simpler users:

- setup requires cloning the repo, installing `uv`, creating TOML config, editing keys, encrypting keys, and remembering per-exchange commands;
- runtime management leaks terminal mechanics (`tmux`, sessions, restarts, manual log hunting);
- accounts and proxies are duplicated across configs instead of living in one inventory;
- strategy code mixes planning, execution, sleeps, safety checks, reporting, and cleanup, so new strategy experiments feel risky.

The replacement should first make the product controllable before rewriting the trading engine.

## Current Inventory

Shared commands come from `lib/cli.py`:

- `trade`
- `close`
- `positions`
- `info`
- `proxy`
- `stats`
- `clean`
- `config new/encrypt/decrypt`
- hidden `tgtest`

Exchange-specific extensions:

- Omni: `competition`, `competition --join`
- Hyena: `reward claim`, `migrate`
- Onyx: `migrate`

Common trading surface:

- config model: `strategy.models.StrategyConfig`
- client protocol: `strategy.models.TradingClient`
- group runner: `strategy.runner.run_groups`
- live cycle: `strategy.cycle.DeltaStrategy`
- plan/open/monitor/close logic: `strategy.trade` and `strategy.execution`

The first replacement should preserve these capabilities, not rewrite them.

## Target Shape

Split the product into three layers:

```text
accounts-vault
  universal wallets/proxies inventory
  encrypted private keys
  read-only SQLite views for scripts

delta-farmer control plane
  setup, status, start, stop, logs, exchange tools
  daemon/viewer boundary later

delta-farmer trading engine
  current Python engine first
  Rust strategy/execution state machine later
```

The important product boundary:

```text
accounts/proxies are not trading config
strategies consume selected accounts from the vault
```

## Minimal Replacement

Add one top-level command before changing internals:

```bash
uv run df.py setup
uv run df.py trade omni
uv run df.py info omni
uv run df.py stats omni
uv run df.py close omni
uv run df.py tool omni competition
uv run df.py tool hyena reward claim
```

This replaces the visible `uv run apps/<exchange>.py ...` UX with one stable command.

Initial implementation can be a thin Python control CLI that dispatches to existing app modules.
It should not duplicate exchange logic yet.

Command mapping:

```text
df.py trade <exchange>       -> apps/<exchange>.py trade
df.py close <exchange>       -> apps/<exchange>.py close
df.py positions <exchange>   -> apps/<exchange>.py positions
df.py info <exchange>        -> apps/<exchange>.py info
df.py stats <exchange> ...   -> apps/<exchange>.py stats ...
df.py proxy <exchange>       -> apps/<exchange>.py proxy
df.py tool <exchange> ...    -> exchange-specific passthrough
```

This is not the final architecture. It is a low-risk UX bridge.

## Setup V1

`df.py setup` should make old config creation less manual:

1. choose exchange;
2. ask whether to use existing `accounts-vault`;
3. select wallets by tag or name;
4. write `configs/<exchange>.toml` from selected vault rows;
5. ask strategy preset: `safe`, `balanced`, `aggressive`;
6. write only strategy settings into TOML.

If `accounts-vault` is not available yet, setup can fall back to old config flow.

The generated TOML should eventually stop storing private keys. For the bridge version, it can
materialize encrypted keys only as a compatibility step.

## Runtime V2

After the CLI bridge works, add daemon/viewer:

```text
delta-farmer daemon
  owns running strategies
  keeps unlocked secrets in memory
  writes logs/events
  exposes local socket API

delta-farmer ui
  viewer only
  sends commands and reads events
```

Closing the viewer must not stop trading.

The daemon API should expose:

- snapshot: running strategies, accounts, balances, positions, proxy state;
- commands: start, stop, close all, restart, run exchange tool, reload config;
- events: log line, strategy status, order update, position update, account error.

## Strategy V3

Only after control-plane UX is stable, split trading logic:

```text
planner:
  snapshot + config -> desired actions

executor:
  desired actions -> exchange calls

state machine:
  idle -> plan -> open -> hedge -> monitor -> close -> recover
```

The current live cycle should be treated as one strategy implementation:

```text
classic delta cycle
```

New experiments, like delta-neutral maker/limit strategies, should be separate strategies with
replay tests before touching live execution.

## First Concrete Tasks

1. Create `df.py` as a top-level unified CLI bridge.
2. Add exchange registry:
   - name;
   - app module;
   - default config path;
   - supported extra tools.
3. Make README quickstart use `df.py`.
4. Add `df.py setup` that creates old-style configs from presets.
5. Integrate `accounts-vault` as optional source for accounts/proxies.
6. Only then start daemon/viewer.

## Non-Goals For The First Pass

- no full Rust rewrite;
- no TUI yet;
- no strategy state-machine rewrite yet;
- no migration of all stats/reporting code;
- no new trading behavior.

The first pass should replace command complexity, not trading behavior.
