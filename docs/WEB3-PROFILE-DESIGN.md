# Web3-Forward User Profile — Design Spec

> Status: DRAFT · Owner: RugMunch Engineering · Date: 2026-08-13 · READ-ONLY design (no code)
> ⚠️ **Partly SUPERSEDED (corrected 2026-09-30).** The "Better Auth sidecar"
> (`127.0.0.1:3011`) described below **was never deployed**: no listener on
> `:3011`, no `better-auth` dependency in `rmi-frontend/package.json`, and
> `rmi-frontend/src/contexts/AuthContext.tsx` posts to `/api/auth/*` on the
> **backend**, with the HttpOnly `rmi_token` cookie as the only session. There
> is therefore **no split-brain** and **no reconciliation job to build** — the
> one-system design below is already the shipped state. Every "Better Auth"
> reference in this file is aspirational target design, not current state.
> Also: production signs **HS256**, not RS256 (see `DEPLOYMENT.md`).
> Inputs audited: `rmi-frontend/src` (AuthContext, ProfilePage, components/profile/*, services/auth-client) · `rmi-backend/app` (domains/auth, models/user|alert|scan|payment, community_badges, api/v1/public/wallet)

## 0. Current state (audit summary)

| Concern | Today |
|---|---|
| Identity | **Single system — no split-brain.** There is **no** Better Auth sidecar: nothing listens on `127.0.0.1:3011` and `AuthContext.tsx` calls the backend `/api/auth/*` directly. Backend auth (`app/domains/auth/`) is the sole source of truth for email+social and owns JWT (HS256 in production, cookie `rmi_token`), Telegram WebApp HMAC, Google/X/GitHub OAuth, TOTP 2FA, **and wallet nonce-signature** (Redis `rmi:wallet_nonce:{nonce}`, 5-min TTL, single-use; EVM via `eth_account` personal_sign, Solana via nacl+base58) in a Redis hash (`rmi:users`). No reconciliation layer is needed. |
| Wallet connect | `AuthContext` connects via injected providers (`window.ethereum`, `window.solana`: MetaMask/Coinbase/Rainbow/Phantom/Solflare/Backpack); persists only address+provider in `localStorage` (`rmi_wallet_address`). `WalletsTab` links/unlinks/primary via `/api/v1/auth/wallet/{chains,list,link,unlink,set-primary}` with a **client-generated `Math.random()` nonce** — replay-weak vs. the server nonce flow. |
| Activity | Postgres `scan_results` (owner_id nullable), `alerts`/`alert_subscriptions` (owner_id), `payments`. **No canonical watchlist table**; portfolio only as domain module (`app/domains/portfolio`). No user-activity event stream. |
| Achievements | Static hardcoded `ALL_BADGES` in `BadgesTab.tsx` (10 badges) + legacy `string[]`/JSONB on user. `community_badges.py` runs on **SQLite** (separate from Postgres) with vote→tier leaderboard. XP/level in Redis user record. No server-issued badge registry, no on-chain mechanics. |

## 1. Product vision

One web3-native identity surface: **email + social (Better Auth) ⇄ connected wallets (SIWE/SIWS)** unified into a single `profile`, overlaid with **on-chain activity** (scans, alerts, watchlist, portfolio), a **reputation score**, and **NFT-style achievement badges** — all served by one `/api/v1/profile/*` router.

## 2. Profile sections (frontend IA)

`ProfilePage` evolves from 7 legacy tabs to a two-level layout:

1. **Hero / Identity deck** — avatar (ENS/Lens/Farcaster name resolution), display name, `@handle`, tier badge (FREE/STARTER/PRO/UNLIMITED), reputation score ring, XP/level bar.
2. **Identity** — Better Auth email + verified badge; linked Telegram (HMAC auth), X/Google/GitHub (OAuth); connected wallets (chain icons, primary star, verify/unlink). Live ENS/Farcaster/Lens resolution.
3. **On-Chain Activity** — tabbed timeline:
   - **Scans** (my runs: token/wallet, risk score, date, re-run)
   - **Alerts** (active subscriptions, fired alerts feed)
   - **Watchlist** (tokens/wallets/deployers tracked, per-chain)
   - **Portfolio** (wallets → aggregated holdings via `domains/portfolio` risk aggregator)
4. **Reputation** — score breakdown (scans, contributions, premium, verifications) + progress to next level/tier.
5. **Badges** — grid of earned/locked NFT-style badges with rarity tier (Common→Mythic), mint tx link when on-chain.
6. **Security** — 2FA (TOTP, exists), sessions, linked-account revoke.
7. **Settings** — display name, avatar, social links, export, delete.

Public variant: `InvestigatorProfilePage` (community forensics) gains a read-only reputation/badge strip — no emails/wallets.

## 3. Data model (Postgres; extend `app/models/`)

```
users                       — existing: id, email, wallet_address, role, tier, xp, level
                               + ADD display_name, username, avatar_url, bio,
                                 reputation_score INT, reputation_breakdown JSONB,
                                 primary_wallet_id UUID FK, last_active_at
linked_identity             — id, user_id FK, provider (telegram|x|google|github|email),
                               provider_id, handle, verified_at, UNIQUE(provider, provider_id)
wallets                     — id, user_id FK, address, chain (evm|solana), label,
                               is_primary, ens_name, lens_handle, farcaster_username,
                               linked_at, UNIQUE(chain, lower(address))
user_activity               — id, user_id FK, action (scan.run|alert.created|watchlist.add|…),
                               entity_type, entity_id, metadata JSONB, created_at  (event log)
watchlist                   — id, user_id FK, subject_type (token|wallet|deployer), subject_id,
                               chain, note, created_at, UNIQUE(user_id, subject_type, subject_id)
badges                      — id, code UNIQUE (e.g. early_adopter), name, description,
                               icon, rarity (common|rare|epic|legendary|mythic),
                               criteria JSONB, onchain_token_id, contract_address, mint_tx_hash
user_badges                 — user_id FK, badge_id FK, earned_at, source (system|claim),
                               tx_hash, UNIQUE(user_id, badge_id)
```

- Keep `scan_results`, `alerts`, `alert_subscriptions`, `payments` as the activity sources; `user_activity` is the aggregate/feed projection (backfilled by event hooks in scanner/alert/portfolio domains).
- Migration path: Redis `rmi:users` remains auth runtime; Postgres becomes the profile-of-record; a sync job reconciles `users` ↔ Redis.

## 4. API endpoints (`/api/v1/profile/*`, auth: Better Auth session + JWT cookie)

- `GET /profile/me` — full profile: identity, wallets, reputation, badges, activity counts
- `PATCH /profile/me` — display_name, username, bio, avatar, social_links
- `GET /profile/{username_or_id}` — public profile (no email/wallets, opt-in privacy)
- `GET /profile/me/wallets` · `POST /profile/me/wallets/link` · `DELETE .../unlink` · `POST .../set-primary`
- `GET /profile/me/activity?type=&page=` · `GET /profile/me/scans` · `GET /profile/me/alerts` · `GET /profile/me/watchlist` · `POST/DELETE /profile/me/watchlist/{id}`
- `GET /profile/me/portfolio` — aggregated holdings + risk (wraps `domains/portfolio`)
- `GET /profile/me/reputation` — score + breakdown + leaderboard percentile
- `GET /profile/me/badges` · `POST /profile/me/badges/{code}/claim` (when on-chain minted)
- `POST /profile/me/identities/{provider}/link` · `DELETE .../unlink` · `GET /profile/me/identities`
- Align wallet verify with Better Auth SIWE/SIWS plugins (loaded `siweClient` already; add SIWS) — fall back to legacy nonce verify until parity.

## 5. Reputation score

`reputation_score` = weighted sum, recomputed daily (or event-driven):

| Component | Weight | Source |
|---|---|---|
| Scan volume (runs, corrected for duplicates) | 30 | `scan_results` |
| Alert setup + engagement (subscriptions, rescan rate) | 15 | `alert_subscriptions` |
| Watchlist depth + curation quality | 10 | `watchlist` |
| Community contributions (votes, case files, verified reports) | 20 | `community_forensics`, `community_badges` |
| Premium tier | 15 | `payments`/tier |
| Identity verification (email verified, ≥1 linked wallet, 2FA) | 10 | `linked_identity`, `wallets`, 2FA status |

Level thresholds: 0–999 Lv1 … geometric curve; tier gates stay separate (billing). Leaderboard percentile exposed on public profile.

## 6. Achievement badges (NFT-ish)

- **Registry-driven**: `badges` table replaces the hardcoded `ALL_BADGES` array; frontend fetches `GET /profile/me/badges`.
- **Earners** (server-side, event-driven via `user_activity`): Early Adopter (join < 30d), First Scan, 50 Scans (Security Expert), Wallet Master (3+ wallets), Whale Watcher (100 tracked), Rug Survivor (5 avoided), Scam Hunter (10 confirmed reports), Premium (tier), Data Diver (20 DataBus tools), Verified (2FA + wallet).
- **On-chain option**: deploy ERC-1155/SPL badge contract; `user_badges.tx_hash` records mint; UI shows "View on explorer". Gas-free L2 (Base — already primary chain) or deferred claim (`POST /badges/{code}/claim`).
- Rarity tiers drive visual treatment + `reputation_score` bonus (max +5).

## 7. Frontend component architecture

```
src/features/profile/
  api.ts                      — react-query hooks (useProfile, useBadges, useReputation, useActivity…)
  types.ts                    — Profile, Wallet, Badge, Reputation, ActivityItem
  components/
    ProfileHeader/            — hero: avatar, name, tier, score ring, XP bar
    IdentityPanel/            — LinkedIdentities, WalletList, WalletConnectButton (refactor from AuthContext)
    ActivityTabs/             — ActivityFeed, ScansList, AlertsList, WatchlistList, PortfolioPanel
    ReputationPanel/          — ReputationGauge, ScoreBreakdown, LevelProgress
    BadgesGrid/               — BadgeCard, RarityBadge, MintStatus
  pages/ProfilePage.tsx       — assembles panels; keeps legacy tab routing for Settings/Security
```

- Move wallet-connect out of `AuthContext` into `features/profile/components/WalletConnectButton` + a `useWallet` hook (address, provider, chain, link/unlink/primary) to reduce AuthContext bloat (552 lines).
- Replace hardcoded `ALL_BADGES` in `BadgesTab` with server-driven `BadgesGrid`.
- New query keys: `['profile']`, `['profile','wallets']`, `['profile','activity']`, `['profile','badges']`, `['profile','reputation']`.

## 8. Rollout phases

1. **P1 — Unified identity**: `linked_identity` + `wallets` tables; identity panel; reconcile Redis↔Postgres; migrate `WalletsTab` to `/api/v1/profile` with server nonce.
2. **P2 — Activity**: `user_activity` + `watchlist`; activity/scans/alerts tabs; backfill hooks.
3. **P3 — Reputation**: score engine + `/reputation` endpoint + gauge UI.
4. **P4 — Badges**: badge registry, earners, BadgesGrid; optional Base ERC-1155 mint + claim.

## 9. Open questions / REVIEW flags

1. ~~**[REVIEW] Identity split-brain**~~ — **RESOLVED, no split exists** (2026-09-30): the sidecar was never deployed, so there is nothing to reconcile. The open question is now only *where* the profile of record lives (Redis `rmi:users` today vs Postgres `users`) — an owner decision, not a correctness bug.
2. **[REVIEW] Wallet link nonce is client-generated** (`Math.random()` in `WalletsTab`) — replay-weak. Spec mandates server-issued nonce; confirm legacy `/api/v1/auth/wallet/link` accepts it or migrate to Better Auth SIWE/SIWS.
3. **[REVIEW] community_badges runs on SQLite**, separate from Postgres profile tables — unify into Postgres to make reputation/badges consistent.
4. **[REVIEW] `scan_results.owner_id` and `alerts.owner_id` are nullable** — anonymous scans won't attribute to profiles until attribution is added at scan time.
5. **[REVIEW] Watchlist has no canonical table today** — P2 defines it; confirm no parallel store exists in `rugmaps`/`whale_alerts` domains.
6. **[REVIEW] Badge rarity/criteria are product decisions** — thresholds in §6 are proposed; confirm with product before build.
7. **Wallet auth parity**: `AuthContext` notes SIWS plugin is vendored but unused — decide whether wallet auth migrates to Better Auth SIWS (recommended) or stays legacy.

## 10. Non-goals (this iteration)

- No on-chain identity (no ENS-based login, no Sign-In-with-Ethereum replacing sessions entirely)
- No cross-user social graph (follows/feeds) — only public profile read
- No badge marketplace/transfer — badges are proof-of-usage, not tradeable in v1
- No changes to billing/payments tier logic
