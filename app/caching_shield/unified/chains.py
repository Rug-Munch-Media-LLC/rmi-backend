"""Provider chain implementation for fallback and hedged execution."""

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from app.core.logging import get_logger

logger = get_logger(__name__)


class ProviderChain:
    """Ordered list of data providers with fallback."""

    def __init__(
        self,
        name: str,
        hedge_after_s: float | None = None,
        per_try_timeout_s: float = 5.0,
    ) -> None:
        self.name = name
        self.hedge_after_s = hedge_after_s
        self.per_try_timeout_s = per_try_timeout_s
        self._providers: list[tuple[str, Any, float]] = []  # (name, fn, rate_rps)

    def add(self, name: str, fn: "Callable[..., Any]", rate_rps: float = 10) -> "ProviderChain":
        self._providers.append((name, fn, rate_rps))
        return self

    async def fetch(self, **kwargs: Any) -> Any | None:
        if self.hedge_after_s is not None and len(self._providers) > 1:
            return await self._fetch_hedged(**kwargs)
        return await self._fetch_sequential(**kwargs)

    async def _fetch_sequential(self, **kwargs: Any) -> Any | None:
        for _prov_name, fn, _rps in self._providers:
            try:
                data = await fn(**kwargs)
                if data is not None:
                    return data
            except Exception as e:
                logger.debug(f"Provider {_prov_name} failed: {e}")
                continue
        return None

    async def _fetch_hedged(self, **kwargs: Any) -> Any | None:
        """Hedged provider execution: the leader gets ``hedge_after_s`` grace; if
        it has not answered, every remaining provider fans out concurrently and
        the first usable result wins. Each attempt is bounded by
        ``per_try_timeout_s`` so a hanging provider cannot set the tail.
        """
        primary = self._providers[0][0] if self._providers else ""
        queue = list(self._providers)
        pending: dict[asyncio.Task[Any], tuple[str, bool]] = {}

        def _launch(prov_name: str, fn: Any) -> None:
            task = asyncio.create_task(asyncio.wait_for(fn(**kwargs), timeout=self.per_try_timeout_s))
            pending[task] = (prov_name, prov_name == primary)

        def _harvest(done: set[asyncio.Task[Any]]) -> Any | None:
            for task in done:
                _prov_name, _is_primary = pending.pop(task)
                try:
                    data = task.result()
                except Exception as e:
                    logger.debug(f"Provider {_prov_name} failed: {e}")
                    continue
                if data is not None:
                    return data
            return None

        try:
            prov_name, fn, _rps = queue.pop(0)
            _launch(prov_name, fn)
            done, _ = await asyncio.wait(pending, timeout=self.hedge_after_s, return_when=asyncio.FIRST_COMPLETED)
            result = _harvest(done) if done else None
            if result is not None:
                return result
            # Leader slow or unusable: fan out every remaining provider.
            while queue:
                pn, f2, _r2 = queue.pop(0)
                _launch(pn, f2)
            while pending:
                done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                result = _harvest(done)
                if result is not None:
                    return result
        finally:
            for task in pending:
                task.cancel()
        return None
