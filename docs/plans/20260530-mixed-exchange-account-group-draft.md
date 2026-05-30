# Mixed-Exchange Account Group Draft

## Overview
- Draft plan for a future refactor that makes mixed-exchange account handling explicit.
- The current code has several places where a group of accounts is treated as if one exchange/client can represent all symbols and prices.
- The goal is to introduce an account-group abstraction for reusable operations across `TradingClient` collections, then use it to fix low-risk mixed-exchange assumptions.

**Acceptance criteria**:
- Shared account-group logic replaces ad hoc "unique exchange x symbol" loops.
- Startup symbol validation still checks each configured symbol on each unique exchange and reports human-readable errors.
- Tradeable-symbol filtering still keeps only symbols that pass on every unique exchange.
- `positions` reporting prices positions through the account/exchange that owns each position, not `accs[0]`.
- `DeltaTrade.gate` checks entry quality for every relevant client/exchange instead of only the lead client.
- `DeltaTrade.load_qtys` mixed-exchange sizing remains explicitly deferred with a clear TODO.

## Context (from discovery)
- `strategy/symbols.py` currently contains helper functions for unique-exchange symbol checks.
- `strategy/runner.py::_check_symbols` uses the helper to fail fast on invalid configured symbols before trading starts.
- `strategy/cycle.py::_tradeable_symbols` uses the helper to filter symbols based on exchange tradeability windows.
- `strategy/runner.py::print_positions` currently uses `accs[0].get_price(symbol)` for all open positions.
- `strategy/trade.py::DeltaTrade.gate` currently calls `wait_for_entry_quality` only on `self.lead.client`.
- `strategy/trade.py::DeltaTrade.load_qtys` currently uses lead price and lead lot size for all legs; this touches sizing and should be handled later.

## Development Approach
- **testing approach**: Regular, code first with focused tests in the same task.
- Complete one task fully before moving to the next.
- Keep the abstraction small and project-local; avoid introducing a broad framework.
- Every code-changing task must include new or updated tests.
- Run targeted tests after each task and the full suite before completion.
- Update this plan if implementation scope changes.

## Testing Strategy
- **unit tests**:
  - account group deduplicates accounts by `exchange` while preserving first-seen order.
  - symbol validation reports exchange-level errors without account names or raw exception types.
  - symbol filtering keeps only symbols that return `True` on every unique exchange.
  - `print_positions` fetches mark prices from each position's own account.
  - `DeltaTrade.gate` invokes entry-quality checks for every relevant client group and fails if any group fails.
- **regression tests**:
  - existing strategy lifecycle tests remain green.
  - existing tradeable-symbol timing tests remain green.
- **commands**:
  - `uv run pytest tests/test_strategy.py`
  - `uv run pytest`
  - `uv run ruff check`
  - `uv run pyright`

## Progress Tracking
- mark completed items with `[x]` immediately when done
- add newly discovered tasks with a `+` prefix
- document issues/blockers with a `!` prefix
- keep this plan in sync with actual implementation decisions

## Solution Overview
- Replace the narrow `strategy/symbols.py` concept with a more general account-group abstraction.
- The abstraction should own operations over a `Sequence[TradingClient]`: lookup by name, first client per exchange, exchange-symbol predicates, and common filtering/ensure behavior.
- Use the abstraction in startup symbol validation and tradeable-symbol filtering.
- Fix reporting and gate behavior where the existing code incorrectly assumes the first or lead client can represent every exchange.
- Defer mixed-exchange quantity sizing because it affects trading risk and delta balancing.

## Technical Details
- Candidate module name: `strategy/account_group.py`.
- Candidate class name: `AccountGroup`.
- Expected shape:

```python
class AccountGroup:
    def __init__(self, accounts: Sequence[TradingClient]): ...

    @property
    def by_name(self) -> dict[str, TradingClient]: ...

    @property
    def exchange_clients(self) -> list[TradingClient]: ...

    async def ensure_exchange_symbols(
        self,
        symbols: Sequence[str],
        predicate: Callable[[TradingClient, str], Awaitable[bool]],
    ) -> None: ...

    async def filter_exchange_symbols(
        self,
        symbols: Sequence[str],
        predicate: Callable[[TradingClient, str], Awaitable[bool]],
    ) -> list[str]: ...
```

