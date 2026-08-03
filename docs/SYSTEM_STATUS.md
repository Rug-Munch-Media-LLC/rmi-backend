# RugMunch Platform — System Status

> Last updated: 2026-08-03

## Quick Health
| System | Status | Key Metric |
|--------|--------|------------|
| RugBrain RAG | 🟢 | Infinity healthy, 4,380 Qdrant points |
| RugMunchScan | 🟢 | 535 tests, C1-C7 engines live |
| Lite Indexer | 🟢 | v1.3.0, 285M transfers, 14 chains |

## RugBrain RAG
- **Embedding**: Infinity bge-m3 (1024d), CPUs 0-11, 16GB RAM
- **Reranking**: Infinity bge-reranker-v2-m3
- **Vector Store**: Qdrant — 18 collections (threat_intel_v2 1,045pts, news_v2 1,058pts, wallet_v2 1,000pts, scam_intel_v2 600pts, scams_v2 300pts, scanner_v2 191pts, hacks_v2 100pts, research_v2 60pts, rmi_community 21pts, rmi_agent_memory 5pts, + 8 legacy empty)
- **Orchestration**: LangGraph agentic (classify→route→retrieve→rerank→synthesize→verify)
- **Ingestion**: Crawl4AI + pryscraper bridge, 5-min scheduler
- **Evaluation**: Ragas (lazy import, heuristic fallback)
- **Tracing**: Phoenix (Arize) OTEL, port 4319→4317
- **API**: /api/v1/rag/v2/{search,ingest,ask,evaluate,feedback}
- **Ollama**: REMOVED — Infinity is sole engine

## RugMunchScan
- **Capabilities**: C1 Wallet Flow ✅, C2 Holder Clusters ✅, C3 New Token Radar ✅, C4 Cross-Chain Entity 🟡v1, C5 Hype Score ✅, C6 Attack Detection ✅, C7 Deployer Fingerprint ✅
- **Universal Deployer**: Any platform, any chain
- **DataBus**: 115 provider chains, own-data-first
- **Scoring**: Explainable with per-dimension breakdowns
- **Telegram**: /scan, /wallet, /deployer, /hype, /compare, /investigate
- **Tests**: 535 passing

## Lite Chain Indexer
- **Version**: 1.3.0
- **Data**: 285M evm_transfers, 13M dex_events, 920K deployer_events, 1.35M Solana SPL
- **Chains**: 16 (ethereum, bsc, polygon, arbitrum, optimism, base, avalanche, gnosis, fantom, celo, scroll, linea, mantle, zksync, solana, tron)
- **Features**: Bloom pre-filter, lazy DEX indexing, enrichment stripping, hourly Parquet cold tier, reorg staging, balance reconciliation, wallet projections
- **Tests**: 157 passing, 3 skipped

## Infrastructure
- **Host**: Talos (netcup), 64GB RAM, 16 cores, 2TB disk
- **Services**: infinity-rag, rmi-backend, rmi-rag-api, rmi-qdrant, rmi-redis, rmi-postgres, rmi-clickhouse, rmi-neo4j, phoenix, langfuse
- **CPU**: Infinity 0-11, indexer/scanner 12-15
- **Disk**: 28% used (546G/2.0T)
- **Memory**: 31Gi available (62Gi total)
