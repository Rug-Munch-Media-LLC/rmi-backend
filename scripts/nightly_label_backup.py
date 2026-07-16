#!/usr/bin/env python3
"""
Nightly Wallet Label Backup & Sync
====================================
1. Validates clean label data integrity
2. Backs up to R2 cloud storage (with rotation)
3. Syncs clean labels into running Redis/ClickHouse
4. Logs freshness metrics
"""

import csv
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

DATA_DIR = os.environ.get("RMI_DATA_DIR", "/root/backend/data")
CLEAN_DIR = os.path.join(DATA_DIR, "wallet-labels-clean")
BACKUP_DIR = os.path.join(DATA_DIR, "wallet-labels-backups")
MANIFEST_PATH = os.path.join(CLEAN_DIR, "manifest.json")

# R2 config
R2_BUCKET = os.environ.get("R2_BUCKET", "rmi-backups")
R2_ENDPOINT = os.environ.get("R2_ENDPOINT", "")
R2_KEY = os.environ.get("R2_ACCESS_KEY", "")
R2_SECRET = os.environ.get("R2_SECRET_KEY", "")

MAX_BACKUPS = 30  # Keep 30 days of backups


def validate_labels():
    """Validate all clean label files for integrity."""
    print("Validating label data...")
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
        print(
            f"  {fname}: {count:,} rows, {dupes} dupes, {invalids} invalid - {results[fname]['status']}"
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
        print(f"  Rotated out: {oldest.name}")

    size_mb = os.path.getsize(archive_path) / (1024 * 1024)
    print(f"Backup created: {archive_path} ({size_mb:.1f} MB)")

    return archive_path


def upload_to_r2(archive_path: str) -> bool:
    """Upload backup to R2 cloud storage."""
    if not all([R2_ENDPOINT, R2_KEY, R2_SECRET]):
        print("R2 not configured - skipping cloud upload")
        return False

    try:
        import boto3

        s3 = boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT,
            aws_access_key_id=R2_KEY,
            aws_secret_access_key=R2_SECRET,
        )

        fname = os.path.basename(archive_path)
        s3.upload_file(
            archive_path,
            R2_BUCKET,
            f"wallet-labels/{fname}",
        )
        print(f"Uploaded to R2: wallet-labels/{fname}")
        return True
    except ImportError:
        print("boto3 not installed - skipping R2 upload")
        return False
    except Exception as e:
        print(f"R2 upload failed: {e}")
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
        print(f"Redis not available: {e}")
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

    print(f"Synced {synced:,} labels to Redis")
    return synced


def main():
    print(f"=== Wallet Label Nightly Backup - {datetime.now().isoformat()} ===\n")

    # 1. Validate
    validation = validate_labels()

    # 2. Backup
    archive = create_backup()

    # 3. Upload to cloud
    uploaded = upload_to_r2(archive)

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

    print("\n=== Done ===")
    print(f"Backup: {archive}")
    print(f"Cloud: {'✓' if uploaded else '✗'}")
    print(f"Redis: {synced:,} labels synced")

    return manifest


if __name__ == "__main__":
    main()
