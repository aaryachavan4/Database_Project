#!/usr/bin/env python3
"""Compare baseline/encrypted benchmark CSV files and generate report + charts."""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PERFORMANCE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = PERFORMANCE_DIR / "results"
BASELINE_DEFAULT = RESULTS_DIR / "baseline_unencrypted.csv"
ENCRYPTED_DEFAULT = RESULTS_DIR / "encrypted.csv"


def load_csv(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Required benchmark file not found: {path}")

    with path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))

    if not rows:
        raise ValueError(f"Benchmark file is empty: {path}")

    data = {}
    for row in rows:
        query = row.get("query", "").strip()
        if not query:
            raise ValueError(f"Missing query name in {path}")
        if query in data:
            raise ValueError(f"Duplicate query '{query}' in {path}")
        data[query] = row
    return data


def number(row: dict[str, Any], field: str) -> float:
    try:
        return float(row[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid or missing numeric field '{field}': {row}") from exc


def calculate_overhead(baseline_ms: float, encrypted_ms: float) -> float:
    """Positive = slower with encryption; negative = faster in this observation."""
    if baseline_ms <= 0:
        raise ValueError("Baseline latency must be greater than zero.")
    return ((encrypted_ms - baseline_ms) / baseline_ms) * 100.0


def percentile(values: list[float], percentile_value: float) -> float:
    """Nearest-rank percentile helper, tested independently from database access."""
    import math

    if not values:
        raise ValueError("Cannot calculate a percentile from an empty sample.")
    if not 0 < percentile_value <= 100:
        raise ValueError("percentile_value must be in the interval (0, 100].")
    ordered = sorted(values)
    rank = max(1, math.ceil((percentile_value / 100) * len(ordered)))
    return ordered[rank - 1]


def validate_comparability(
    baseline: dict[str, dict[str, Any]],
    encrypted: dict[str, dict[str, Any]],
) -> list[str]:
    warnings = []
    if set(baseline) != set(encrypted):
        missing_encrypted = sorted(set(baseline) - set(encrypted))
        missing_baseline = sorted(set(encrypted) - set(baseline))
        raise ValueError(
            "The benchmark query sets differ. "
            f"Missing from encrypted: {missing_encrypted}; "
            f"missing from baseline: {missing_baseline}"
        )

    # Different row counts or primary-key sets mean the workloads are not equivalent.
    identity_fields = [
        "customers_rows", "accounts_rows", "transactions_rows",
        "customers_id_sha256", "accounts_id_sha256", "transactions_id_sha256",
    ]
    first_base = next(iter(baseline.values()))
    first_enc = next(iter(encrypted.values()))
    identity_mismatches = [
        field for field in identity_fields
        if first_base.get(field, "") != first_enc.get(field, "")
    ]
    if identity_mismatches:
        details = [
            f"{field}: baseline={first_base.get(field)!r}, encrypted={first_enc.get(field)!r}"
            for field in identity_mismatches
        ]
        raise ValueError(
            "Baseline and encrypted datasets are not equivalent by row count/primary-key set: "
            + "; ".join(details)
        )

    metadata_fields = ["server_version", "buffer_pool_size_bytes"]
    for field in metadata_fields:
        left = first_base.get(field, "")
        right = first_enc.get(field, "")
        if left != right:
            warnings.append(f"Metadata differs for {field}: baseline={left!r}, encrypted={right!r}")

    for query in sorted(baseline):
        for label, row in (("baseline", baseline[query]), ("encrypted", encrypted[query])):
            failures = int(float(row.get("failed_runs", 0) or 0))
            successful = int(float(row.get("successful_runs", row.get("runs", 0)) or 0))
            if failures:
                warnings.append(f"{label} {query} had {failures} failed execution(s).")
            if successful < 2:
                warnings.append(f"{label} {query} has fewer than two successful samples.")

    return warnings


def make_comparison(
    baseline: dict[str, dict[str, Any]],
    encrypted: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    comparison = []
    for query in baseline:
        base = baseline[query]
        enc = encrypted[query]
        base_avg = number(base, "avg_ms")
        enc_avg = number(enc, "avg_ms")
        base_median = number(base, "median_ms")
        enc_median = number(enc, "median_ms")

        comparison.append({
            "query": query,
            "baseline_avg_ms": round(base_avg, 6),
            "encrypted_avg_ms": round(enc_avg, 6),
            "avg_overhead_pct": round(calculate_overhead(base_avg, enc_avg), 4),
            "baseline_median_ms": round(base_median, 6),
            "encrypted_median_ms": round(enc_median, 6),
            "median_overhead_pct": round(calculate_overhead(base_median, enc_median), 4),
            "baseline_stdev_ms": round(number(base, "stdev_ms"), 6),
            "encrypted_stdev_ms": round(number(enc, "stdev_ms"), 6),
            "baseline_p95_ms": round(number(base, "p95_ms"), 6),
            "encrypted_p95_ms": round(number(enc, "p95_ms"), 6),
            "baseline_runs": int(float(base.get("successful_runs", base.get("runs", 0)))),
            "encrypted_runs": int(float(enc.get("successful_runs", enc.get("runs", 0)))),
        })
    return comparison


def write_comparison_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def generate_charts(rows: list[dict[str, Any]], output_dir: Path) -> list[Path]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError(
            "matplotlib is required to generate charts. "
            "Install it with: python -m pip install -r performance/requirements.txt"
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    query_names = [row["query"].replace("_", "\n") for row in rows]
    outputs = []

    # Chart 1: absolute latency comparison.
    fig, ax = plt.subplots(figsize=(13, 7))
    x = list(range(len(rows)))
    width = 0.38
    ax.bar([i - width / 2 for i in x],
           [row["baseline_avg_ms"] for row in rows],
           width, label="Unencrypted")
    ax.bar([i + width / 2 for i in x],
           [row["encrypted_avg_ms"] for row in rows],
           width, label="Encrypted")
    ax.set_ylabel("Average client-observed latency (ms)")
    ax.set_title("MariaDB query latency: unencrypted vs encrypted")
    ax.set_xticks(x)
    ax.set_xticklabels(query_names, rotation=0, fontsize=8)
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    path = output_dir / "charts.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    outputs.append(path)

    # Chart 2: percentage overhead.
    fig, ax = plt.subplots(figsize=(13, 6))
    overheads = [row["avg_overhead_pct"] for row in rows]
    ax.bar(query_names, overheads)
    ax.axhline(0, linewidth=1)
    ax.set_ylabel("Average latency change (%)")
    ax.set_title("Observed encryption overhead by query")
    ax.set_xticks(range(len(query_names)))
    ax.set_xticklabels(query_names, rotation=0, fontsize=8)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    path = output_dir / "overhead.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    outputs.append(path)

    # Chart 3: variability (standard deviation).
    fig, ax = plt.subplots(figsize=(13, 6))
    ax.bar([i - width / 2 for i in x],
           [row["baseline_stdev_ms"] for row in rows],
           width, label="Unencrypted")
    ax.bar([i + width / 2 for i in x],
           [row["encrypted_stdev_ms"] for row in rows],
           width, label="Encrypted")
    ax.set_ylabel("Standard deviation (ms)")
    ax.set_title("Run-to-run latency variability")
    ax.set_xticks(x)
    ax.set_xticklabels(query_names, fontsize=8)
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    path = output_dir / "variability.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    outputs.append(path)

    return outputs


def write_report(
    rows: list[dict[str, Any]],
    warnings: list[str],
    baseline: dict[str, dict[str, Any]],
    encrypted: dict[str, dict[str, Any]],
    path: Path,
) -> None:
    avg_overheads = [row["avg_overhead_pct"] for row in rows]
    total_base = sum(row["baseline_avg_ms"] for row in rows)
    total_enc = sum(row["encrypted_avg_ms"] for row in rows)
    weighted_overhead = calculate_overhead(total_base, total_enc)
    mean_query_overhead = statistics.mean(avg_overheads)
    highest = max(rows, key=lambda row: row["avg_overhead_pct"])
    lowest = min(rows, key=lambda row: row["avg_overhead_pct"])

    first_base = next(iter(baseline.values()))
    first_enc = next(iter(encrypted.values()))

    lines = [
        "# MariaDB Encryption Performance Analysis",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "## Objective",
        "",
        "Measure the observed query-latency difference between the unencrypted "
        "baseline and the MariaDB instance configured for data-at-rest encryption.",
        "",
        "## Environment and dataset",
        "",
        f"- Baseline MariaDB version: `{first_base.get('server_version', 'not recorded')}`",
        f"- Encrypted MariaDB version: `{first_enc.get('server_version', 'not recorded')}`",
        f"- Baseline `innodb_encrypt_tables`: `{first_base.get('innodb_encrypt_tables', 'not recorded')}`",
        f"- Encrypted `innodb_encrypt_tables`: `{first_enc.get('innodb_encrypt_tables', 'not recorded')}`",
        f"- Baseline buffer pool bytes: `{first_base.get('buffer_pool_size_bytes', 'not recorded')}`",
        f"- Encrypted buffer pool bytes: `{first_enc.get('buffer_pool_size_bytes', 'not recorded')}`",
        f"- Baseline row counts (customers/accounts/transactions): "
        f"`{first_base.get('customers_rows')}/{first_base.get('accounts_rows')}/{first_base.get('transactions_rows')}`",
        f"- Encrypted row counts (customers/accounts/transactions): "
        f"`{first_enc.get('customers_rows')}/{first_enc.get('accounts_rows')}/{first_enc.get('transactions_rows')}`",
        "",
        "## Methodology",
        "",
        "- Eight queries are executed against the project's actual schema.",
        "- Two warm-up executions are discarded by default.",
        "- Ten measured executions are collected per query by default.",
        "- Parameters are selected from existing primary keys using a fixed seed.",
        "- SELECT timing includes fetching the result set.",
        "- INSERT and UPDATE timing includes transaction commit.",
        "- Benchmark INSERT rows are deleted after the run.",
        "- Individual samples are retained in the source benchmark CSV files.",
        "",
        "## Results",
        "",
        "| Query | Baseline avg (ms) | Encrypted avg (ms) | Avg overhead | Median overhead |",
        "|---|---:|---:|---:|---:|",
    ]

    for row in rows:
        lines.append(
            f"| {row['query']} | {row['baseline_avg_ms']:.4f} | "
            f"{row['encrypted_avg_ms']:.4f} | {row['avg_overhead_pct']:.2f}% | "
            f"{row['median_overhead_pct']:.2f}% |"
        )

    lines.extend([
        "",
        "## Summary",
        "",
        f"- Mean of per-query average-latency overheads: **{mean_query_overhead:.2f}%**.",
        f"- Aggregate latency overhead across the listed query averages: **{weighted_overhead:.2f}%**.",
        f"- Highest observed overhead: **{highest['query']} ({highest['avg_overhead_pct']:.2f}%)**.",
        f"- Lowest observed overhead: **{lowest['query']} ({lowest['avg_overhead_pct']:.2f}%)**.",
        "",
        "A positive percentage means the encrypted run was slower for that query. "
        "A negative percentage means the encrypted run was faster in this measurement. "
        "Neither result alone proves that encryption caused the entire difference; "
        "cache state, background activity, storage, CPU load and run order can also affect latency.",
        "",
        "## Charts",
        "",
        "- `charts.png`: average latency comparison.",
        "- `overhead.png`: percentage latency change by query.",
        "- `variability.png`: standard deviation comparison.",
        "",
        "## Warnings and limitations",
        "",
    ])

    if warnings:
        lines.extend([f"- {warning}" for warning in warnings])
    else:
        lines.append("- No data-count, query-set or failed-run warnings were detected by the analysis script.")

    lines.extend([
        "- The benchmark measures client-observed latency, not isolated storage-device latency.",
        "- A global encryption setting is not sufficient proof that every target tablespace is encrypted. "
        "The MariaDB/Vault owners must independently verify table-level encryption status.",
        "- The comparison is valid only when the two instances use equivalent data, schema, indexes, "
        "MariaDB versions, buffer-pool settings and comparable hardware/runtime conditions.",
        "- Ten samples per query provide an initial comparison, not a high-confidence statistical study.",
        "- Run the experiment more than once and compare independent runs if time permits.",
        "",
        "## Conclusion",
        "",
        "Replace this paragraph with a short interpretation after reviewing the measured results. "
        "Explain which query types changed most, whether the observed differences are consistent, "
        "and what limitations affect the conclusion. Do not claim a fixed or expected overhead "
        "without evidence from the recorded measurements.",
        "",
    ])

    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare baseline and encrypted MariaDB results.")
    parser.add_argument("--baseline", type=Path, default=BASELINE_DEFAULT)
    parser.add_argument("--encrypted", type=Path, default=ENCRYPTED_DEFAULT)
    parser.add_argument("--output-dir", type=Path, default=PERFORMANCE_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        baseline = load_csv(args.baseline)
        encrypted = load_csv(args.encrypted)
        warnings = validate_comparability(baseline, encrypted)
        rows = make_comparison(baseline, encrypted)

        results_dir = args.output_dir / "results"
        write_comparison_csv(rows, results_dir / "comparison.csv")
        chart_paths = generate_charts(rows, args.output_dir)
        write_report(
            rows, warnings, baseline, encrypted,
            args.output_dir / "analysis.md",
        )

        avg_overhead = statistics.mean(row["avg_overhead_pct"] for row in rows)
        print("\n=== Encryption Performance Comparison ===")
        print(f"{'Query':<36} {'Baseline ms':>12} {'Encrypted ms':>14} {'Overhead':>11}")
        print("-" * 78)
        for row in rows:
            print(
                f"{row['query']:<36} {row['baseline_avg_ms']:>12.4f} "
                f"{row['encrypted_avg_ms']:>14.4f} {row['avg_overhead_pct']:>10.2f}%"
            )
        print("-" * 78)
        print(f"Mean per-query overhead: {avg_overhead:.2f}%")
        print(f"Comparison CSV: {results_dir / 'comparison.csv'}")
        print(f"Report: {args.output_dir / 'analysis.md'}")
        for chart in chart_paths:
            print(f"Chart: {chart}")
        for warning in warnings:
            print(f"WARNING: {warning}")
        return 0

    except Exception as exc:
        print(f"ANALYSIS FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