- `ensure_exchange_symbols` should convert ordinary predicate failures and ordinary exceptions into one `AppError`:

```text
Invalid configured symbols: ET is not available on pacifica
```

- Control-flow exceptions such as `asyncio.CancelledError`, `KeyboardInterrupt`, and `SystemExit` should continue to propagate.
- `filter_exchange_symbols` should treat `False` as normal business filtering and ordinary exceptions as actual failures to raise.

## What Goes Where
- **Implementation Steps**: code changes, tests, and local verification in this repository.
- **Post-Completion**: future trading-risk review for `DeltaTrade.load_qtys`.

## Implementation Steps

### Task 1: Replace symbol helper with account group abstraction

**Files:**
- Create: `strategy/account_group.py`
- Modify: `strategy/runner.py`
- Modify: `strategy/cycle.py`
- Modify: `tests/test_strategy.py`
- Delete: `strategy/symbols.py` if fully replaced

- [ ] create `AccountGroup` with account lookup and unique exchange-client behavior
- [ ] move `ensure_exchange_symbols` and `filter_exchange_symbols` behavior into `AccountGroup`
- [ ] update `_check_symbols` to use `AccountGroup(accs).ensure_exchange_symbols(...)`
- [ ] update `_tradeable_symbols` to use `AccountGroup(self.accounts).filter_exchange_symbols(...)`
- [ ] update tests for the new class API and preserve current invalid-symbol behavior
- [ ] run `uv run pytest tests/test_strategy.py`

### Task 2: Fix position reporting price source

**Files:**
- Modify: `strategy/runner.py`
- Modify: `tests/test_strategy.py`

- [ ] replace `accs[0].get_price(symbol)` in `print_positions` with account-specific price lookup
- [ ] key mark prices by `(account_name, symbol)` instead of only `symbol`
- [ ] fall back to entry price only for the specific account/symbol whose price lookup failed
- [ ] add a mixed-exchange test where the first account cannot price the second account's symbol
- [ ] run `uv run pytest tests/test_strategy.py`

### Task 3: Check entry gate per relevant client

**Files:**
- Modify: `strategy/trade.py`
- Modify: `tests/test_strategy.py`

- [ ] group trade legs by `leg.client`
- [ ] call `wait_for_entry_quality(client, symbol, grouped_legs, cfg)` for each client group
- [ ] return `True` only when every client group passes
- [ ] add a test that gate checks both lead and rest clients
- [ ] add a test that one failing client group blocks the whole trade
- [ ] run `uv run pytest tests/test_strategy.py`

### Task 4: Document deferred mixed-exchange sizing

**Files:**
- Modify: `strategy/trade.py`
- Modify: `docs/plans/20260530-mixed-exchange-account-group-draft.md`

- [ ] add a concise TODO near `DeltaTrade.load_qtys` explaining that it still uses lead price/lot size
- [ ] note that the future fix must compute qty per leg and revisit delta adjustment across different lot sizes
- [ ] avoid changing sizing behavior in this plan
- [ ] run `uv run pytest tests/test_strategy.py`

### Task 5: Verify acceptance criteria

- [ ] verify startup symbol validation still fails before trading starts
- [ ] verify tradeable symbol filtering still handles market-hours checks
- [ ] verify `positions` reporting no longer depends on the first account for all prices
- [ ] verify gate checks every relevant client group
- [ ] run `uv run pytest`
- [ ] run `uv run ruff check`
- [ ] run `uv run pyright`

### Task 6: Final documentation

- [ ] update this plan if actual implementation differs
- [ ] move this plan to `docs/plans/completed/` when done

## Post-Completion
*Items requiring separate future work - no checkboxes, informational only*

**Future sizing review**:
- `DeltaTrade.load_qtys` should eventually compute price and lot size per leg for true mixed-exchange baskets.
- That change should be handled separately because it affects order quantities, delta adjustment, and trading risk.
- The future design should decide whether delta balancing is done in base qty, USD notional, or exchange-specific rounded qty.
