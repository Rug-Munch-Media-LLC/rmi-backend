#!/usr/bin/env python3
"""Refresh TTL on existing RAG keys to match new persistence policy."""

import subprocess
import sys


def redis_cli(*args):
    result = subprocess.run(
        ["docker", "exec", "rmi-redis", "redis-cli", "-a", REDIS_PASS, *args],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def redis_eval(lua_script, numkeys, *keys_and_args):
    """Run a Lua script via EVAL. Returns stdout."""
    all_args = ["EVAL", lua_script, str(numkeys), *list(keys_and_args)]
    return redis_cli(*all_args)


# Get Redis password from container env
env_out = subprocess.run(
    ["docker", "exec", "rmi-backend", "env"], capture_output=True, text=True
).stdout
REDIS_PASS = ""
for line in env_out.splitlines():
    if line.startswith("REDIS_PASSWORD="):
        REDIS_PASS = line.split("=", 1)[1]
        break

if not REDIS_PASS:
    print("ERROR: Could not find REDIS_PASSWORD")
    sys.exit(1)

# TTL policy: 0 = persist (no expiry), N = seconds
TTL_POLICY = {
    "scam_patterns": 0,  # permanent
    "contract_audits": 0,  # permanent
    "transaction_patterns": 0,  # permanent
    "forensic_reports": 0,  # permanent
    "known_scams": 0,  # permanent (was 365d)
    "wallet_profiles": 86400 * 365,  # 1 year
    "market_intel": 86400 * 365,  # 1 year
    "token_analysis": 86400 * 90,  # 90 days
    "news_articles": 86400 * 30,  # 30 days
    "general": 86400 * 30,  # 30 days
}

total_scanned = 0
total_persisted = 0
total_refreshed = 0
errors = 0

# Lua script that processes keys in batches
# For PERSIST (ttl=0): only persist keys that have a TTL (not -1)
# For EXPIRE (ttl>0): only set expiry on keys that are persistent (-1) or have shorter TTL
LUA_BATCH = """
local collection = ARGV[1]
local desired_ttl = tonumber(ARGV[2])
local count = 0
local persist_count = 0
local refresh_count = 0

-- Use SCAN to iterate keys
local cursor = '0'
repeat
    local reply = redis.call('SCAN', cursor, 'MATCH', 'rag:' .. collection .. ':*', 'COUNT', 500)
    cursor = reply[1]
    local keys = reply[2]
    for i, key in ipairs(keys) do
        count = count + 1
        local current_ttl = redis.call('TTL', key)
        if desired_ttl == 0 then
            -- Make permanent: only if not already persistent (-1)
            if current_ttl ~= -1 then
                redis.call('PERSIST', key)
                persist_count = persist_count + 1
            end
        else
            -- Set expiry if currently persistent (-1) or TTL is shorter than desired
            if current_ttl == -1 then
                -- Already persistent, leave it (don't downgrade)
            elseif current_ttl < desired_ttl then
                redis.call('EXPIRE', key, desired_ttl)
                refresh_count = refresh_count + 1
            end
        end
    end
until cursor == '0'

return {count, persist_count, refresh_count}
"""

print("Refreshing RAG TTLs according to persistence policy...")
print()

for collection, desired_ttl in TTL_POLICY.items():
    result = redis_eval(LUA_BATCH, 0, collection, str(desired_ttl))
    # Result format: one integer per line (Lua array serializes as newline-separated)
    lines = [l.strip() for l in result.splitlines() if l.strip()]  # noqa: E741
    if len(lines) == 3:
        try:
            scanned = int(lines[0].lstrip("[").rstrip(","))
            persisted = int(lines[1].rstrip(","))
            refreshed = int(lines[2].rstrip("]"))
        except (ValueError, IndexError):
            print(f"  {collection}: unexpected result: {result}")
            errors += 1
            continue

        action = (
            f"TTL={0} (persistent)"
            if desired_ttl == 0
            else f"TTL={desired_ttl}s ({desired_ttl // 86400}d)"
        )
        print(
            f"  {collection}: {scanned} keys scanned, {persisted} persisted, {refreshed} refreshed [{action}]"
        )
        total_scanned += scanned
        total_persisted += persisted
        total_refreshed += refreshed
    else:
        print(f"  {collection}: unexpected result: {result}")
        errors += 1

print()
print(
    f"Done: {total_scanned} keys scanned, {total_persisted} persisted (TTL removed), {total_refreshed} TTLs refreshed"
)
if errors:
    print(f"Errors: {errors}")
