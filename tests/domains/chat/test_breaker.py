"""Circuit-breaker unit tests — fake clock, no network."""

from __future__ import annotations

from app.domains.chat.breaker import CircuitBreaker

class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Recorder:
    """Replaces request_provider; records order and plays a failure plan."""

    def __init__(self, fail_keys: set[str], error: ProviderError | None = None) -> None:
        self.fail_keys = fail_keys
        self.error = error or ProviderError("HTTP 500: down", retryable=False)
        self.calls: list[str] = []

    async def __call__(self, spec, messages, max_tokens):
        self.calls.append(spec.key)
        if spec.key in self.fail_keys:
            raise self.error
        return OK


@pytest.fixture(autouse=True)
def _fresh_breaker():
    BREAKER.reset()
    yield
    BREAKER.reset()


# ── breaker ────────────────────────────────────────────────────────────
def test_breaker_opens_after_three_failures_and_denies_calls():
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=3, open_seconds=300, clock=clock)
    for _ in range(2):
        breaker.record_failure("a")
    assert breaker.state("a") == "closed"
    assert breaker.allow("a") is True
    breaker.record_failure("a")
    assert breaker.state("a") == "open"
    assert breaker.allow("a") is False


def test_breaker_half_open_probe_closes_on_success_and_reopens_on_failure():
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=3, open_seconds=300, clock=clock)
    for _ in range(3):
        breaker.record_failure("a")
    clock.now += 301
    assert breaker.state("a") == "half_open"
    assert breaker.allow("a") is True          # probe slot claimed
    assert breaker.allow("a") is False         # only one probe at a time
    breaker.record_failure("a")
    assert breaker.state("a") == "open"
    assert breaker.allow("a") is False

    clock.now += 301
    assert breaker.allow("a") is True
    breaker.record_success("a")
    assert breaker.state("a") == "closed"
    assert breaker.allow("a") is True


def test_breaker_opens_after_three_failures_and_denies_calls():
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=3, open_seconds=300, clock=clock)
    for _ in range(2):
        breaker.record_failure("a")
    assert breaker.state("a") == "closed"
    assert breaker.allow("a") is True
    breaker.record_failure("a")
    assert breaker.state("a") == "open"
    assert breaker.allow("a") is False


def test_breaker_half_open_probe_closes_on_success_and_reopens_on_failure():
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=3, open_seconds=300, clock=clock)
    for _ in range(3):
        breaker.record_failure("a")
    clock.now += 301
    assert breaker.state("a") == "half_open"
    assert breaker.allow("a") is True          # probe slot claimed
    assert breaker.allow("a") is False         # only one probe at a time
    breaker.record_failure("a")
    assert breaker.state("a") == "open"
    assert breaker.allow("a") is False

    clock.now += 301
    assert breaker.allow("a") is True
    breaker.record_success("a")
    assert breaker.state("a") == "closed"
    assert breaker.allow("a") is True


