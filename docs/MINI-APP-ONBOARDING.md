# Telegram Mini App Onboarding — Rug Munch Intelligence

Status: design · owner=RM Engineering · date=2026-08-13 · READ-ONLY (no code changed)

## 1. Form Factor Decision

**Telegram Mini App** (Web App) over in-bot conversation for onboarding:
- Full-screen canvas → richer value pitch, wallet connect, x402 paywall UI.
- Reuses existing entry point: `socialLogin('telegram')` already opens `https://t.me/rugmunchbot?start=signup` (AuthContext.tsx L304-307).
- rugmunchbot owns the bot → add **Menu Button → Open Mini App** (`https://t.me/rugmunchbot/app`).
- In-bot remains the fallback for deep links, alerts, and `/start` signup → opens Mini App.

## 2. User Flow (Screens)

| # | Screen | Action |
|---|--------|--------|
| S0 | **Landing** | Value prop: animated risk-score demo ("Is this a rug?"), scan a sample token, live threat ticker. CTA: Continue with Telegram. |
| S1 | **Telegram login** (one tap) | Read `initData` via `@telegram-apps/sdk-react`, POST `/api/v1/auth/telegram` → JWT. No password. |
| S2 | **Identity upsell** | Free tier granted (5 scans — existing user model). Optional: link email/Google (OSS OAuth), X, or wallet (WalletConnect) to persist identity off-Telegram. |
| S3 | **First-run value** | Paste token address or wallet → live scan (trial credits via `tier_resolver.check_trial`). Show what Premium unlocks (unlockable cards, muted). |
| S4 | **Premium upsell** | Two rails: **x402 pay-per-call** (USDC, EIP-3009) or **subscription** (existing tiers + Telegram Stars). First purchase = 25% off in-app. |
| S5 | **Retention** | Watchlist + alerts wired to bot webhook; shareable "Rug Check" cards; back to bot. |

## 3. Auth / Identity Mapping

**Telegram → platform account** (backend `app/domains/auth/routes_telegram.py`):
- Verify `initData` hash: HMAC-SHA256(secret=SHA256(bot_token), sorted `k=v` lines). ✅ exists — **must extend**: parse nested `user` JSON, include `query_id`, enforce `auth_date` freshness (≤24h). Current route only accepts flat id/first_name/last_name/username/photo_url.
- User id `tg:{id}`, email `<id>@telegram.rmi`, tier FREE, stored in Redis `rmi:users` + `rmi:users:telegram` index. ✅ exists.
- JWT via `_create_jwt`; cookie+CSRF set. ✅ exists.

**Identity linking (NEW)**: today a Telegram login creates a *separate* account even if the same human has an email/wallet account (store has no merge). Add `POST /api/v1/auth/link` — authenticated by both (verified TG initData + verified email/wallet) → bind `telegram_id` onto the existing user, dedupe. OSS OAuth (Google/X) flows already exist (`oauth.py`, `/auth/google/url`, `/auth/x/url`).

## 4. Wallet Connect Integration Points

- **UI**: WalletConnect modal (RainbowKit or @walletconnect/modal) inside the Mini App — browser extensions **do not inject into the WebApp iframe**, so WalletConnect QR/deep-link is required, not `window.ethereum`.
- **Reuse**: `connectEvmWallet` / `connectSolanaWallet` logic (AuthContext.tsx) — adapt to WalletConnect sessions; chains already mapped (ethereum/polygon/arbitrum/optimism/base/bsc + solana).
- **Auth**: nonce flow — `generate_nonce` (jwt.py) → sign message → `POST /api/v1/auth/wallet` → `verify_wallet_signature` (5-min nonce, replay-safe). ✅ backend exists (auth-client.ts confirms `/api/v1/auth/wallet`).
- **x402 pay**: reuse `x402Payment.ts` (viem EIP-3009 USDC transfer, 6 chains) — but route signing through the WalletConnect client instead of `window.ethereum`.

## 5. Premium Upsell

- **Pay-per-call (x402)**: `POST /api/v1/x402/payment` returns signed invoice (amount_usd, pay_to, chain, expires_at, payment_url, signature) → client signs EIP-3009 → `POST /verify`. Pricing catalog: $0.03–$0.35/tool. ✅ exists, no backend work.
- **Subscription**: existing tiers FREE | PORTFOLIO_PRO ($3) | STARTER | PRO | ELITE (tierGate.ts) via existing billing; **add Telegram Stars** as native rail (Stars purchase works in-bot/WebApp, no card — biggest funnel lever for TG users). Resolve via `tier_resolver`.
- Free tier: 5 scans (`scans_remaining`) — keep as friction point into upsell.

## 6. Implementation Plan + Effort

**Backend (rmi-backend)** — ~3d
1. Extend `routes_telegram.py`: full initData (nested user, query_id, auth_date freshness) + `/auth/link` identity-merge endpoint — 1.5d
2. Wallet nonce endpoint (`GET /api/v1/auth/wallet/nonce`) if missing — 0.5d
3. Bot deep-link route `/start=app` → Mini App URL + Stars checkout webhook — 1d

**Frontend Mini App (NEW repo, e.g. `rmi-tg-miniapp`)** — ~6d
4. Scaffold Vite+React+TS, `@telegram-apps/sdk-react` init (theme, back button, haptics) — 0.5d
5. S0–S1 landing + Telegram login — 1d
6. S2 identity linking UI (OAuth + WalletConnect) — 1.5d
7. S3 scan demo + trial credits — 1d
8. S4 paywall (x402 viem signing via WC client + Stars) — 1.5d
9. S5 watchlist/alerts + share cards — 0.5d

**Bot (rugmunchbot)** — 1d
10. Menu button → `/app`, `/start=app` deep links, alert webhooks — 1d

**Test/E2E** — 1.5d (initData forgery tests, WC session, x402 on Base testnet)
**Total ≈ 11.5 engineer-days.** No new MCPs; no new services; reuses existing auth/x402/bot.

## [REVIEW] Flags
1. Telegram initData is only trustworthy if parsed **server-side** with strict HMAC + `auth_date` ≤24h + bot-token secret — current route accepts a narrow field set; must add full parsing before shipping (spoofing risk).
2. Wallet connect in WebApp iframe: extensions don't inject → must use WalletConnect modal; `window.ethereum` paths (connectEvmWallet, x402Payment.ts) need a client abstraction.
3. Identity merge has no existing logic — two accounts per human today (tg: vs email). `/auth/link` is the critical piece; scope dedupe semantics (merge wallets, keep scans, bump tier) explicitly.
4. x402 EIP-3009 signing requires a live WC session on the chosen chain; default Base (chainId 8453, USDC 0x8335…2913).
5. Mini App URL must be HTTPS + whitelisted in BotFather (domain allowlist); test with Telegram dev preview mode before prod.
6. Stars vs x402 overlap: decide rails by user intent (low-commit → Stars sub; one-off/pro → x402) to avoid confusing the paywall.
