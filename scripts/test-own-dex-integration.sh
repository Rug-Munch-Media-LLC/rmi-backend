#!/bin/bash
# Quick test: does own_dex_data return data for a known token?
# Tests both ClickHouse path and fallback to DexScreener API.
set -euo pipefail

TOKEN="${1:-0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2}" # WETH
CHAIN="${2:-ethereum}"

echo "=== Own DEX Data Integration Test ==="
echo "Token: $TOKEN ($CHAIN)"
echo ""

# Step 1: Verify ClickHouse table exists and has data
echo "--- Step 1: ClickHouse table check ---"
docker exec rmi-clickhouse clickhouse-client --query "
  SELECT count() as total_rows
  FROM wallet_memory.dex_events
" 2>/dev/null || echo "TABLE_NOT_FOUND (but that's OK if no data yet)"

docker exec rmi-clickhouse clickhouse-client --query "
  SELECT chain, count() as rows, max(ingested_at) as last_ingested
  FROM wallet_memory.dex_events
  GROUP BY chain
  ORDER BY rows DESC
  LIMIT 10
" --format PrettyCompact 2>/dev/null || echo "QUERY_FAILED"

echo ""

# Step 2: Verify the table schema matches lite_indexer INSERT
echo "--- Step 2: Verify schema compatibility ---"
docker exec rmi-clickhouse clickhouse-client --query "
  SELECT name, type
  FROM system.columns
  WHERE database = 'wallet_memory' AND table = 'dex_events'
  ORDER BY position
" --format PrettyCompact 2>/dev/null

echo ""

# Step 3: Test own_dex_data module end-to-end
echo "--- Step 3: own_dex_data module test ---"
cd /srv/work/repos/rmi-backend
python3 -c "
import asyncio, json, os
os.environ['CH_HOST'] = '127.0.0.1'
os.environ['CH_PASSWORD'] = 'clickhouse_rmi_prod_2026'

from app.domains.scanner.engines.own_dex_data import get_own_dex_data

async def test():
    result = await get_own_dex_data('$TOKEN', '$CHAIN')
    summary = {
        'source': result.get('source'),
        'pairs': len(result.get('pairs', [])),
        'total_swaps_24h': result.get('metrics', {}).get('swap_count_24h', 0),
        'total_volume_24h': result.get('metrics', {}).get('total_volume_24h', 0),
    }
    print(json.dumps(summary, indent=2))

asyncio.run(test())
" 2>&1 | grep -v '^2026-.*\[info\].*$' || true

echo ""

# Step 4: Test scanner integration
echo "--- Step 4: Scanner integration (_fetch_dexscreener) ---"
cd /srv/work/repos/rmi-backend
python3 -c "
import asyncio, os
os.environ['PREFER_OWN_DATA'] = 'true'
os.environ['CH_HOST'] = '127.0.0.1'
os.environ['CH_PASSWORD'] = 'clickhouse_rmi_prod_2026'

from app.domains.scanner.modules._core import _fetch_dexscreener

async def test():
    result = await _fetch_dexscreener('$TOKEN', '$CHAIN')
    if result and result.get('pairs'):
        print('PREFER_OWN_DATA:', os.environ['PREFER_OWN_DATA'])
        print('Pairs returned:', len(result['pairs']))
        print('First pair:', json.dumps({
            'chainId': result['pairs'][0].get('chainId', ''),
            'dexId': result['pairs'][0].get('dexId', ''),
            'priceUsd': result['pairs'][0].get('priceUsd', ''),
        }))
    else:
        print('No pairs returned (DexScreener API unavailable?)')
        print('Result:', result)

import json
asyncio.run(test())
" 2>&1 | grep -v '^2026-.*\[info\].*$' | grep -v '^2026-.*\[debug\].*$' || true

echo ""
echo "=== Test Complete ==="
