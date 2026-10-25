# ADR: DataBus Provider Contract

Status: Accepted · Scope: `app/databus/` · Branch: `feat/databus-provider-contract`

## Context — measured, not assumed

The brief said "~52 files / 25k lines, 30+ providers, each honouring (or bypassing) a
`DataProvider` ABC". **Measurement contradicts this:**

| Claim | Measured on `forgejo/main` |
|---|---|
| ~52 flat modules in `app/databus/` | **62** `.py` modules, **22,115** lines |
| A `DataProvider` ABC that only some honour | **No ABC exists.** `provider_core.Provider` is a `@dataclass` holding a `fetch_fn: Callable` — providers are plain functions |
| Providers live in `app/databus/providers/` | `provider_implementations/` (11 modules) + `app/domains/databus/` (chains moved in P4); `app/databus/providers.py` is a lazy-import shim |
| Every provider re-implements retry/auth/timeout | Partly: **0** bare `except:`; **4** `except Exception: pass`; retry exists only at chain level |

### Inventory (post-ADR baseline)

| Bucket | Count | Detail |
|---|---|---|
| Provider implementation modules | 11 | `alchemy, arkham, dune, free_data, mcp_bridge, moralis, news, passthrough, passthrough_extra, price, __init__` |
| Modules carrying provider functions (`_<source>_<data>`) | 10 | `mcp_bridge` is transport, excluded from conformance |
| Providers reachable through the contract gate | measured by the gate | `tests/contract/test_databus_provider_contract.py` |
| Providers honouring the contract (subclass `DatabusProvider`) | **1** | `CoinGeckoPriceProvider` (migrated here) |
| Providers honouring the *de-facto* dataclass contract | all registered in chains | they are `fetch_fn` callables, validated by `ProviderChain` |
| Providers that bypass any base | **all but 1** | they own retry, auth, timeout, parse inline |
| Duplicate retry logic | 1 copy now | was 0 (no retry existed outside the chain) |
| Duplicate auth/key resolution | 2 paths | `vault.resolve_key()` (preferred) and `os.getenv()` fallback in `ProviderChain` |
| Modules with no HTTP timeout | 0 in `provider_implementations/` (gate-enforced); flat-module set (20 files call `httpx.AsyncClient`) is not yet gated | |
| Bare `except:` in `app/databus/` | 0 | CI ratchet holds |
| `except Exception: pass` | 4 | must be replaced by typed handlers |

## Decision

Keep the **dataclass registry** (`Provider` + `ProviderChain`) as the composition mechanism —
it is what `app/domains/databus/_generated/chains/*.py` already build, and rewriting it buys
nothing. Add a **provider contract** that the `fetch_fn` of each `Provider` must satisfy.

### The contract

```python
class DatabusProvider:
    name: str            # must equal the Provider name registered in its chain
    base_url: str        # no absolute URLs sprinkled through methods
    timeout: float       # > 0, always passed to httpx (default 10.0s)
    max_attempts: int    # default 3
    tier: str            # local | free_api | freemium | paid
    requires_key: bool   # False for free tier
    key_env: str         # name only — the value comes from the vault

    def __init__(self, *, api_key: str | None = None, timeout: float | None = None) -> None
    async def fetch(self, **kwargs) -> dict[str, Any] | None   # public, retry-wrapped
    async def _call(self, **kwargs) -> dict[str, Any] | None   # subclass hook, one attempt
```

**Fetch signature.** `fetch(**kwargs)` — keyword-only, never positional. `ProviderChain`
calls `fetch_fn(api_key=api_key, **kwargs)`; therefore **every provider function must accept
`**kwargs`**. This is enforced by the gate.

**Error taxonomy** (`app/databus/provider_contract.py`):

| Class | `error_code` | Retryable | Raised when |
|---|---|---|---|
| `ProviderTransientError` | `PROVIDER_TRANSIENT` | yes | 5xx, connection reset, transport error |
| `ProviderTimeoutError` | `PROVIDER_TIMEOUT` | yes | `httpx.TimeoutException` |
| `ProviderRateLimitedError` | `PROVIDER_RATE_LIMITED` | yes (honours `Retry-After`) | 429 |
| `ProviderAuthError` | `PROVIDER_AUTH` | no | 401/403 |
| `ProviderPermanentError` | `PROVIDER_PERMANENT` | no | 4xx other than 401/403/404 |
| `ProviderContractError` | `PROVIDER_CONTRACT` | no | payload violates the declared shape |
| `ProviderConfigError` | `PROVIDER_CONFIG` | no | missing key/token — wiring bug |

`ProviderError` **inherits from `OSError`**. This is deliberate: `ProviderChain.fetch`
already catches `(ConnectionError, OSError, TimeoutError, httpx.HTTPError)`, so typed errors
flow through the existing fallback machinery with zero changes to the chain. A plain
`Exception` base would have escaped the chain and broken failover.

**Retry policy.** Only `retryable=True` is retried. Exponential backoff with jitter:
`min(0.25 * 2**(attempt-1) + U(0, 0.25), 4.0)` s. `max_attempts` = 3 (1 + 2 retries).
Never retry non-idempotent writes — all DataBus providers are reads. `Retry-After` is
honoured for 429.

**Rate limiting.** Per-provider, declared on `Provider.rate_limit_rps` and enforced by
`ProviderChain` (single global limiter was abandoned — see playbook). The provider itself
must not sleep to self-throttle.

**Cache key.** `databus:{provider.name}:{op}:{sha1(sorted_query_params)[:16]}`, TTL owned by
`app/databus/cache.py` per endpoint class. The provider must not read or write the cache;
caching is a chain/manager concern.

**Metrics.** `provider_metrics()["<name>"] = {calls, failures, retries, total_ms}` —
exported for `/metrics`. Every `fetch` records exactly one call and at most one failure.

### A provider may NOT

1. **Write to the database** (Postgres/ClickHouse/Redis) — persistence is manager-side.
2. **Log or `repr` a secret** — `DatabusProvider.__repr__` is redacted; keys come from the
   vault, never `os.getenv` inside a provider.
3. **Swallow an error** — no bare `except`, no `except ...: pass`. Log structured fields
   (`provider=`, `error_code=`, `status=`) or let it propagate.
4. **Create an `httpx.AsyncClient` without `timeout=`** — gate-enforced.
5. **Return raw bytes/`Response`** — return a JSON-serialisable dict or `None`.
6. **Call another provider directly** — compose through a `ProviderChain`.
7. **Sleep to self-throttle.**

## Consequences

- New providers: subclass `DatabusProvider`, implement `_call`, register in a chain.
  The gate fails the build on any shape deviation, so the surface can only improve.
- Legacy providers keep working unchanged (the gate validates them as `fetch_fn` callables).
- Migration is incremental: each move to `DatabusProvider` deletes one copy of retry/
  timeout/parse logic. Ratchet is shrink-only — `tests/contract` counts go up, never down.
