"""WebSocket keepalive frame parsing (``is_ping``).

Regression: a client frame that is neither ``ping`` nor valid JSON used to
raise ``json.JSONDecodeError`` out of the receive loop, killing the socket.
Malformed frames must be treated as non-pings.
"""

import pytest

from app.databus.ws_stream import is_ping


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ping", True),
        ("  ping  ", True),
        ('{"type": "ping"}', True),
        ('{"type": "ping", "ts": 1}', True),
        ('{"type": "pong"}', False),
        ('{"type": "subscribe"}', False),
        ("not json at all", False),
        ("", False),
        ("[]", False),
        ('"ping"', False),
        ("{}", False),
    ],
)
def test_is_ping(raw: str, expected: bool) -> None:
    assert is_ping(raw) is expected
