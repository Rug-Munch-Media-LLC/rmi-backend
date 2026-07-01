"""Background jobs. Run separately from the API process.

- firehose:  continuous RAG ingestion
- scanner_queue: background token scan workers
- ingest_cron: scheduled data ingestion
- cleanup:    storage maintenance
"""
