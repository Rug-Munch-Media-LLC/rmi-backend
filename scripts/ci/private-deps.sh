#!/usr/bin/env bash
# scripts/ci/private-deps.sh — make the private pinned dependency resolvable in CI.
#
# pyproject.toml pins:
#   rugmunch-scanner @ git+https://git.rugmunch.io/RugMunchMedia/rugmunch-scanner.git@<sha>
# The CI runner holds no credential for that host, so every job that installs the
# dependency died in "Install dependencies" with
#   fatal: could not read Username for 'https://git.rugmunch.io'
# (measured: CI / build-and-test, 0 successes in 281 attempts over 45 days.)
#
# Fix: install a git url.<authed>.insteadOf prefix rewrite for the host, so pip and
# uv fetch the pinned sha over https using the job token. Only the host prefix is
# rewritten — the repo path and the pinned commit are carried through untouched.
# The token comes from the job (`CI_GIT_TOKEN: ${{ github.token }}`); it is never
# written into a workflow file, never printed, and dies with the ephemeral container.
# Precedent: nightly-mutation.yml clones RugMunchMedia/standards the same way.
set -euo pipefail

HOST="${CI_GIT_HOST:-https://git.rugmunch.io}"
DEP_REPO="RugMunchMedia/rugmunch-scanner.git"
# A dedicated secret with cross-repo read wins; the repo-scoped job token is
# the fallback (it cannot read another private repo).
TOKEN="${CI_GIT_TOKEN_SECRET:-${CI_GIT_TOKEN:-}}"

if [[ -z "$TOKEN" ]]; then
	echo "::error::CI_GIT_TOKEN is empty — the step that installs the private pinned dependency must set 'env: {CI_GIT_TOKEN: \${{ github.token }}}'"
	exit 1
fi

AUTHED="${HOST/https:/https://oauth2:${TOKEN}@}"
git config --global "url.${AUTHED}/.insteadOf" "${HOST}/"
# Never echo a config value here: the key holds the token.
echo "insteadOf rewrite installed for ${HOST}/ (token from the job, not the repo)"

# Fail fast and loudly if the token cannot read the repo, instead of surfacing a
# credential prompt 60 seconds later inside the resolver with no explanation.
if ! git ls-remote "${HOST}/${DEP_REPO}" HEAD >/dev/null 2>&1; then
	echo "::error::the job token cannot read ${HOST}/${DEP_REPO} — grant the runner token read access to that repository"
	exit 1
fi
echo "job token can read ${DEP_REPO}; pinned sha will resolve without a prompt"
