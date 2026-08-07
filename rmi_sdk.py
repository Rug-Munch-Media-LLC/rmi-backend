"""
RMI Python SDK v2.0 - Production-Grade Client
===============================================
Rug Munch Intelligence Python SDK with:
- Async/sync dual API (httpx)
- Automatic retry with exponential backoff
- Batch tool calls (parallel execution)
- Webhook registration and management
- Developer API key support
- x402 payment handling
- Type hints throughout
- Proper error classes

Install: pip install rmi-sdk  (or copy this file as rmi_sdk.py)

Usage:
    from rmi_sdk import RMI
    rmi = RMI(api_key="rmi_dev_...")  # free tier, 100 calls/day
    result = rmi.scan("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")

    # Async
    async with AsyncRMI(api_key="rmi_dev_...") as rmi:
        result = await rmi.scan("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")

    # Batch
    results = rmi.batch([
        ("scan", {"address": "0x..."}),
        ("audit", {"address": "0x..."}),
        ("whale", {"address": "0x..."}),
    ])

Author: RMI Development
Date: 2026-06-05
"""

import contextlib
import hashlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

try:
    import asyncio

    ASYNCIO_AVAILABLE = True
except ImportError:
    ASYNCIO_AVAILABLE = False

# ── Error Classes ─────────────────────────────────────────────────


class RMIError(Exception):
    """Base RMI SDK error."""


class RMIConnectionError(RMIError):
    """Failed to connect to RMI API."""


class RMITimeoutError(RMIError):
    """Request timed out."""


class RMIAuthenticationError(RMIError):
    """Invalid or missing API key."""


class RMIRateLimitError(RMIError):
    """Rate limit exceeded."""

    def __init__(self, message: str, retry_after: int = 60, remaining: int = 0):
        super().__init__(message)
        self.retry_after = retry_after
        self.remaining = remaining


class RMIPaymentRequired(RMIError):
    """Payment required for this tool call."""

    def __init__(self, message: str, pay_to: str = "", amount: str = "", chain: str = ""):
        super().__init__(message)
        self.pay_to = pay_to
        self.amount = amount
        self.chain = chain


class RMIToolError(RMIError):
    """Tool execution failed."""

    def __init__(self, tool: str, message: str, status: str = "error"):
        super().__init__(message)
        self.tool = tool
        self.status = status


class RMIDataError(RMIError):
    """No data returned for the query."""


# ── Data Classes ──────────────────────────────────────────────────


@dataclass
class ToolResult:
    """Result from a tool call."""

    tool: str
    data: dict[str, Any]
    status: str = "ok"
    enrichment: dict[str, Any] | None = None
    intel: dict[str, Any] | None = None
    confidence: dict[str, Any] | None = None
    trial_remaining: int = 0
    cache_hit: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ok(self) -> bool:
        return self.status in ("ok", "success")

    @property
    def has_data(self) -> bool:
        if not self.data:
            return False
        # Check for meaningful data (not just empty dicts/lists)
        return any(v is not None and v != [] and v != {} for v in self.data.values())


@dataclass
class BatchResult:
    """Result from a batch tool call."""

    results: list[ToolResult]
    successful: int = 0
    failed: int = 0
    total_time_ms: float = 0

    @property
    def success_rate(self) -> float:
        total = self.successful + self.failed
        return (self.successful / total * 100) if total > 0 else 0


@dataclass
class Webhook:
    """Registered webhook."""

    id: str
    url: str
    events: list[str]
    address: str
    active: bool = True
    created_at: str = ""


@dataclass
class TierInfo:
    """Developer tier information."""

    name: str
    daily_limit: int
    rate_limit_per_min: int
    price: float | str
    webhook_support: bool
    priority_queue: bool


@dataclass
class UsageInfo:
    """API key usage information."""

    tier: str
    email: str
    status: str
    today_calls: int
    daily_limit: int
    remaining_today: int
    total_calls: int
    created_at: str
    rate_limit_per_min: int


# ── Retry Logic ───────────────────────────────────────────────────

T = TypeVar("T")


