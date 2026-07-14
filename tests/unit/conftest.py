"""Unit test fixtures - sets required env vars before any app imports.

The app.config module instantiates Settings at module level, which
requires JWT_SECRET to be set (min_length=32) and a valid environment.
"""

import os

# Set required env vars before any app import
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-key-thats-32-chars-long!")
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
