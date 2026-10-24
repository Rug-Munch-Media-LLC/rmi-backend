"""Orchestration: policy → rate limit → refusal → retrieval → answer.

Streaming and non-streaming share :meth:`prepare`, so a refused or unsourced
request never opens an upstream connection at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.assistant_gateway.aliases import AliasSpec
from app.assistant_gateway.builder import build_messages, upstream_payload
from app.assistant_gateway.executor import InternalApiToolExecutor
from app.assistant_gateway.loop import run_tool_loop
from app.assistant_gateway.models import ChatCompletionRequest, Citation
from app.assistant_gateway.policy import (
    PolicyViolation,
    last_user_text,
    no_source_answer,
    refusal_answer,
    refusal_reason,
    validate_request,
)
from app.assistant_gateway.proxy import LiteLLMProxy, UpstreamError, default_proxy
from app.assistant_gateway.ratelimit import SlidingWindowLimiter, get_limiter
from app.assistant_gateway.responses import (
    completion_body,
    error_body,
    sse,
    sse_citations,
    sse_text,
)
from app.assistant_gateway.tools import ToolExecutor, select_tools


@dataclass(slots=True)
class Prepared:
    """Everything decided before an upstream call is made."""

    alias: AliasSpec
    messages: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)
    answer: str | None = None
    headers: dict[str, int] = field(default_factory=dict)


class AssistantService:
    """The gateway's single orchestration entry point."""

    def __init__(
        self,
        *,
        proxy: LiteLLMProxy | None = None,
        executor: ToolExecutor | None = None,
        limiter: SlidingWindowLimiter | None = None,
    ) -> None:
        self._proxy = proxy
        self._executor = executor
        self._limiter = limiter

    @property
    def proxy(self) -> LiteLLMProxy:
        if self._proxy is None:
            self._proxy = default_proxy()
        return self._proxy

    @property
    def executor(self) -> ToolExecutor:
        if self._executor is None:
            self._executor = InternalApiToolExecutor()
        return self._executor

    @property
    def limiter(self) -> SlidingWindowLimiter:
        if self._limiter is None:
            self._limiter = get_limiter()
        return self._limiter

    async def prepare(
        self,
        request: ChatCompletionRequest,
        client_key: str,
        alias_override: str | None = None,
    ) -> Prepared:
        """Validate, throttle, refuse, and prefetch citations."""
        alias = validate_request(request, alias_override)
        allowed, window = await self.limiter.acquire(client_key)
        if not allowed:
            raise PolicyViolation(
                429, "rate_limited", "too many assistant requests", headers=window
            )
        prepared = Prepared(alias=alias, headers=window)
        reason = refusal_reason(last_user_text(request.messages))
        if reason:
            prepared.answer = refusal_answer(reason)
            return prepared
        if alias.requires_citations:
            prepared.citations = await self._retrieve(request)
            if not prepared.citations:
                prepared.answer = no_source_answer(alias)
                return prepared
        prepared.messages = build_messages(alias, request, prepared.citations)
        return prepared

    async def _retrieve(self, request: ChatCompletionRequest) -> list[dict[str, str]]:
        """Prefetch RAG sources; an unavailable retriever yields no citations."""
        query = last_user_text(request.messages).strip()
        if not query:
            return []
        result = await self.executor.run("rag_search", {"query": query[:500]})
        return [dict(citation) for citation in result.citations] if result.ok else []

    async def complete(self, prepared: Prepared, request: ChatCompletionRequest) -> dict[str, Any]:
        """Non-streaming response, including the bounded server-side tool loop."""
        citations = _as_citations(prepared.citations)
        if prepared.answer is not None:
            return completion_body(prepared.alias, prepared.answer, citations=citations)
        tools = [spec.as_openai() for spec in select_tools(prepared.alias, request.tools)]
        answer, usage = await run_tool_loop(
            self.proxy,
            self.executor,
            prepared.alias,
            prepared.messages,
            tools,
            request,
        )
        return completion_body(prepared.alias, answer, usage, citations)

    async def stream(
        self, prepared: Prepared, request: ChatCompletionRequest
    ) -> AsyncIterator[bytes]:
        """SSE response. Tools are prefetched above, so the stream is tool-free."""
        if prepared.citations:
            yield sse_citations(_as_citations(prepared.citations))
        if prepared.answer is not None:
            yield sse_text(prepared.alias, prepared.answer)
            return
        payload = upstream_payload(
            prepared.alias,
            prepared.messages,
            [],
            stream=True,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        )
        try:
            async for chunk in self.proxy.stream(payload):
                yield chunk
        except UpstreamError as exc:
            yield sse(error_body("upstream_error", str(exc))) + sse("[DONE]")


def _as_citations(raw: list[dict[str, str]]) -> list[Citation]:
    """Coerce stored citation dicts into the response model."""
    return [Citation(**citation) for citation in raw]
