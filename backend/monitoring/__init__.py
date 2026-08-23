"""
Phase 9.5 — Monitoring Package
================================
Provides log reading, analytics aggregation, in-memory metrics tracking,
error catalog, and log export utilities for the Admin Dashboard.

Sub-modules:
  log_reader.py   — Parse and filter JSONL log files with pagination
  analytics.py    — Compute aggregated metrics from parsed logs on-demand
  metrics_store.py — Ephemeral in-memory server statistics
  exporter.py     — Stream filtered logs as JSON or CSV downloads
"""
