#!/usr/bin/env python3
"""
Nightly Wallet Label Backup & Sync
====================================
1. Validates clean label data integrity
2. Backs up to B2 cloud storage (with rotation)
3. Syncs clean labels into running Redis/ClickHouse
4. Logs freshness metrics

Migrated from R2 to B2 2026-07-31
"""

import csv
import hashlib
import json
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("RMI_DATA_DIR", "/app/data")
CLEAN_DIR = os.path.join(DATA_DIR, "wallet-labels-clean")
BACKUP_DIR = os.path.join(DATA_DIR, "wallet-labels-backups")
MANIFEST_PATH = os.path.join(CLEAN_DIR, "manifest.json")

# B2 config (S3-compatible API)
B2_BUCKET = os.environ.get("B2_BUCKET", "rmi-backups")
B2_ENDPOINT = os.environ.get("B2_ENDPOINT", "https://s3.us-west-004.backblazeb2.com")
B2_KEY_ID = os.environ.get("B2_APPLICATION_KEY_ID", "")
B2_APP_KEY = os.environ.get("B2_APPLICATION_KEY", "")

MAX_BACKUPS = 30  # Keep 30 days of backups


def validate_labels():
    """Validate all clean label files for integrity."""
    logger.info("Validating label data...")
    results = {}

    for fname in ["wallet_labels_ethereum.csv", "wallet_labels_solana.csv"]:
        path = os.path.join(CLEAN_DIR, fname)
        if not os.path.exists(path):
            results[fname] = {"status": "MISSING"}
            continue

        count = 0
        dupes = 0
        invalids = 0
        seen = set()

        with open(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                addr = row.get("address", "")
                if not addr:
                    invalids += 1
                    continue
                if addr.lower() in seen:
                    dupes += 1
                seen.add(addr.lower())
                count += 1

        results[fname] = {
            "status": "OK" if dupes == 0 and invalids == 0 else "ISSUES",
            "count": count,
            "duplicates": dupes,
            "invalids": invalids,
            "unique": len(seen),
        }
        logger.info(
            "  %s: %s rows, %s dupes, %s invalid - %s", fname, f"{count:,}", dupes, invalids, results[fname]["status"]
        )

    # Validate manifest
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH) as f:
            manifest = json.load(f)
        results["manifest"] = {
            "generated": manifest.get("generated_at"),
            "total": manifest.get("total_labels"),
            "chains": manifest.get("chains"),
        }

    return results


def create_backup():
    """Create a timestamped backup of the clean label data."""
    os.makedirs(BACKUP_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"labels_{timestamp}")
    os.makedirs(backup_path, exist_ok=True)

    # Copy clean label files
    for fname in os.listdir(CLEAN_DIR):
        src = os.path.join(CLEAN_DIR, fname)
        dst = os.path.join(backup_path, fname)
        if os.path.isfile(src):
            shutil.copy2(src, dst)

    # Create checksums
    checksums = {}
    for fname in os.listdir(backup_path):
        fpath = os.path.join(backup_path, fname)
        with open(fpath, "rb") as f:
            checksums[fname] = hashlib.sha256(f.read()).hexdigest()

    with open(os.path.join(backup_path, "checksums.json"), "w") as f:
        json.dump(checksums, f, indent=2)

    # Create backup manifest
    backup_manifest = {
        "backup_time": datetime.now(UTC).isoformat(),
        "backup_id": timestamp,
        "files": os.listdir(backup_path),
        "checksums": checksums,
    }
    with open(os.path.join(backup_path, "backup_manifest.json"), "w") as f:
        json.dump(backup_manifest, f, indent=2)

    # Compress
    archive_path = f"{backup_path}.tar.gz"
    shutil.make_archive(backup_path, "gztar", BACKUP_DIR, f"labels_{timestamp}")

    # Cleanup uncompressed
    shutil.rmtree(backup_path)

    # Rotate old backups
    backups = sorted(Path(BACKUP_DIR).glob("labels_*.tar.gz"))
    while len(backups) > MAX_BACKUPS:
        oldest = backups.pop(0)
        oldest.unlink()
        logger.info("  Rotated out: %s", oldest.name)

    size_mb = os.path.getsize(archive_path) / (1024 * 1024)
    logger.info("Backup created: %s (%.1f MB)", archive_path, size_mb)

    return archive_path


