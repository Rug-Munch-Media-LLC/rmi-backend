"""Performance benchmark tests for RMI Backend.

Every test tracks execution time and asserts it's below a threshold.
Uses time.perf_counter() for cross-platform high-resolution timing.
Database benchmarks skip gracefully when services are unavailable.
"""
