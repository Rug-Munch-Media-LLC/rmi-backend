# Branch triage — 2026-10-25

Scope: branches with commit date >= 2026-10-23. Excluded `recovery/*`, `archive/*`,
`backup/*` and branches owned by a running lane (`debt-backend10`, `debt-fe7`,
`mod-scanner`, `pry-fix11`, `fe-design13`, `pryweb-design13`).
Base: `forgejo/main` @ 282bf55c0.

## Method
`git rev-list --left-right --count forgejo/main...<branch>` (behind/ahead) plus
`git cherry forgejo/main <branch>` (`+` = not upstream, `-` = patch already upstream).

## Outcome

| Branch | Ahead/behind | Verdict | Evidence |
|---|---|---|---|
| debt-backend6 | 1 / 17 | **UNMERGED — verified GREEN** | ruff clean on 5 touched files; `tests/test_health_latency_policy.py` 5 passed; 1 conflict (`app/core/health_stores.py`) resolved to branch side (`STORE_TIMEOUT_S` + `classify_latency`) |
| land/branch-disposition-20261025 | 1 / 18 | UNMERGED — docs only | 1 file, 67 lines, `docs/BRANCH-DISPOSITION-20261025.md` |
| backend-errors | 1 / 50 | Superseded | fix already in main as 2ae4b67c6 ("thread-safe ClickHouse clients, drain cancelled thread work, pause Bitquery 402") |
| backend-debt5 | 4 / 24 | Superseded | all 4 commits `git cherry` = `-` (patch-equivalent via land/backend-debt5) |
| assistant-gateway | 0 / 57 | Merged (ancestor) | ahead = 0 |
| assistant-gateway-lint | 0 / 56 | Merged | ahead = 0 |
| backend-debt3-finish | 0 / 48 | Merged | ahead = 0 |
| backend-debt4 | 0 / 31 | Merged | ahead = 0 |
| be-archive | 0 / 63 | Merged | ahead = 0 |
| boost-system | 0 / 61 | Merged | ahead = 0 |
| chart-data | 0 / 57 | Merged | ahead = 0 |
| debt-backend6-v2 | 0 / 12 | Merged | ahead = 0 |
| debt-backend8 | 0 / 3 | Merged | ahead = 0 |
| engagement-api | 0 / 9 | Merged | ahead = 0 |
| fix/smart-money-recovered-20261024 | 0 / 65 | Merged | ahead = 0 |
| free-chat-finish | 0 / 31 | Merged | ahead = 0 |
| free-chat-pool | 0 / 49 | Merged | ahead = 0 |
| free-pool-fix | 0 / 33 | Merged | ahead = 0 |
| freemium-api | 0 / 9 | Merged | ahead = 0 |
| gmgn-intel | 0 / 57 | Merged | ahead = 0 |
| land/backend-debt5 | 0 / 17 | Merged | ahead = 0 |
| land/backend-errors | 0 / 17 | Merged | ahead = 0 |
| land/free-chat-finish | 0 / 17 | Merged | ahead = 0 |
| live-trades-api | 0 / 57 | Merged | ahead = 0 |
| page-context-finish | 0 / 56 | Merged | ahead = 0 |
| pyrevm-revert | 0 / 44 | Merged | ahead = 0 |
| rugmaps-clusters | 0 / 57 | Merged | ahead = 0 |
| token-icons | 0 / 61 | Merged | ahead = 0 |
| trending-algo | 0 / 61 | Merged | ahead = 0 |

Backup refs: `refs/backup/triage-20261025/<branch>` for 26 branches (merged +
superseded). Deletion of the remote branch is **blocked** — `git push --delete` is
refused by the agent GitGuard; an operator must run it. Nothing is lost: the branches
still exist on `forgejo` and every tip is pinned in the backup refs.

## Leftover
`debt-backend6` and `land/branch-disposition-20261025` still need landing.