def upload_to_b2(archive_path: str) -> bool:
    """Upload backup to B2 cloud storage (native B2 HTTP API via httpx)."""
    if not all([B2_ENDPOINT, B2_KEY_ID, B2_APP_KEY]):
        logger.warning("B2 not configured - skipping cloud upload")
        return False

    try:
        import base64
        from urllib.parse import quote

        import httpx

        # 1. Authorize account (native B2 API)
        basic = base64.b64encode(f"{B2_KEY_ID}:{B2_APP_KEY}".encode()).decode()
        auth_resp = httpx.post(
            "https://api.backblazeb2.com/b2api/v3/b2_authorize_account",
            headers={"Authorization": f"Basic {basic}"},
            timeout=30,
        )
        auth_resp.raise_for_status()
        auth = auth_resp.json()
        api_url = auth["apiUrl"]
        auth_token = auth["authorizationToken"]
        account_id = auth["accountId"]

        auth_headers = {"Authorization": auth_token}

        # 2. Resolve bucket ID by name
        buckets_resp = httpx.post(
            f"{api_url}/b2api/v3/b2_list_buckets",
            json={"accountId": account_id, "bucketName": B2_BUCKET},
            headers=auth_headers,
            timeout=30,
        )
        buckets_resp.raise_for_status()
        buckets = buckets_resp.json().get("buckets", [])
        bucket_id = next((b["bucketId"] for b in buckets if b["bucketName"] == B2_BUCKET), None)
        if not bucket_id:
            logger.error("B2 bucket not found: %s", B2_BUCKET)
            return False

        # 3. Get upload URL + token
        upload_resp = httpx.post(
            f"{api_url}/b2api/v3/b2_get_upload_url",
            json={"bucketId": bucket_id},
            headers=auth_headers,
            timeout=30,
        )
        upload_resp.raise_for_status()
        upload = upload_resp.json()
        upload_url = upload["uploadUrl"]
        upload_token = upload["authorizationToken"]

        # 4. Upload file
        fname = os.path.basename(archive_path)
        # B2 API requires SHA-1 for content verification (X-Bz-Content-Sha1) — not used for security
        sha1 = hashlib.sha1()  # noqa: S324
        with open(archive_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha1.update(chunk)

        with open(archive_path, "rb") as f:
            put_resp = httpx.post(
                upload_url,
                content=f,
                headers={
                    "Authorization": upload_token,
                    "X-Bz-File-Name": quote(f"wallet-labels/{fname}"),
                    "X-Bz-Content-Sha1": sha1.hexdigest(),
                    "Content-Type": "application/octet-stream",
                },
                timeout=300,
            )
        put_resp.raise_for_status()
        logger.info("Uploaded to B2: wallet-labels/%s", fname)
        return True
    except ImportError:
        logger.warning("httpx not installed - skipping B2 upload")
        return False
    except Exception as e:
        logger.error("B2 upload failed: %s", e)
        return False


def sync_to_redis():
    """Sync clean labels into Redis for fast lookups."""
    try:
        import redis

        r = redis.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            password=os.environ.get("REDIS_PASSWORD", ""),
            decode_responses=True,
        )
        r.ping()
    except Exception as e:
        logger.warning("Redis not available: %s", e)
        return 0

    synced = 0
    for chain_file in ["wallet_labels_ethereum.csv", "wallet_labels_solana.csv"]:
        path = os.path.join(CLEAN_DIR, chain_file)
        if not os.path.exists(path):
            continue

        pipe = r.pipeline()
        batch = 0
        with open(path) as f:
            for row in csv.DictReader(f):
                addr = row["address"].lower()
                key = f"wl:{addr}"
                pipe.hset(
                    key,
                    mapping={
                        "chain": row.get("chain", ""),
                        "source": row.get("source", ""),
                        "name": row.get("name", ""),
                        "entity_type": row.get("entity_type", ""),
                        "synced_at": datetime.now(UTC).isoformat(),
                    },
                )
                batch += 1
                if batch % 1000 == 0:
                    pipe.execute()
                    pipe = r.pipeline()
                    synced += batch
                    batch = 0

        if batch > 0:
            pipe.execute()
            synced += batch

    logger.info("Synced %s labels to Redis", f"{synced:,}")
    return synced


def main():
    logger.info("=== Wallet Label Nightly Backup - %s ===", datetime.now().isoformat())

    # 1. Validate
    validation = validate_labels()

    # 2. Backup
    archive = create_backup()

    # 3. Upload to cloud
    uploaded = upload_to_b2(archive)

    # 4. Sync to Redis
    synced = sync_to_redis()

    # 5. Update manifest
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "last_backup": str(archive),
        "cloud_uploaded": uploaded,
        "redis_synced": synced,
        "validation": validation,
    }
    with open(os.path.join(CLEAN_DIR, "nightly_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    logger.info("=== Done ===")
    logger.info("Backup: %s", archive)
    logger.info("Cloud: %s", "yes" if uploaded else "no")
    logger.info("Redis: %s labels synced", f"{synced:,}")

    return manifest


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
