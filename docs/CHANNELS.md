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

| Role | Public handle | Channel name | Gopass entry | Env var (canonical) | Legacy env alias | Consumers |
|------|---------------|--------------|--------------|---------------------|------------------|-----------|
| news | `@rmicryptonews` | RMI Crypto News 24/7 | `rmi/telegram/CHANNEL_RMI_NEWS` | `RMI_NEWS_CHANNEL_ID` | `RMI_CRYPTONEWS_CHANNEL_ID` | bot channel jobs (`news`), alert transport `rmicryptonews` |
| alerts | `@rmialerts` | RMI Security — Rug Alerts & Exploits | `rmi/telegram/CHANNEL_CRYPTO_ALERTS` | `RMI_ALERTS_CHANNEL_ID` | — | bot channel jobs (`alerts`), alert transport `rmialerts` + `alerts_channel` (handle surface, env `RMI_ALERTS_CHANNEL`) |
| alpha | `@rmialpha` | RMI Whale & Alpha Signals | `rmi/telegram/CHANNEL_CRYPTO_ALPHA` | `RMI_ALPHA_CHANNEL_ID` | — | bot channel jobs (`alpha`), alert transport `rmialpha` |
| scans | `@rmiscans` | RMI Scanner - Suspicious Tokens | `rmi/telegram/CHANNEL_COMMUNITY_SCANS` | `RMI_SCANS_CHANNEL_ID` | — | bot channel jobs (`scans`) — **no backend surface yet** |
| main | `@cryptorugmuncher` | Main (personal/curated) | `rmi/telegram/CHANNEL_CRYPTO_RUGMUNCHER` | `RMI_MAIN_CHANNEL_ID` | — | bot `main` notifications, alert transport `main_channel` |
| updates | `@rmiupdates` | RMI Changelog | `rmi/telegram/CHANNEL_RMI_UPDATES` | `RMI_UPDATES_CHANNEL_ID` | — | mapped in registry; **no consumer yet** (no scheduled job, no transport surface) |

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
- **All public handles verified by the operator (2026-08-14).** Every role now
  carries its real channel handle: `@rmicryptonews` (news), `@rmialerts`
  (alerts), `@rmialpha` (alpha), `@rmiscans` (scans),
  `@cryptorugmuncher` (main/personal), `@rmiupdates` (updates).
- **Bot admin rights.** The RugMunchBot has admin rights in every channel
  listed above, so the scheduled channel jobs and the alert transport can
  post to all of them.
