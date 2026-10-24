"""Declarative free-pool spec — providers are DATA, never code."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

SPEC_PATH: Final = Path(__file__).with_name("pool.json")


class PoolSpecError(ValueError):
    """The pool file is missing, malformed, or contains a banned provider."""


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """One upstream free lane, in fallback order."""

    id: str
    model: str
    base_url: str
    secret_env: str
    secret_dotenv: str = ""
    daily_requests: int = 0
    rpm: int = 0
    timeout: float = 60.0
    kind: str = "openai"
    needs_proxy: bool = False
    enabled: bool = True

    def api_key(self, secrets_dir: str | None = None) -> str | None:
        """Key from the environment, else from a secrets dir by naming convention.

        The directory is runtime input (``FREE_POOL_SECRETS_DIR``), never a
        committed path: this repo must not carry internal filesystem shape.
        """
        value = os.getenv(self.secret_env, "").strip()
        if value:
            return value
        base = secrets_dir or os.getenv("FREE_POOL_SECRETS_DIR", "")
        return _from_secrets_dir(self, base) if base else None


def _candidate_paths(spec: ProviderSpec, base: str) -> list[Path]:
    """Convention: ``<dir>/<env name lowercase>``, ``.env``, then the bundle."""
    name = spec.secret_env.lower()
    paths = [Path(base) / name, Path(base) / f"{name}.env"]
    if spec.secret_dotenv:
        paths.append(Path(base) / f"{spec.secret_dotenv}.env")
    return paths


def _from_secrets_dir(spec: ProviderSpec, base: str) -> str | None:
    """First readable candidate that yields either a raw key or our name."""
    for candidate in _candidate_paths(spec, base):
        try:
            raw = candidate.read_text().strip()
        except OSError:
            continue
        if "=" not in raw:
            return raw or None
        for line in raw.splitlines():
            key, _, rest = line.partition("=")
            if key.strip() == spec.secret_env:
                return rest.strip().strip("\"'") or None
    return None


@dataclass(frozen=True, slots=True)
class PoolSpec:
    """The ordered pool plus the fail-closed contract."""

    name: str
    providers: tuple[ProviderSpec, ...]
    fail_closed_code: str = "free_pool_exhausted"

    def enabled(self) -> tuple[ProviderSpec, ...]:
        """Providers eligible for traffic, in fallback order."""
        return tuple(p for p in self.providers if p.enabled)

    def by_id(self, provider_id: str) -> ProviderSpec:
        for provider in self.providers:
            if provider.id == provider_id:
                return provider
        msg = f"unknown provider: {provider_id}"
        raise PoolSpecError(msg)


def load_pool(path: Path | None = None) -> PoolSpec:
    """Load and validate the pool. Raises :class:`PoolSpecError`."""
    target = path or SPEC_PATH
    try:
        raw: dict[str, Any] = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"cannot read pool spec {target}: {exc}"
        raise PoolSpecError(msg) from exc
    providers = tuple(ProviderSpec(**item) for item in raw.get("providers", ()))
    if not providers:
        msg = f"pool spec {target} declares no providers"
        raise PoolSpecError(msg)
    return PoolSpec(
        name=str(raw.get("name", "chat-free")),
        providers=providers,
        fail_closed_code=str(raw.get("fail_closed_code", "free_pool_exhausted")),
    )
