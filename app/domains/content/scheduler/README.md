# RMI Daily Briefing — systemd timer install notes

Runs the daily content/alerts pipeline (`news` → `alerts` → `shill` →
`briefing`) at **05:30 UTC** (methodology §2 — well before US market open).
The pipeline is fail-open: a broken step is recorded in the JSON summary, it
never aborts the run.

## Files

| File | Purpose |
|------|---------|
| `rmi-daily-briefing.service` | One-shot run of `python -m app.domains.content.daily_pipeline --run` (dry-run by default) |
| `rmi-daily-briefing.timer` | Fires the service daily at 05:30 UTC, `Persistent=true` |

## Install

```sh
# 1. Copy the units (paths below assume Talos /srv/work/repos/rmi-backend).
#    If the repo lives elsewhere, edit the ExecStart/WorkingDirectory in the
#    .service file first — and change User=/Group= to the deploy user.
sudo cp rmi-daily-briefing.service rmi-daily-briefing.timer /etc/systemd/system/

# 2. Reload + enable + start
sudo systemctl daemon-reload
sudo systemctl enable --now rmi-daily-briefing.timer
sudo systemctl list-timers rmi-daily-briefing.timer   # verify next run

# 3. Run once immediately (dry-run) to smoke-test:
sudo systemctl start rmi-daily-briefing.service
sudo journalctl -u rmi-daily-briefing.service -n 200   # JSON summary printed
```

## Owner-veto workflow (§2)

The unit runs **dry-run by default** (`--run` without `--no-dry-run`):
real data is gathered and the briefing is drafted + fact-checked, but nothing
is published or sent. Inspect the JSON output (`briefing_draft.draft` +
`briefing_draft.review`), then flip the commented `ExecStart` line to
`--no-dry-run` for the live push. A step that fails makes the service exit
non-zero (visible in `systemctl status` / journal), but the remaining steps
still run.

## Cron alternative

If you prefer cron over systemd timers:

```cron
# 05:30 UTC — dry-run (owner veto); switch to --no-dry-run after approval
0 5 * * * cd /srv/work/repos/rmi-backend && .venv/bin/python -m app.domains.content.daily_pipeline --run >> /var/log/rmi-daily-briefing.log 2>&1
```

## Notes / prerequisites

- `app.content_platform.create_post` (briefing publish) needs the Ghost admin
  session cookie readable by the service user (see `GHOST_SESSION_COOKIE`).
- The `news` step needs a working Telegram session (gopass credentials or a
  stored session). When unavailable the sweep degrades to `[]` (fail-open) and
  the summary records `count: 0` — no alert, but also no crash.
- Step filter for debugging: `--step news|alerts|shill|briefing` (repeatable).
