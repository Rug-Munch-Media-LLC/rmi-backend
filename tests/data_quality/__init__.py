"""Data quality tests — schema consistency, migration integrity, data invariants.

These tests verify that the database schema matches ORM model expectations and
that migrations produce the expected table structure. They are informational by
default (marker: data_quality) and require a running PostgreSQL instance.
"""
