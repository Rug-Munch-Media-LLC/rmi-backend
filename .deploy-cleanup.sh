#!/bin/bash
# Run at start of every deploy to prevent stale bytecode
echo "Cleaning stale .pyc cache..."
find /root/backend -name "*.pyc" -delete
find /root/backend -name "__pycache__" -type d -empty -delete
echo "Done. $(date)"
