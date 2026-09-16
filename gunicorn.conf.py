"""Gunicorn config for rmi-backend.

2026-09-17: prometheus_client multiprocess mode requires marking worker
processes dead so their mmap files are cleaned up on recycle.
"""

import os


def child_exit(server, worker):
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(worker.pid)
