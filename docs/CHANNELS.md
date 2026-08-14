# Telegram Push-Channel Inventory (canonical)

Single source of truth: **`app/core/channels_registry.py`**.

This doc mirrors the registry module docstring. If you change a channel
mapping, change the registry — **not** this table and **not** a consumer.

- The bot (`app/domains/telegram/rugmunchbot/config.py`) and the backend
  alert transport (`app/domains/alerts/telegram_transport.py`) both import
  from the registry.
- Resolution order is always **gopass → env → fallback**:
  1. `gopass show <gopass_path>` (skipped when gopass is unavailable, e.g.
     inside the backend container);
  2. `os.environ[<env_var>]` (canonical, then legacy aliases);
  3. documented public fallback (bot only; the alert transport fails open
     and skips unconfigured channels).

> **No secrets here.** Public handles only — numeric chat ids are never
> listed in this file or the registry docstring. The code keeps documented
> *public* channel-id fallbacks for resilience, but they are not credentials
> and are not documented here by policy.

## Inventory

| Role | Public handle | Gopass entry | Env var (canonical) | Legacy env alias | Consumers |
|------|---------------|--------------|---------------------|------------------|-----------|
| news | — (unverified) | `rmi/telegram/CHANNEL_RMI_NEWS` | `RMI_NEWS_CHANNEL_ID` | `RMI_CRYPTONEWS_CHANNEL_ID` | bot channel jobs (`news`), alert transport `rmicryptonews` |
| alerts | `@RugMunchAlerts` | `rmi/telegram/CHANNEL_CRYPTO_ALERTS` | `RMI_ALERTS_CHANNEL_ID` | — | bot channel jobs (`alerts`), alert transport `rmialerts` + `alerts_channel` (handle surface, env `RMI_ALERTS_CHANNEL`) |
| alpha | `@rmialpha` | `rmi/telegram/CHANNEL_CRYPTO_ALPHA` | `RMI_ALPHA_CHANNEL_ID` | — | bot channel jobs (`alpha`), alert transport `rmialpha` |
| scans | — (unverified) | `rmi/telegram/CHANNEL_COMMUNITY_SCANS` | `RMI_SCANS_CHANNEL_ID` | — | bot channel jobs (`scans`) — **no backend surface yet** |
| main | — (unverified) | `rmi/telegram/CHANNEL_CRYPTO_RUGMUNCHER` | `RMI_MAIN_CHANNEL_ID` | — | bot `main` notifications, alert transport `main_channel` |
| updates | — (unverified) | `rmi/telegram/CHANNEL_RMI_UPDATES` | `RMI_UPDATES_CHANNEL_ID` | — | mapped in registry; **no consumer yet** (no scheduled job, no transport surface) |

## Notes / drift

- **news env-var drift (reconciled).** The backend alert transport used the
  legacy name `RMI_CRYPTONEWS_CHANNEL_ID`; the bot used `RMI_NEWS_CHANNEL_ID`.
  Canonical is now `RMI_NEWS_CHANNEL_ID`; the legacy name is still read as an
  alias so existing deployments keep working. Migrate deployments to the
  canonical name.
- **scans — one-sided.** Configured for the bot (gopass + env + fallback) but
  the backend alert publisher has no `scans` surface. Add a
  `ChannelPolicy`/transport binding in `app/domains/alerts/` when scan
  digests should also push through the backend.
- **updates — mapped, unused.** `rmi/telegram/CHANNEL_RMI_UPDATES` exists in
  gopass and is now registered (env `RMI_UPDATES_CHANNEL_ID`), but neither the
  bot scheduler nor the alert transport consumes it yet.
- **Public handles for news/scans/main/updates are unverified.** `alerts`
  (`@RugMunchAlerts`) and `alpha` (`@rmialpha`) are verified in code/tests;
  the rest are marked unverified until a human confirms the handle.
