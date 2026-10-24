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
| `wip/smart-money2` | `67d43dc1` | archived, duplicate | Commit message says it: `wip(smart-money): park duplicate surface work`. 1 commit / 26 behind. Content already landed via `merge/smart-money`. |
| `wip/smart-money4` | `b17697c2` | archived, superseded | 3 commits / 24 behind. Older smart-money WIP; superseded by `merge/smart-money` (contained in main). |
| `merge/smart-money` | `dccd3e00` | deleted, already in main | Fully contained in main; the actual landing branch for the smart-money work. |
| `11 further branches` | `see tags` | deleted, already in main | ghost-live, poly-attribution, x-webshare, x-audit, poly-opportunities, ref-analytics, backend-debt2/3, backend-errors, trending-api3, wip/smart-money3 — all verified contained; tags retain tips. |

## Restore

Nothing was force-pushed and `main` was not rewritten. Tags are the audit trail;
a tag can be dropped once its branch is confirmed unnecessary.
