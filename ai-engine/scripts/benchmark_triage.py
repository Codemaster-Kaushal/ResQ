#!/usr/bin/env python3
"""
RescueNet AI Engine — Triage Pipeline Benchmark
Uses 50 seeded reports from data/demo/reports.json.
Rule-based only (no Ollama required) for reliable, reproducible results.

Usage:
    python scripts/benchmark_triage.py
"""

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

# Make sure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_engine.pipeline import TriagePipeline
from shared.schemas.incident_ai import IncidentAIInput


DEMO_REPORTS_PATH = Path(__file__).parent.parent / "data" / "demo" / "reports.json"

# ── Load reports ──────────────────────────────────────────────────────────────


def load_reports() -> list[IncidentAIInput]:
    """Load and parse the demo reports JSON."""
    if not DEMO_REPORTS_PATH.exists():
        print(f"ERROR: {DEMO_REPORTS_PATH} not found. Run from project root.")
        sys.exit(1)

    with open(DEMO_REPORTS_PATH, encoding="utf-8") as f:
        raw = json.load(f)

    reports = []
    for item in raw:
        # Skip fields not in IncidentAIInput
        valid_keys = {
            "report_id", "description", "image",
            "latitude", "longitude", "client_timestamp", "reporter_pseudonym"
        }
        filtered = {k: v for k, v in item.items() if k in valid_keys}
        try:
            reports.append(IncidentAIInput(**filtered))
        except Exception as e:
            print(f"  [WARN] Skipped {item.get('report_id', '?')}: {e}")

    return reports


# ── Benchmark runner ──────────────────────────────────────────────────────────


async def run_benchmark(reports: list[IncidentAIInput]) -> dict:
    """
    Run the full TriagePipeline (rule-based only, no Ollama) for each report.
    Returns latency statistics.
    """
    # No provider = rule-based only → deterministic, fast, no network
    pipeline = TriagePipeline(provider=None, vision_provider=None)

    latencies_ms: list[float] = []
    errors = 0
    results_summary = []

    print(f"\nBenchmarking {len(reports)} reports (rule-based only, no Ollama)...")
    print("-" * 60)

    for i, report in enumerate(reports, 1):
        t0 = time.perf_counter()
        try:
            result = await pipeline.run(report)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies_ms.append(elapsed_ms)
            results_summary.append({
                "report_id": report.report_id,
                "incident_type": result.incident_type.value,
                "severity_label": result.severity_label.value,
                "severity_score": result.severity_score,
                "latency_ms": round(elapsed_ms, 2),
            })
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            errors += 1
            print(f"  [ERROR] {report.report_id}: {exc}")
            latencies_ms.append(elapsed_ms)

        # Progress indicator
        if i % 10 == 0 or i == len(reports):
            avg = statistics.mean(latencies_ms)
            print(f"  Progress: {i}/{len(reports)} — running avg: {avg:.2f}ms")

    return {
        "latencies_ms": latencies_ms,
        "errors": errors,
        "results_summary": results_summary,
    }


def print_results(data: dict, total_time: float) -> None:
    """Print a formatted benchmark results table."""
    latencies = data["latencies_ms"]
    n = len(latencies)
    errors = data["errors"]

    if not latencies:
        print("No results to display.")
        return

    sorted_lats = sorted(latencies)
    p50 = statistics.median(sorted_lats)
    p95 = sorted_lats[int(0.95 * n)]
    p99 = sorted_lats[int(0.99 * n)] if n >= 100 else sorted_lats[-1]
    avg = statistics.mean(latencies)
    std = statistics.stdev(latencies) if n > 1 else 0.0
    max_lat = max(latencies)
    min_lat = min(latencies)

    print("\n" + "=" * 60)
    print("  RESCUENET AI ENGINE — BENCHMARK RESULTS")
    print("=" * 60)
    print(f"  Reports processed : {n}")
    print(f"  Errors            : {errors}")
    print(f"  Total wall time   : {total_time:.2f}s")
    print(f"  Throughput        : {n / total_time:.1f} reports/sec")
    print()
    print("  LATENCY (per report, ms)")
    print(f"  +-- p50 (median)  : {p50:.2f} ms")
    print(f"  +-- p95           : {p95:.2f} ms")
    print(f"  +-- p99           : {p99:.2f} ms")
    print(f"  +-- Average       : {avg:.2f} ms")
    print(f"  +-- Std deviation : {std:.2f} ms")
    print(f"  +-- Min           : {min_lat:.2f} ms")
    print(f"  +-- Max           : {max_lat:.2f} ms")
    print()

    # Show first 10 results as sample
    print("  SAMPLE RESULTS (first 10)")
    print(f"  {'ID':<15} {'Type':<22} {'Severity':<10} {'Score':>5}  {'Lat(ms)':>8}")
    print(f"  {'-'*15} {'-'*22} {'-'*10} {'-'*5}  {'-'*8}")
    for r in data["results_summary"][:10]:
        print(
            f"  {r['report_id']:<15} {r['incident_type']:<22} "
            f"{r['severity_label']:<10} {r['severity_score']:>5}  "
            f"{r['latency_ms']:>8.2f}"
        )

    print("=" * 60)

    # Pass/fail SLA check (500ms per report for rule-based is very generous)
    sla_ms = 500
    sla_pass = p95 <= sla_ms
    print(f"\n  SLA CHECK (p95 <= {sla_ms}ms): {'[PASS]' if sla_pass else '[FAIL]'}")
    if not sla_pass:
        sys.exit(1)


async def main() -> None:
    reports = load_reports()
    if not reports:
        print("ERROR: No valid reports loaded.")
        sys.exit(1)

    wall_t0 = time.perf_counter()
    data = await run_benchmark(reports)
    wall_time = time.perf_counter() - wall_t0

    print_results(data, wall_time)


if __name__ == "__main__":
    asyncio.run(main())
