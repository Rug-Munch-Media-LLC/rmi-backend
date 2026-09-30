# JWT signing — HS256 in production, and why JWKS publishes nothing

> Corrected 2026-09-30. The HS256 claim here supersedes the `RS256` text in
> `DEPLOYMENT.md` (§ JWT), `ARCHITECTURE.md` (§ Security Model) and
> `unified-auth.md`, which still read "RS256" and "serves JWKS".

## What production actually runs

`JWT_ALGORITHM=HS256`. Verified two ways: the backend's own environment, and
`app/auth/jwt_validation.py:get_jwks()`, which refuses to emit a key unless
`JWT_ALGORITHM=RS256` — both JWKS routes answer with an error naming HS256 as
the active algorithm.

`app/domains/auth/jwt.py` implements **both** paths; only one is live:

| Path | Condition | Status |
|------|-----------|--------|
| `jwt.encode(payload, JWT_PRIVATE_KEY, algorithm="RS256")` | `JWT_ALGORITHM=RS256` **and** the RSA private key is set | implemented, **inactive** — no key is configured |
| `jwt.encode(payload, JWT_SECRET, algorithm="HS256")` | otherwise | **live** |

`app/auth/jwt_validation.py` reads the same `JWT_ALGORITHM` and picks
`algorithms=["RS256"]` or `["HS256"]` to match, so verification is self-consistent
inside this service.

## Consequence: the public-key routes are dead

| Route | Handler | Live response |
|-------|---------|---------------|
| `GET /.well-known/jwks.json` | `app/routers/jwks.py:jwks_well_known` | error — no key published |
| `GET /api/v1/auth/jwks` | `app/routers/jwks.py:jwks_v1` | error — no key published |

There is **no RSA public key to publish under HS256**, so the module docstring
("serves the RSA public key") and `API.md` / `API_REFERENCE.md` ("JWKS public
keys for JWT verification") describe an endpoint that returns nothing.

## The unresolved cross-service gap

Peer services validate this backend's tokens by fetching the JWKS document
(`PRY_JWKS_URL` defaults to the public `/.well-known/jwks.json`) and verifying
**RS256 only**, fail-closed at startup — the HS256 fallback was deliberately
removed after the 2026-08-20 algorithm-confusion audit
(`pryscraper/src/pry/core/jwt_auth.py`, module top). That peer has **no
shared-secret path**.

So as shipped: this service issues HS256 tokens that its peers are built to
refuse, and the endpoint they are built to fetch publishes no key. Neither
doc set claimed this. It needs an owner decision — issue RS256 (and configure
the key pair, which also revives JWKS), or add a documented shared-secret
verification path on the peer side. Until then, do not tell an agent that
"services validate rmi-backend tokens via JWKS".