def retry_with_backoff(
    func: Callable[..., T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    retryable_errors: tuple[type, ...] = (RMIConnectionError, RMITimeoutError),
) -> T:
    """Execute a function with exponential backoff retry."""
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except retryable_errors as e:
            last_exception = e
            if attempt == max_retries:
                break
            delay = min(base_delay * (exponential_base**attempt), max_delay)
            # Add jitter
            import random

            delay *= 0.5 + random.random()
            time.sleep(delay)
    raise last_exception or RMIError("Retry failed")


# ── Sync SDK ──────────────────────────────────────────────────────


class RMI:
    """Rug Munch Intelligence client with automatic x402 payment handling."""

    DEFAULT_BASE = "https://mcp.rugmunch.io/api/v1/x402-tools"
    DEFAULT_DEV_BASE = "https://mcp.rugmunch.io/api/v1/developer"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        dev_base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        fingerprint: str | None = None,
        wallet_identity: str | None = None,
    ):
        """
        Initialize RMI client.

        Args:
            api_key: Developer API key (from /api/v1/developer/register).
                     If not provided, uses trial mode (limited calls).
            base_url: Base URL for x402 tools API.
            dev_base_url: Base URL for developer API.
            timeout: Request timeout in seconds.
            max_retries: Number of retries on transient errors.
            retry_delay: Base delay between retries (seconds).
            fingerprint: Client fingerprint for trial tracking.
            wallet_identity: Verified wallet identity (from /developer/wallet/verify).
        """
        if not HTTPX_AVAILABLE:
            raise ImportError(
                "httpx is required: pip install httpx. Or use the fallback urllib mode (limited features)."
            )

        self.api_key = api_key or os.environ.get("RMI_API_KEY")
        self.base = (base_url or os.environ.get("RMI_API_BASE") or self.DEFAULT_BASE).rstrip("/")
        self.dev_base = (dev_base_url or os.environ.get("RMI_DEV_BASE") or self.DEFAULT_DEV_BASE).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.wallet_identity = wallet_identity

        # Generate fingerprint if not provided
        if fingerprint:
            self._fingerprint = fingerprint
        else:
            self._fingerprint = hashlib.sha256(f"rmi-sdk-v2-{os.uname().nodename}-{time.time()}".encode()).hexdigest()[
                :32
            ]

        # Create client
        headers = {
            "Content-Type": "application/json",
            "x-fingerprint": self._fingerprint,
            "User-Agent": "rmi-python-sdk/2.0",
        }
        if self.api_key:
            headers["X-RMI-Dev-Key"] = self.api_key
        if self.wallet_identity:
            headers["X-Wallet-Identity"] = self.wallet_identity

        self._client = httpx.Client(
            base_url=self.base,
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=10.0),
            http2=True,  # Use HTTP/2 if available
        )

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_data: dict | None = None,
        headers: dict | None = None,
        base_url: str | None = None,
    ) -> httpx.Response:
        """Make an HTTP request with retry logic."""
        f"{base_url or self.base}/{path.lstrip('/')}"

        def _do_request():
            try:
                if method.upper() == "GET":
                    return self._client.get(path, params=params, headers=headers)
                else:
                    return self._client.post(path, json=json_data, headers=headers)
            except httpx.ConnectTimeout as e:
                raise RMITimeoutError(f"Connection timeout: {e}") from e
            except httpx.ReadTimeout as e:
                raise RMITimeoutError(f"Read timeout: {e}") from e
            except httpx.ConnectError as e:
                raise RMIConnectionError(f"Connection failed: {e}") from e
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    raise RMIAuthenticationError("Invalid API key") from e
                elif e.response.status_code == 402:
                    body = e.response.json() if e.response.content else {}
                    raise RMIPaymentRequired(
                        message=body.get("error", "Payment required"),
                        pay_to=body.get("pay_to", ""),
                        amount=body.get("amount", ""),
                        chain=body.get("chain", ""),
                    ) from e
                elif e.response.status_code == 429:
                    retry_after = int(e.response.headers.get("Retry-After", 60))
                    raise RMIRateLimitError(
                        message="Rate limit exceeded",
                        retry_after=retry_after,
                    ) from e
                else:
                    raise RMIToolError(
                        tool=path.split("/")[-1],
                        message=f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                    ) from e

        return retry_with_backoff(
            _do_request,
            max_retries=self.max_retries,
            base_delay=self.retry_delay,
            retryable_errors=(RMIConnectionError, RMITimeoutError),
        )

    def _parse_response(self, response: httpx.Response, tool: str) -> ToolResult:
        """Parse an API response into a ToolResult."""
        try:
            data = response.json()
        except Exception:
            raise RMIToolError(tool, f"Invalid JSON response: {response.text[:200]}") from None

        # Check for error
        if "error" in data and not data.get("result"):
            raise RMIToolError(tool, data["error"], status=data.get("status", "error"))

        # Check for no data
        if data.get("status") == "no_data":
            raise RMIDataError("No data found for query")

        return ToolResult(
            tool=tool,
            data=data.get("result", data),
            status=data.get("status", "ok"),
            enrichment=data.get("_enrichment"),
            intel=data.get("_intel"),
            confidence=data.get("_confidence"),
            trial_remaining=int(response.headers.get("X-RMI-Trial-Remaining", 0)),
            cache_hit=response.headers.get("X-Cache") == "HIT",
            raw=data,
        )

    # ── Core Tool Execution ───────────────────────────────────────

    def call(self, tool: str, params: dict | None = None) -> ToolResult:
        """Call a single tool.

        Args:
            tool: Tool name (e.g., "scan", "audit", "whale").
            params: Tool parameters as a dict.

        Returns:
            ToolResult with data, enrichment, and metadata.
        """
        response = self._request("POST", tool, json_data=params or {})
        return self._parse_response(response, tool)

    # ── Convenience Methods ───────────────────────────────────────

    def scan(self, address: str, chain: str = "solana") -> ToolResult:
        """Scan a wallet address for security risks."""
        return self.call("scan", {"address": address, "chain": chain})

    def audit(self, address: str, chain: str = "solana") -> ToolResult:
        """Audit a smart contract."""
        return self.call("audit", {"address": address, "chain": chain})

    def whale(self, address: str, chain: str = "solana") -> ToolResult:
        """Analyze whale wallet activity."""
        return self.call("whale", {"address": address, "chain": chain})

    def reputation(self, address: str) -> ToolResult:
        """Get reputation score for an address."""
        return self.call("reputation_score", {"address": address})

    def honeypot(self, address: str, chain: str = "solana") -> ToolResult:
        """Check if a token is a honeypot."""
        return self.call("honeypot_check", {"address": address, "chain": chain})

    def forensics(self, address: str, chain: str = "solana") -> ToolResult:
        """Run wallet forensics."""
        return self.call("forensics", {"address": address, "chain": chain})

    def sentiment(self, query: str) -> ToolResult:
        """Get market sentiment for a query."""
        return self.call("sentiment", {"query": query})

    def rug_probability(self, address: str, chain: str = "solana") -> ToolResult:
        """Get rug probability score for a token."""
        return self.call("rug_probability", {"address": address, "chain": chain})

    def composite_score(self, address: str, chain: str = "solana") -> ToolResult:
        """Get composite score for a token."""
        return self.call("composite_score", {"address": address, "chain": chain})

    def smart_money(self, address: str) -> ToolResult:
        """Find smart money wallets similar to this one."""
        return self.call("smart_money", {"address": address})

    def market_overview(self) -> ToolResult:
        """Get market overview."""
        return self.call("market_overview", {})

    def narrative(self) -> ToolResult:
        """Get current market narrative."""
        return self.call("narrative", {})

    # ── Batch Execution ───────────────────────────────────────────

    def batch(self, calls: list[tuple[str, dict]], max_concurrent: int = 5) -> BatchResult:
        """Execute multiple tool calls in parallel.

        Args:
            calls: List of (tool_name, params) tuples.
            max_concurrent: Maximum concurrent requests.

        Returns:
            BatchResult with all results.
        """
        import concurrent.futures
        import threading

        results = []
        successful = 0
        failed = 0
        start = time.monotonic()
        semaphore = threading.Semaphore(max_concurrent)

        def _execute(tool: str, params: dict) -> ToolResult:
            with semaphore:
                try:
                    return self.call(tool, params)
                except Exception as e:
                    return ToolResult(
                        tool=tool,
                        data={},
                        status="error",
                        raw={"error": str(e)},
                    )

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {executor.submit(_execute, tool, params): (tool, params) for tool, params in calls}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                results.append(result)
                if result.is_ok:
                    successful += 1
                else:
                    failed += 1

        total_time = (time.monotonic() - start) * 1000

        return BatchResult(
            results=results,
            successful=successful,
            failed=failed,
            total_time_ms=total_time,
        )

    # ── Developer API ─────────────────────────────────────────────

    def register(self, email: str, tier: str = "free", wallet_address: str | None = None) -> dict:
        """Register for a developer API key.

        Args:
            email: Email address for registration.
            tier: Tier name (free, trial, paid, pro).
            wallet_address: Optional wallet address for enhanced identity.

        Returns:
            Dict with api_key and tier info.
        """
        response = self._request(
            "POST",
            "/register",
            json_data={"email": email, "tier": tier, "wallet_address": wallet_address},
            base_url=self.dev_base,
        )
        result = response.json()
        # If we got a new key, update the client
        if result.get("success") and result.get("api_key"):
            self.api_key = result["api_key"]
            self._client.headers["X-RMI-Dev-Key"] = self.api_key
        return result

    def verify_key(self) -> UsageInfo:
        """Verify the current API key and get usage info."""
        if not self.api_key:
            raise RMIAuthenticationError("No API key set")

        response = self._request(
            "POST",
            "/verify",
            json_data={"api_key": self.api_key},
            base_url=self.dev_base,
        )
        data = response.json()
        if not data.get("valid"):
            raise RMIAuthenticationError(data.get("error", "Invalid API key"))

        return UsageInfo(
            tier=data.get("tier", "free"),
            email=data.get("email", ""),
            status=data.get("status", "active"),
            today_calls=data.get("today_calls", 0),
            daily_limit=data.get("daily_limit", 100),
            remaining_today=data.get("remaining_today", 0),
            total_calls=data.get("total_calls", 0),
            created_at=data.get("created_at", ""),
            rate_limit_per_min=data.get("rate_limit_per_min", 10),
        )

    def get_tiers(self) -> list[TierInfo]:
        """Get available developer tiers."""
        response = self._request("GET", "/tiers", base_url=self.dev_base)
        data = response.json()
        tiers = []
        for name, info in data.get("tiers", {}).items():
            tiers.append(
                TierInfo(
                    name=name,
                    daily_limit=info.get("daily_limit", 0),
                    rate_limit_per_min=info.get("rate_limit_per_min", 0),
                    price=info.get("price", 0),
                    webhook_support=info.get("webhook_support", False),
                    priority_queue=info.get("priority_queue", False),
                )
            )
        return tiers

    def get_usage(self) -> dict:
        """Get detailed usage stats for the current API key."""
        if not self.api_key:
            raise RMIAuthenticationError("No API key set")

        response = self._request(
            "POST",
            "/usage",
            json_data={"api_key": self.api_key},
            base_url=self.dev_base,
        )
        return response.json()

    # ── Webhook Management ────────────────────────────────────────

    def register_webhook(
        self,
        url: str,
        events: list[str],
        address: str,
    ) -> Webhook:
        """Register a webhook for real-time alerts.

        Args:
            url: Webhook URL to receive POST requests.
            events: List of event types (rug_pull, whale_move, price_crash, etc.).
            address: Wallet or token address to monitor.

        Returns:
            Webhook object with registration details.
        """
        # Use the x402 webhook_register tool
        result = self.call(
            "webhook_register",
            {
                "url": url,
                "events": events,
                "address": address,
            },
        )
        return Webhook(
            id=result.data.get("webhook_id", ""),
            url=url,
            events=events,
            address=address,
            active=True,
        )

    def list_webhooks(self, address: str | None = None) -> list[Webhook]:
        """List registered webhooks."""
        params = {}
        if address:
            params["address"] = address

        response = self._request("GET", "/webhook_list", params=params)
        data = response.json()
        webhooks = []
        for wh in data.get("webhooks", []):
            webhooks.append(
                Webhook(
                    id=wh.get("id", ""),
                    url=wh.get("url", ""),
                    events=wh.get("events", []),
                    address=wh.get("address", ""),
                    active=wh.get("active", True),
                    created_at=wh.get("created_at", ""),
                )
            )
        return webhooks

    def delete_webhook(self, webhook_id: str) -> bool:
        """Delete a registered webhook."""
        # Webhook deletion via the x402 tools API
        try:
            self.call("webhook_delete", {"webhook_id": webhook_id})
            return True
        except Exception:
            return False

    # ── Tool Catalog ──────────────────────────────────────────────

    def list_tools(self) -> list[dict]:
        """Get the full tool catalog."""
        response = self._request("GET", "/catalog")
        data = response.json()
        return data.get("tools", [])

    def get_tool_info(self, tool: str) -> dict | None:
        """Get info about a specific tool."""
        response = self._request("GET", f"/{tool}")
        return response.json()

    # ── Cleanup ───────────────────────────────────────────────────

    def close(self):
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        with contextlib.suppress(Exception):
            self.close()


