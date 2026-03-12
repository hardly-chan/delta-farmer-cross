# Changelog

## v0.4.0 — 2026-03-12

### New

- **Multi-symbol basket mode** — one trade cycle can now cover 2–4 symbols simultaneously. Each symbol stays delta-neutral, and each account also nets out across the full basket. Configure with `symbols_per_trade = 2` (or 3, 4).
- **Telegram notifications** — get push alerts when a trade opens/closes, on errors and crashes, and periodic digests with volume and burn stats. Add a `[telegram]` section to your config to enable.
- **Combined basket ROI limit** — new `combined_roi_limit` safety check closes the full basket if total P&L across all positions exceeds the threshold, in addition to the existing per-position check.

### Fixes

- Ethereal: fixed points count and authentication on the points endpoint
- Nado: fixed gap between live and archive trade data
- Pacifica: fixed balance display in the `info` command
- Limit order polling reliability improved across all exchanges

---

## v0.3.0 — 2026-03-04

### New

- **Ethereal support** — full trading, stats, and points tracking for Ethereal (EVM).
- **Nado support** — full trading, stats, and points tracking for Nado (EVM).
- **Grouped trading mode** — split accounts into independent strategy groups running in parallel within one process. Configure with `group_size` and optionally `regroup_interval` to periodically reshuffle groups.

---

## v0.2.0 — 2026-02-21

### New

- **Omni support** — full trading and stats for Omni (EVM, by Variational).
- **Stats filtering by day** — `stats -g day` groups results by day instead of week.
- Pacifica genesis date corrected to match the official UI and Discord.

---

## v0.1.0 — 2026-02-14

Initial release with **Pacifica** (Solana) support — delta-neutral trading, multi-account management, encrypted key storage, limit/market order modes, and weekly stats.
