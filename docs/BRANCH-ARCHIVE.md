# Branch archive — 2026-10-24

Branch cleanup pass (operator-authorized). Every deleted branch kept its tip as an
archive tag: `archive/<branch>-20261024`. Recover a branch with

```bash
git fetch forgejo --tags
git branch <name> archive/<branch>-20261024
```

## Branches deleted in this pass

| branch | tip | verdict | reason |
|---|---|---|---|
| `wip/smart-money2` | `67d43dc1` | archived, duplicate | Commit message says it: `wip(smart-money): park duplicate surface work`. 1 commit / 26 behind. Parked surface (7 TSX) superseded by the smart-money feature merged in **rmi-frontend** `main` (`src/features/smart-money/*`); `SmartMoneyTabs.tsx` hashes identical. No backend code unique to this tip. |
| `wip/smart-money4` | `b17697c2` | archived, **partially recovered** | 3 commits / 24 behind. NOT fully contained in main: `app/domains/smart_money/sources.py` label joins are case-sensitive in main, `_CLUSTERS` selects `address` instead of `wallet_address` (broken cluster join), and known-token metadata (OP-stack WETH symbol/decimals fallback) is absent. Recovered via `fix/smart-money-recovered-20261024`. |
| `merge/smart-money` | `dccd3e00` | deleted, already in main | Fully contained in main; the actual landing branch for the smart-money work. |
| `11 further branches` | `see tags` | deleted, already in main | ghost-live, poly-attribution, x-webshare, x-audit, poly-opportunities, ref-analytics, backend-debt2/3, backend-errors, trending-api3, wip/smart-money3 — all verified contained; tags retain tips. |

## Restore

Nothing was force-pushed and `main` was not rewritten. Tags are the audit trail;
a tag can be dropped once its branch is confirmed unnecessary.

## Audit addendum (2026-10-24, tag audit)

Tags `archive/wip/smart-money2-20261024` and `archive/wip/smart-money4-20261024`
were diff-tip audited against `main`:

- **smart-money2** — parked frontend surface exists in `rmi-frontend` main
  (`src/features/smart-money/`); no unique backend code. Do not restore.
- **smart-money4** — the "case-safe labels, cluster join, known-token metadata"
  fix was NOT contained in `merge/smart-money`. Unique hunks (lower() joins,
  `wallet_address` key fix, `known_tokens.py` + known CTE in `_EVM_FLOWS`,
  symbol/price data-quality reasons) ported onto `main` in
  `fix/smart-money-recovered-20261024`.