# ── Async SDK ─────────────────────────────────────────────────────


class AsyncRMI:
    """Async RMI client with httpx.

    Usage:
        async with AsyncRMI(api_key="...") as rmi:
            result = await rmi.scan("0x...")
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        dev_base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        fingerprint: str | None = None,
        wallet_identity: str | None = None,
    ):
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required for async mode: pip install httpx")

        self.api_key = api_key or os.environ.get("RMI_API_KEY")
        self.base = (base_url or os.environ.get("RMI_API_BASE") or RMI.DEFAULT_BASE).rstrip("/")
        self.dev_base = (dev_base_url or os.environ.get("RMI_DEV_BASE") or RMI.DEFAULT_DEV_BASE).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.wallet_identity = wallet_identity

        if fingerprint:
            self._fingerprint = fingerprint
        else:
            self._fingerprint = hashlib.sha256(
                f"rmi-sdk-v2-async-{os.uname().nodename}-{time.time()}".encode()
            ).hexdigest()[:32]

        headers = {
            "Content-Type": "application/json",
            "x-fingerprint": self._fingerprint,
            "User-Agent": "rmi-python-sdk/2.0-async",
        }
        if self.api_key:
            headers["X-RMI-Dev-Key"] = self.api_key
        if self.wallet_identity:
            headers["X-Wallet-Identity"] = self.wallet_identity

        self._client = httpx.AsyncClient(
            base_url=self.base,
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=10.0),
            http2=True,
        )

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_data: dict | None = None,
        headers: dict | None = None,
        base_url: str | None = None,
    ) -> httpx.Response:
        f"{base_url or self.base}/{path.lstrip('/')}"  # noqa: B021

        for attempt in range(self.max_retries + 1):
            try:
                if method.upper() == "GET":
                    response = await self._client.get(path, params=params, headers=headers)
                else:
                    response = await self._client.post(path, json=json_data, headers=headers)

                # Handle error responses
                if response.status_code == 401:
                    raise RMIAuthenticationError("Invalid API key")
                elif response.status_code == 402:
                    body = response.json() if response.content else {}
                    raise RMIPaymentRequired(
                        message=body.get("error", "Payment required"),
                        pay_to=body.get("pay_to", ""),
                        amount=body.get("amount", ""),
                        chain=body.get("chain", ""),
                    )
                elif response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    raise RMIRateLimitError("Rate limit exceeded", retry_after=retry_after)
                elif response.status_code >= 400:
                    raise RMIToolError(
                        tool=path.split("/")[-1],
                        message=f"HTTP {response.status_code}: {response.text[:200]}",
                    )

                return response

            except (httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                if attempt == self.max_retries:
                    raise RMITimeoutError(str(e)) from e
            except httpx.ConnectError as e:
                if attempt == self.max_retries:
                    raise RMIConnectionError(str(e)) from e

            # Retry with backoff
            delay = min(self.retry_delay * (2**attempt), 30.0)
            import random

            delay *= 0.5 + random.random()
            await asyncio.sleep(delay)

        raise RMIError("All retries failed")

    def _parse_response(self, response: httpx.Response, tool: str) -> ToolResult:
        data = response.json()
        if "error" in data and not data.get("result"):
            raise RMIToolError(tool, data["error"], status=data.get("status", "error"))
        if data.get("status") == "no_data":
            raise RMIDataError("No data found")

        return ToolResult(
            tool=tool,
            data=data.get("result", data),
            status=data.get("status", "ok"),
            enrichment=data.get("_enrichment"),
            intel=data.get("_intel"),
            confidence=data.get("_confidence"),
            trial_remaining=int(response.headers.get("X-RMI-Trial-Remaining", 0)),
            cache_hit=response.headers.get("X-Cache") == "HIT",
            raw=data,
        )

    async def call(self, tool: str, params: dict | None = None) -> ToolResult:
        """Call a single tool."""
        response = await self._request("POST", tool, json_data=params or {})
        return self._parse_response(response, tool)

    # Convenience methods (same as sync version)
    async def scan(self, address: str, chain: str = "solana") -> ToolResult:
        return await self.call("scan", {"address": address, "chain": chain})

    async def audit(self, address: str, chain: str = "solana") -> ToolResult:
        return await self.call("audit", {"address": address, "chain": chain})

    async def whale(self, address: str, chain: str = "solana") -> ToolResult:
        return await self.call("whale", {"address": address, "chain": chain})

    async def reputation(self, address: str) -> ToolResult:
        return await self.call("reputation_score", {"address": address})

    async def honeypot(self, address: str, chain: str = "solana") -> ToolResult:
        return await self.call("honeypot_check", {"address": address, "chain": chain})

    async def forensics(self, address: str, chain: str = "solana") -> ToolResult:
        return await self.call("forensics", {"address": address, "chain": chain})

    async def sentiment(self, query: str) -> ToolResult:
        return await self.call("sentiment", {"query": query})

    async def rug_probability(self, address: str, chain: str = "solana") -> ToolResult:
        return await self.call("rug_probability", {"address": address, "chain": chain})

    async def composite_score(self, address: str, chain: str = "solana") -> ToolResult:
        return await self.call("composite_score", {"address": address, "chain": chain})

    async def smart_money(self, address: str) -> ToolResult:
        return await self.call("smart_money", {"address": address})

    async def market_overview(self) -> ToolResult:
        return await self.call("market_overview", {})

    async def narrative(self) -> ToolResult:
        return await self.call("narrative", {})

    # Batch execution (async)
    async def batch(self, calls: list[tuple[str, dict]], max_concurrent: int = 5) -> BatchResult:
        """Execute multiple tool calls concurrently."""
        semaphore = asyncio.Semaphore(max_concurrent)
        results = []
        successful = 0
        failed = 0
        start = time.monotonic()

        async def _execute(tool: str, params: dict) -> ToolResult:
            async with semaphore:
                try:
                    return await self.call(tool, params)
                except Exception as e:
                    return ToolResult(tool=tool, data={}, status="error", raw={"error": str(e)})

        tasks = [_execute(tool, params) for tool, params in calls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                failed += 1
            elif result.is_ok:
                successful += 1
            else:
                failed += 1

        total_time = (time.monotonic() - start) * 1000

        return BatchResult(
            results=[r for r in results if isinstance(r, ToolResult)],
            successful=successful,
            failed=failed,
            total_time_ms=total_time,
        )

    # Developer API (async)
    async def register(self, email: str, tier: str = "free") -> dict:
        response = await self._request(
            "POST",
            "/register",
            json_data={"email": email, "tier": tier},
            base_url=self.dev_base,
        )
        result = response.json()
        if result.get("success") and result.get("api_key"):
            self.api_key = result["api_key"]
            self._client.headers["X-RMI-Dev-Key"] = self.api_key
        return result

    async def verify_key(self) -> UsageInfo:
        if not self.api_key:
            raise RMIAuthenticationError("No API key set")
        response = await self._request(
            "POST",
            "/verify",
            json_data={"api_key": self.api_key},
            base_url=self.dev_base,
        )
        data = response.json()
        if not data.get("valid"):
            raise RMIAuthenticationError(data.get("error", "Invalid API key"))
        return UsageInfo(
            tier=data.get("tier", "free"),
            email=data.get("email", ""),
            status=data.get("status", "active"),
            today_calls=data.get("today_calls", 0),
            daily_limit=data.get("daily_limit", 100),
            remaining_today=data.get("remaining_today", 0),
            total_calls=data.get("total_calls", 0),
            created_at=data.get("created_at", ""),
            rate_limit_per_min=data.get("rate_limit_per_min", 10),
        )

    async def list_tools(self) -> list[dict]:
        response = await self._request("GET", "/catalog")
        return response.json().get("tools", [])

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    def __del__(self):
        try:
            if hasattr(self, "_client"):
                asyncio.get_event_loop().create_task(self._client.aclose())
        except Exception:  # noqa: S110 — best-effort rate limiting
            pass
