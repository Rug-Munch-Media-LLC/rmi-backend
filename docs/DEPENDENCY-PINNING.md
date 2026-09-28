# Dependency Pinning — `rugmunch-scanner`

**Status:** canonical · **Scope:** the `rugmunch-scanner` git dependency

This repo consumes the private `rugmunch-scanner` engine as a source
dependency. A source dependency is special: unlike a PyPI package, it has no
immutable release artifact behind it — the "version" is a git ref, and a git
ref can move. This document exists so that the ref **cannot** move silently.

---

## 1. What is pinned, and where

There is exactly **one** authoritative pin.

| What | Where | Value |
|---|---|---|
| Authoritative pin (committed) | `pyproject.toml` — `rugmunch-scanner @ git+…` | `13d7878cfe62f9bcda847c9b1f94ba498b1b6d45` |
| Build-time override | `Dockerfile.multistage` — `ARG RUGMUNCH_SCANNER_REF` | *empty* (means: use the committed SHA) |
| Resolved record (not authoritative for Docker) | `uv.lock` | `…rugmunch-scanner.git#13d7878cfe62…` |

> **Why a commit SHA and not a tag?** A tag is a *mutable* pointer: anyone can
> run `git tag -f v0.2.0 <other-sha>` and every subsequent build installs
> different code under the same name. A branch is worse — it advances on every
> merge. Only a 40-hex commit SHA is content-addressed and can never be
> rewritten. The tag `v0.2.0` already points at `d7c502b`; if the pin were
> `@v0.2.0`, moving that one tag would silently change the production backend
> with no diff in this repo. Do not "simplify" the SHA back to a tag.

Every install path funnels through that one line, because `requirements.txt`
is just an editable install of the project itself:

```
requirements.txt  ->  -e .            (line 2)
                  ->  pyproject.toml  ->  the scanner git URL  (single source of truth)
```

So `pip install -e .`, `pip install -r requirements.txt`, and the
`Dockerfile.multistage` builder stage all resolve the *same* pinned ref. There
is no second place to keep in sync — that is deliberate.

### The one-line summary of the resolution path

```
Dockerfile.multistage:46  pip install -r requirements-prod.txt
requirements.txt:2        -e .
pyproject.toml:37         "rugmunch-scanner @ git+…rugmunch-scanner.git@13d7878…"
```

---

## 2. How the build-time override works

`Dockerfile.multistage` declares:

```dockerfile
ARG RUGMUNCH_SCANNER_REF=""
```

When it is **empty** the committed SHA in `pyproject.toml` is used verbatim.
When it is set it must be a 40-hex commit SHA; the build substitutes it in
before pip reads the file, and **fails loudly** on anything else (a branch name,
a tag, a URL, or uppercase hex). The base repository URL is anchored and
cannot be redirected by a build-arg value.

The ARG exists so an upgrade/rollback is a one-flag rebuild rather than a
source edit:

```bash
# Roll back / forward without editing any file
docker build -f Dockerfile.multistage \
  --build-arg RUGMUNCH_SCANNER_REF=<40-hex-sha> \
  -t backend-rmi-backend:rollback .
```

---

## 3. How to move the pin FORWARD (intentional upgrade)

1. Pick the scanner commit you have actually reviewed and tested.
2. Build and test the backend against it (the override is enough to try it):

   ```bash
   docker build -f Dockerfile.multistage \
     --build-arg RUGMUNCH_SCANNER_REF=<candidate-sha> \
     -t backend-rmi-backend:candidate .
   ```

3. Once green, commit the new SHA as the default so the repo is self-consistent:

   ```bash
   # Edit pyproject.toml: replace @13d7878… with @<candidate-sha>
   uv lock            # refresh the lock's resolved source line
   #   (Expect exactly the scanner source line to change; if uv rewrites many
   #    other packages, that is NOT a pin move — stop and use `uv lock
   #    --upgrade-package rugmunch-scanner` instead, see §4.)
   ```

4. Update the table in §1 of this file with the new SHA.
5. Commit: `fix(deps): pin rugmunch-scanner to <short-sha>`

---

## 4. About `uv.lock` (read this before regenerating)

`uv.lock` records the resolved source line for the scanner and is used by
`uv`-based workflows. It is **not** what the Docker build installs — Docker
uses `pip` against `pyproject.toml`, so the Docker build is governed by the
SHA in `pyproject.toml`, not by the lock.

When you intentionally move the pin, regenerate the lock **scoped to the one
package**:

```bash
uv lock --upgrade-package rugmunch-scanner
```

Do **not** run a bare `uv lock` (or `uv lock --upgrade`) as a side effect of a
pin change. This repo has hundreds of loose lower bounds (`>=…`) in
`pyproject.toml`; an unscoped `uv lock --upgrade` would pull every one of them
forward at once, mixing an unrelated dependency sweep into a pin bump. That is
how a pinning fix turns into an outage. `uv lock` without `--upgrade` respects
the existing lock and only re-resolves what the edit actually invalidated,
which is the safe default.

---

## 5. ROLLBACK

**Fastest — no rebuild, use the override flag (leaves the committed pin alone):**

```bash
docker build -f Dockerfile.multistage \
  --build-arg RUGMUNCH_SCANNER_REF=13d7878cfe62f9bcda847c9b1f94ba498b1b6d45 \
  -t backend-rmi-backend:rollback .
docker compose -f <compose-backend-file>.yml up -d rmi-backend
```

(Use whichever compose file actually runs the backend in your environment.)

**Canonical — roll the committed pin itself back** (if the bad SHA was
committed and deployed):

```bash
cd /path/to/rmi-backend            # the backend checkout
git revert <commit-that-changed-the-pin>   # or:
git log -S 'rugmunch-scanner.git@' -- pyproject.toml   # find the pin-changing commit
git revert <that-commit>
uv lock --upgrade-package rugmunch-scanner
docker build -f Dockerfile.multistage -t backend-rmi-backend:latest .
docker compose -f <compose-backend-file>.yml up -d rmi-backend
```

**Image-only rollback** (if a known-good image already exists) — see
`docs/DEPLOYMENT.md` §8 "Rollback Procedures".

> **Note for maintainers:** `docs/DEPLOYMENT.md` §8 is the canonical place a
> reader looks for rollback instructions, but that file cannot currently be
> edited past this repo's `methodology-leak` pre-commit gate — it already
> contains ~23 `/srv/...` internal paths and is not in the gate's
> `EXEMPT_FILES` list, so *any* modification to it blocks the commit. This
> document is therefore the authoritative scanner-pinning rollback procedure.
> Fixing that is a separate change: either add `docs/DEPLOYMENT.md` to
> `EXEMPT_FILES` in `scripts/pre-commit-methodology-check.sh`, or scrub its
> paths to placeholders. See the "Known gap" note in the commit message.

---

## 6. Verifying the pin is real

A quick, install-free check that the pinned SHA exists and that the syntax
resolves:

```bash
# Does the commit exist in the scanner repo? (read-only, no clone)
git ls-remote https://git.rugmunch.io/RugMunchMedia/rugmunch-scanner.git

# Does uv accept the pinned requirement and resolve it? (metadata only)
uv lock --dry-run
```

A successful `uv lock` that reports the scanner at the pinned SHA proves the
ref is both syntactically valid and reachable. If uv instead reports
`(HEAD)` / branch tip, the pin is not being honored.
