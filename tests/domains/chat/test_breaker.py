"""Circuit-breaker unit tests — fake clock, no network."""

from __future__ import annotations

from app.domains.chat.breaker import CircuitBreaker


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_opens_after_three_failures_and_denies_calls():
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=3, open_seconds=300, clock=clock)
    for _ in range(2):
        breaker.record_failure("a")
    assert breaker.state("a") == "closed"
    assert breaker.allow("a") is True
    breaker.record_failure("a")
    assert breaker.state("a") == "open"
    assert breaker.allow("a") is False


def test_half_open_probe_closes_on_success_and_reopens_on_failure():
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=3, open_seconds=300, clock=clock)
    for _ in range(3):
        breaker.record_failure("a")

    clock.now += 301
    assert breaker.state("a") == "half_open"
    assert breaker.allow("a") is True  # probe slot claimed
    assert breaker.allow("a") is False  # only one probe at a time
    breaker.record_failure("a")
    assert breaker.state("a") == "open"
    assert breaker.allow("a") is False

    clock.now += 301
    assert breaker.allow("a") is True
    breaker.record_success("a")
    assert breaker.state("a") == "closed"
    assert breaker.allow("a") is True


def test_success_resets_the_failure_count():
    clock = FakeClock()
    breaker = CircuitBreaker(threshold=3, open_seconds=300, clock=clock)
    breaker.record_failure("a")
    breaker.record_failure("a")
    breaker.record_success("a")
    breaker.record_failure("a")
    breaker.record_failure("a")
    assert breaker.state("a") == "closed"


def test_reset_forgets_everything():
    breaker = CircuitBreaker(threshold=1, open_seconds=300)
    breaker.record_failure("a")
    assert breaker.state("a") == "open"
    breaker.reset()
    assert breaker.state("a") == "closed"
    assert breaker.allow("a") is True
