"""
Benchmark MariaDB query latency (run once against the unencrypted instance,
once against the encrypted instance, then compare with analysis.py).

Usage examples (from the repo root):
  python performance/benchmark.py --label baseline  --port 3306 \
      --out performance/results/baseline_unencrypted.csv
  python performance/benchmark.py --label encrypted --port 3307 \
      --out performance/results/encrypted.csv

Password is read from the DB_PASSWORD env var (default: root_password).
"""
import argparse
import csv
import os
import random
import statistics
import time

import pymysql

# Row-count ranges produced by mariadb/load_data.py
MAX_CUSTOMER = 10_000
MAX_ACCOUNT = 50_000
MAX_TRANSACTION = 500_000

# (name, sql, kind, param_range)
#   kind: "read" = SELECT, "write" = statement that must be committed
#   param_range: (low, high) for a random integer parameter, or None
QUERIES = [
    ("q1_count_customers", "SELECT COUNT(*) FROM customers", "read", None),
    ("q2_customer_by_pk", "SELECT * FROM customers WHERE customer_id = %s", "read", (1, MAX_CUSTOMER)),
    ("q3_accounts_of_customer", "SELECT * FROM accounts WHERE customer_id = %s", "read", (1, MAX_CUSTOMER)),
    ("q4_transactions_of_account", "SELECT * FROM transactions WHERE account_id = %s LIMIT 100", "read", (1, MAX_ACCOUNT)),
    ("q5_sum_amount", "SELECT SUM(amount) FROM transactions", "read", None),
    ("q6_join_txn_account",
     "SELECT t.transaction_id, t.amount, a.account_type "
     "FROM transactions AS t JOIN accounts AS a ON t.account_id = a.account_id LIMIT 1000", "read", None),
    ("q7_insert_transaction",
     "INSERT INTO transactions (account_id, amount, transaction_type, description, merchant_name, status) "
     "VALUES (%s, 42.00, 'Debit', 'benchmark row', 'BenchMerchant', 'Completed')", "write", (1, MAX_ACCOUNT)),
    ("q8_update_transaction",
     "UPDATE transactions SET status = 'Completed' WHERE transaction_id = %s", "write", (1, MAX_TRANSACTION)),
]


def make_params(rng, param_range, n):
    """Pre-generate parameters so baseline and encrypted runs use identical values."""
    if param_range is None:
        return [None] * n
    return [rng.randint(*param_range) for _ in range(n)]


def run_one(cursor, conn, sql, kind, param):
    """Time one execution. SELECTs fetch all rows so result transfer is included."""
    start = time.perf_counter()
    if param is None:
        cursor.execute(sql)
    else:
        cursor.execute(sql, (param,))
    if kind == "read":
        cursor.fetchall()
    else:
        conn.commit()  # include commit (redo-log write/fsync) in write timings
    return (time.perf_counter() - start) * 1000.0  # ms


def run_benchmark(args):
    password = os.environ.get("DB_PASSWORD", "root_password")
    conn = pymysql.connect(host=args.host, port=args.port, user=args.user,
                           password=password, database=args.db, autocommit=False)
    cursor = conn.cursor()

    cursor.execute("SELECT @@innodb_buffer_pool_size, @@innodb_encrypt_tables, @@innodb_encrypt_log")
    pool, enc_tables, enc_log = cursor.fetchone()
    print(f"[{args.label}] {args.host}:{args.port}  buffer_pool={pool / 1024 / 1024:.0f}MB  "
          f"innodb_encrypt_tables={enc_tables}  innodb_encrypt_log={enc_log}")

    results = []
    inserted_ids = []

    for name, sql, kind, prange in QUERIES:
        # Same seed per query -> identical parameters in both runs
        rng = random.Random(f"{args.seed}-{name}")
        params = make_params(rng, prange, args.warmup + args.runs)
        times = []

        for i, param in enumerate(params):
            try:
                ms = run_one(cursor, conn, sql, kind, param)
                if name == "q7_insert_transaction":
                    inserted_ids.append(cursor.lastrowid)
            except Exception as exc:  # keep going, but report it
                conn.rollback()
                print(f"  ! {name} run {i}: {exc}")
                continue
            if i >= args.warmup:  # discard warm-up runs
                times.append(ms)

        if not times:
            continue
        avg = statistics.mean(times)
        sd = statistics.stdev(times) if len(times) > 1 else 0.0
        row = {
            "query": name,
            "avg_ms": round(avg, 4),
            "median_ms": round(statistics.median(times), 4),
            "min_ms": round(min(times), 4),
            "max_ms": round(max(times), 4),
            "stdev_ms": round(sd, 4),
            "cv_pct": round(sd / avg * 100, 2) if avg else 0.0,
            "runs": len(times),
        }
        results.append(row)
        print(f"  {name:<28} avg={avg:9.3f}ms  sd={sd:8.3f}  min={min(times):9.3f}  max={max(times):9.3f}")

    # Clean up benchmark inserts so both databases stay identical
    if inserted_ids:
        cursor.executemany("DELETE FROM transactions WHERE transaction_id = %s",
                           [(i,) for i in inserted_ids])
        conn.commit()
        print(f"  cleaned up {len(inserted_ids)} benchmark rows")

    cursor.close()
    conn.close()
    return results


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--label", required=True, help="baseline or encrypted (for log output)")
    p.add_argument("--out", required=True, help="CSV output path")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=3306)
    p.add_argument("--user", default="root")
    p.add_argument("--db", default="encryption_test")
    p.add_argument("--runs", type=int, default=10, help="measured runs per query")
    p.add_argument("--warmup", type=int, default=2, help="discarded warm-up runs per query")
    p.add_argument("--seed", default="42")
    args = p.parse_args()

    results = run_benchmark(args)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    fields = ["query", "avg_ms", "median_ms", "min_ms", "max_ms", "stdev_ms", "cv_pct", "runs"]
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)
    print(f"Saved {len(results)} rows to {args.out}")


if __name__ == "__main__":
    main()

