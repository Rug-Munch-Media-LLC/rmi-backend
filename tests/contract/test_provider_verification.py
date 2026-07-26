"""Pact provider verification: rmi-backend verifies it satisfies pryscraper's expectations.

This test fetches consumer contracts (pacts) from the Pact Broker and replays each
request against the running rmi-backend instance. If any response differs from what
the consumer (pryscraper) expects, the test fails — catching breakage before deploy.

Run: PACT_BROKER_URL=http://localhost:9292 python -m pytest tests/contract/ -v --tb=short -m integration
"""
import os
import subprocess

import pytest


@pytest.mark.integration
def test_verify_pacts_against_provider():
    """Verify that rmi-backend satisfies all consumer contracts from the Pact Broker."""
    broker_url = os.environ.get("PACT_BROKER_URL", "http://localhost:9292")

    # This runs the pact verifier which:
    # 1. Fetches all contracts for rmi-backend from the broker
    # 2. Replays each request against the running rmi-backend
    # 3. Validates that responses match consumer expectations
    result = subprocess.run([
        "pact-verifier",
        "--provider-base-url", "http://localhost:8000",
        "--pact-broker-base-url", broker_url,
        "--provider", "rmi-backend",
        "--provider-app-version", os.environ.get("CI_COMMIT_SHA", "local"),
        "--publish-verification-results",
    ], capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        pytest.fail(f"Pact verification failed:\n{result.stderr}")
