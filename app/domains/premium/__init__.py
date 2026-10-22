"""Premium scanner features — watch diffs, bundle graph, cohorts, export.

Each module here is deliberately pure where it decides something (a diff, a
cluster, a rollup) and thin where it stores (Redis watches, the ``alerts``
table). A pure decision is a unit test with real numbers; the storage is one
call away from the canonical store that already exists.
"""
