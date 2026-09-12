"""Fuzz test configuration."""

import os

from hypothesis import settings

settings.register_profile("ci", max_examples=1000, deadline=None)
settings.register_profile("dev", max_examples=100, deadline=500)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
