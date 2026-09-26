"""Key namespace and tuning windows for the host-health incident state."""

from __future__ import annotations

import os

KEY_PREFIX = os.getenv("HEALTH_ALERT_STATE_PREFIX", "rmi:health:incident:")

# Generous ceiling so a key can never outlive a week of quiet alerts.
TTL_SECONDS = int(os.getenv("HEALTH_ALERT_STATE_TTL", "604800"))

# The ownership marker is a LEASE, deliberately not TTL_SECONDS. The watch tick
# is 300s, so a lease well under one tick lets every tick publish while still
# stopping sibling workers from publishing the same sample twice. Reusing the
# 7-day incident TTL here made the FIRST tick win the key for a week: the plane
# published exactly one message ever and the debounce logic below it never ran.
LEASE_SECONDS = int(os.getenv("HEALTH_ALERT_LEASE_SECONDS", "120"))

# The ownership marker's namespace is versioned. v1 was written with the 7-day
# incident TTL and never released, so it locked the plane out for a week — and
# simply shortening the lease does not help, because the stale v1 key is still
# there holding the claim. Versioning orphans the old lock instead of inheriting
# it, so the fix heals itself on the next tick with no manual redis poke.
MARKER_SUFFIX = ":seen:v2"
