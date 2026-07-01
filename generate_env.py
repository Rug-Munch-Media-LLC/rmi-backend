#!/usr/bin/env python3
"""
RMI Backend — Environment Generator
====================================
Generates /root/backend/.env from .env.example + Hermes .env matching keys.
Also syncs from /root/.secrets/ where available.

Usage:
    python3 generate_env.py           # Generate .env, show diff
    python3 generate_env.py --force   # Overwrite existing .env
"""

import sys
from pathlib import Path

BACKEND_DIR = Path("/root/backend")
HERMES_ENV = Path("/root/.hermes/.env")
SECRETS_DIR = Path("/root/.secrets")
EXAMPLE = BACKEND_DIR / ".env.example"
TARGET = BACKEND_DIR / ".env"

# Known key mappings between Hermes .env and backend .env
KEY_MAP = {
    # Hermes key -> Backend key  (or same if one name)
    "TELEGRAM_BOT_TOKEN": "BOT_TOKEN",
}


def load_env_file(path: Path) -> dict:
    """Parse a .env file into a dict, handling basic quoting."""
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if val:
            env[key] = val
    return env


def get_secret(name: str) -> str:
    """Read a secret from /root/.secrets/"""
    path = SECRETS_DIR / name
    if path.exists():
        return path.read_text().strip()
    # Try with _api_key suffix
    path = SECRETS_DIR / f"{name}_api_key"
    if path.exists():
        return path.read_text().strip()
    return ""


def generate():
    hermes_env = load_env_file(HERMES_ENV)
    secrets_found = []

    # Read example template
    if not EXAMPLE.exists():
        print(f"ERROR: {EXAMPLE} not found")
        sys.exit(1)

    lines = EXAMPLE.read_text().splitlines()
    output = []
    filled = 0
    missing = 0

    for line in lines:
        if "=" not in line or line.strip().startswith("#"):
            output.append(line)
            continue

        key = line.split("=")[0].strip()
        val = ""

        # Check Hermes env first
        if hermes_env.get(key):
            val = hermes_env[key]
        elif key in KEY_MAP and KEY_MAP[key] in hermes_env:
            val = hermes_env[KEY_MAP[key]]

        # Check secrets
        if not val:
            secret_val = get_secret(key.lower())
            if secret_val:
                val = secret_val
                secrets_found.append(key)

        if val:
            output.append(f"{key}={val}")
            filled += 1
        else:
            output.append(line)
            missing += 1

    content = "\n".join(output) + "\n"

    # Show what we found
    print(f"\n{'=' * 60}")
    print("Generated .env from:")
    print(f"  Hermes .env: {len(hermes_env)} keys loaded")
    print(f"  Secrets dir: {len(secrets_found)} found ({', '.join(secrets_found) if secrets_found else 'none'})")
    print(f"  Result: {filled} filled, {missing} still empty")
    print(f"{'=' * 60}\n")

    if missing > 0:
        print(f"⚠️  {missing} variables still need values. Check {TARGET}")

    # Write
    if TARGET.exists() and "--force" not in sys.argv:
        print(f"\n{TARGET} already exists. Use --force to overwrite.")
        print("Showing diff instead:\n")
        existing = TARGET.read_text()
        import difflib

        diff = difflib.unified_diff(
            existing.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=str(TARGET),
            tofile="generated",
        )
        sys.stdout.writelines(diff)
    else:
        TARGET.write_text(content)
        print(f"✅ Written {TARGET}")


if __name__ == "__main__":
    generate()
