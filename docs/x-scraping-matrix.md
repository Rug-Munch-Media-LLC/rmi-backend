# X/Twitter Scraping Matrix — live audit (2026-10-24)

Scope: every X content path in `rmi-backend` @ `x-audit`.
Method: one live probe per path from Talos (`talos.rugmunch.io`), plus key-presence
checks inside the running `rmi-backend` container (names only, no values).
Probe timestamp 2026-10-24; egress = Talos datacenter IP.

## Matrix

| # | Path | Code | Auth | Proxy | Coverage | Live probe | Cost | Reliability |
|---|------|------|------|-------|----------|-----------|------|-------------|
| 1 | **Web GraphQL timeline** | `scripts/news_x/x_graphql.py` + `x_session.py` | session cookies `auth_token`+`ct0` from the operator browser-export jar (67 rows, both present) | optional (`proxy=` param), unset | **profile + originals timeline**, ids + ISO timestamps | **OK — 65 posts, 2.6 s, route `graphql`**, newest 2026-10-05 | $0 | **primary**; dies when the browser session expires (`auth_http_401`), no alert today |
| 2 | GetXAPI | `app/domains/shill/getxapi.py` | `Bearer GETXAPI_API_KEY` (present in container, len 58) | none | profile + authored tweets | **402 Payment Required** on `/twitter/user/info` and `/twitter/user/tweets` (1.6–2.4 s, 32 B body) | paid credits — **exhausted** | **dead now**; failure is indistinguishable from "handle not found" and negative-caches for 900 s |
| 3 | Pry (self-hosted) | `scripts/news_x/readers.pry`, `app/integrations/pryscraper_client.py` | `PRY_API_KEY` — **absent** in container and reader env | Pry's own FlareSolverr | profile + rendered markdown (no ids) | service healthy (`/health` ok, v3.0.0, flaresolverr up); `POST /v1/scrape` to **402 x402** (Base `eip155:8453`, 1000 units) | metered x402 | reader returns `no_pry_api_key` before it calls; service fine, lane unauthenticated |
| 4 | Bright Data Web Unlocker | `scripts/news_x/readers.brightdata` | `bdata` CLI v0.4.0 (present, account-authed) | Bright Data | profile markdown | **OK but stale — 5 posts, 4.1 s**, newest 2024-09-19 | credits | fallback only: pinned/archived content, not the current timeline |
| 5 | Syndication embed (HTML) | `readers.syndication` → the `syndication.twitter.com` timeline-profile embed endpoint | none | none | timeline HTML with `__NEXT_DATA__` | **200, 549 KB, 0.6 s, 99 entries with ids**, newest entry **2024-04-05** | $0 | **stale cache**, not a 429: unusable for "today" (reason in `fetch.py` docstring is wrong) |
| 6 | Syndication CDN (JSON) | `cdn.syndication.twimg.com/timeline/profile?screen_name=` | none | none | — | **200 with 0 bytes** | $0 | dead |
| 7 | Nitter RSS (3 mirrors) | `app/databus/social_feeds.py::NITTER_INSTANCES`, `social_scraper.py` | none | none | RSS timelines | `nitter.net` **410**, `nitter.privacydev.net` **TLS name mismatch**, `nitter.poast.org` **NXDOMAIN** — 0/3 | $0 | dead; two pipelines still depend on it |
| 8 | xurl CLI (OAuth) | `app/databus/x_intel/fetchers.py` | OAuth2 via `xurl` | none | search + user timeline (CT Rundown lane) | `command -v xurl` → **not installed**; `~/.x_cookies.json` → **absent** | paid X API | **dead**; `fetch_ct_rundown()` returns an empty rundown today, no error surfaced |
| 9 | Legacy cookie scrape | `fetchers._cookie_scrape_x` | `~/.x_cookies.json` | none | profile timeline | file absent **and** parser targets `__NEXT_DATA__`, which no longer exists in `x.com` HTML (0 matches in 165 KB shell) | $0 | dead twice over |
| 10 | DuckDuckGo web scrape | `fetchers._web_scrape_x` | none | none | status URLs → `og:description` | DDG returns **202 with 0 status URLs**; probed status page 404 | $0 | broken (bot-gated + no discovery) |
| 11 | Jina reader | `readers.jina` (`r.jina.ai`) | none | none | profile text only | **200, 3.6 KB, 0.3 s** | $0 | text-only: no ids, no timestamps, no engagement |
| 12 | X API v2 search | `app/databus/news_intel/fetchers.py::_fetch_x_crypto`, `app/databus/social.py::search_mentions` | `X_API_KEY` / `TWITTER_BEARER_TOKEN` — **absent** in container | none | search, mentions | not callable | paid | **0 coverage**; search is the one capability no working free lane provides |
| 13 | WebShare residential proxies | `app/databus/proxy_rotator.py` | `WEBSHARE_PROXY_LIST` (present, **1 entry**) | residential | none for X — used by GMGN/DexScreener | not wired to any X lane (`X_INGEST_PROXY` unset) | subscription | X traffic never exits residential; free lanes stay datacenter-IP bound |

## Coverage summary

| Capability | Working lane | Notes |
|---|---|---|
| Profile read | GraphQL (cookies) | followers/verified/name; GetXAPI profile is 402 |
| Timeline / status text | GraphQL (cookies) | 65 posts, ids + dates; Bright Data only gives 2024 material |
| Thread reconstruction | none | `is_reply` exists only in `scripts/news_x/model.py`; `app/` lanes drop it |
| Search / discovery | **none** | needs `X_API_KEY` (absent) or a working xurl (absent) |
| Engagement metrics | partial | `x_intel` scores on likes/RTs but the GraphQL parser drops `public_metrics` |

## Gaps and failure modes

1. **Two payment walls masquerade as content misses.** GetXAPI answers 402 and the
   caller caches `not_found` for 900 s; Pry answers 402 x402 while the reader returns
   `no_pry_api_key` first. Billing outages look exactly like "this account has no posts".
2. **No health signal.** No path exposes reachability; a dead lane only shows up as an
   empty rundown.
3. **Stale-lane drift.** The syndication "429" comment is wrong (it is a stale embed), so
   nobody re-tests it; dead Nitter mirrors are still shipped in `social_feeds.py`.
4. **Datacenter egress everywhere.** The residential proxy tier exists but is not wired to
   any X path, so every free lane is one blocklist entry from total loss.
5. **Single point of failure.** GraphQL+sessions is the only current-coverage lane; when
   the cookie jar expires, everything stops (paths 5–11 cannot replace it).
