"""
Person 3 - Performance benchmark: unencrypted vs encrypted MariaDB.

Both servers hold the SAME data (customers 10k, accounts 50k, transactions 1M).
The script measures them side by side, in the same loop, so background noise on
the machine hits both equally:

  * every measured run alternates which server goes first
  * both servers get the exact same random ids (seeded per query + run)
  * 1 warm-up run is discarded, then --runs measured runs (default 10)
  * each run executes the query `reps` times; we keep every sample

Modes
  hot         data fits in the buffer pool (normal production case)
  cold        start both servers with a tiny buffer pool (e.g. 32M) first, so
              pages are read from disk and must be DECRYPTED on every read
  throughput  N concurrent clients for a fixed time (1 and 8 threads),
              point reads and single-row write transactions
  rebuild     ALTER TABLE transactions FORCE (= cost of (re)encrypting a
              1M-row table, what mariadb/encrypted-config.sql does)

Outputs (performance/results/)
  raw_<mode>.csv              one row per server x query x run
  baseline_unencrypted.csv    summary per query and mode, unencrypted server
  encrypted.csv               summary per query and mode, encrypted server
  environment_<mode>.json     server settings, row counts, encryption status

Usage (from the repo root; password comes from DB_PASSWORD or MYSQL_ROOT_PASSWORD):
  python performance/benchmark.py --mode hot
  python performance/benchmark.py --mode cold          # after restarting both with a small buffer pool
  python performance/benchmark.py --mode throughput
  python performance/benchmark.py --mode rebuild
  python performance/analysis.py
Defaults: baseline = 127.0.0.1:3306, encrypted = 127.0.0.1:3307.
"""
import argparse
import csv
import glob
import json
import math
import os
import platform
import random
import statistics
import threading
import time
from datetime import datetime

import pymysql

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
SERVERS = ("baseline", "encrypted")

# name, category, sql, kind, param generator, reps per run (hot, cold)
#   kind "read": fetch all rows; "write": commit after each statement
#   param: function(rng, ids) -> tuple of parameters, or None
QUERIES = [
    ("q1_count_customers", "small scan",
     "SELECT COUNT(*) FROM customers", "read", None, (50, 10)),
    ("q2_customer_by_pk", "point lookup",
     "SELECT * FROM customers WHERE customer_id = %s", "read",
     lambda r, ids: (r.randint(1, ids["customer"]),), (300, 100)),
    ("q3_accounts_of_customer", "index lookup",
     "SELECT * FROM accounts WHERE customer_id = %s", "read",
     lambda r, ids: (r.randint(1, ids["customer"]),), (300, 100)),
    ("q4_transactions_of_account", "index lookup",
     "SELECT * FROM transactions WHERE account_id = %s LIMIT 100", "read",
     lambda r, ids: (r.randint(1, ids["account"]),), (200, 50)),
    ("q5_sum_amount", "full scan 1M",
     "SELECT SUM(amount) FROM transactions", "read", None, (5, 3)),
    ("q6_join_txn_account", "join",
     "SELECT t.transaction_id, t.amount, a.account_type FROM transactions AS t "
     "JOIN accounts AS a ON t.account_id = a.account_id LIMIT 1000", "read", None, (50, 10)),
    ("q7_group_by_merchant", "aggregate 1M",
     "SELECT merchant_name, COUNT(*), SUM(amount) FROM transactions GROUP BY merchant_name",
     "read", None, (3, 2)),
    ("q8_count_distinct_desc", "temp files 1M",
     "SELECT COUNT(DISTINCT description) FROM transactions", "read", None, (2, 2)),
    ("w1_insert_commit", "write",
     "INSERT INTO transactions (account_id, amount, transaction_type, description, merchant_name, status) "
     "VALUES (%s, %s, 'Debit', 'benchmark row', 'BenchMerchant', 'Completed')", "write",
     lambda r, ids: (r.randint(1, ids["account"]), round(r.uniform(1, 500), 2)), (200, 100)),
    ("w2_update_by_pk", "write",
     "UPDATE transactions SET status = 'Completed' WHERE transaction_id = %s", "write",
     lambda r, ids: (r.randint(1, ids["transaction"]),), (200, 100)),
    ("w3_insert_batch_1000", "bulk write",
     "INSERT INTO transactions (account_id, amount, transaction_type, description, merchant_name, status) "
     "VALUES (%s, %s, 'Credit', 'benchmark row', 'BenchMerchant', 'Completed')", "batch",
     lambda r, ids: [(r.randint(1, ids["account"]), round(r.uniform(1, 500), 2)) for _ in range(1000)],
     (5, 3)),
]

# two-sided 95% t critical values, by degrees of freedom
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
       9: 2.262, 10: 2.228, 14: 2.145, 19: 2.093, 29: 2.045}


def t95(df):
    if df <= 0:
        return float("nan")
    return T95.get(df) or T95[min(T95, key=lambda k: abs(k - df))]


def parse_endpoint(s):
    host, _, port = s.rpartition(":")
    return host or "127.0.0.1", int(port)


def connect(endpoint, args):
    host, port = endpoint
    password = os.environ.get("DB_PASSWORD") or os.environ.get("MYSQL_ROOT_PASSWORD")
    if not password:
        raise SystemExit("Set DB_PASSWORD (or MYSQL_ROOT_PASSWORD) first.")
    try:
        return pymysql.connect(host=host, port=port, user=args.user, password=password,
                               database=args.db, autocommit=True, connect_timeout=10,
                               read_timeout=600, write_timeout=600)
    except pymysql.MySQLError as exc:
        raise SystemExit(f"Cannot connect to {host}:{port}: {exc}")


def scalar(cur, sql, params=None):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def describe_server(conn):
    """Settings, row counts and encryption status, saved next to the results."""
    cur = conn.cursor()
    info = {}
    cur.execute("SHOW GLOBAL VARIABLES WHERE Variable_name IN ("
                "'version','innodb_buffer_pool_size','innodb_encrypt_tables','innodb_encrypt_log',"
                "'innodb_encrypt_temporary_tables','encrypt_tmp_files','encrypt_tmp_disk_tables',"
                "'encrypt_binlog','log_bin','innodb_flush_log_at_trx_commit','innodb_encryption_threads',"
                "'innodb_flush_method','innodb_data_file_buffering','innodb_log_file_size')")
    info["variables"] = {k: v for k, v in cur.fetchall()}
    cur.execute("SELECT PLUGIN_NAME, PLUGIN_STATUS FROM information_schema.PLUGINS "
                "WHERE PLUGIN_TYPE = 'ENCRYPTION'")
    info["encryption_plugins"] = {k: v for k, v in cur.fetchall()}
    info["rows"] = {t: scalar(cur, f"SELECT COUNT(*) FROM {t}")
                    for t in ("customers", "accounts", "transactions")}
    cur.execute("SELECT NAME, ENCRYPTION_SCHEME, CURRENT_KEY_ID, CURRENT_KEY_VERSION "
                "FROM information_schema.INNODB_TABLESPACES_ENCRYPTION "
                "WHERE NAME LIKE 'encryption_test/%'")
    info["tablespace_encryption"] = {n: {"scheme": s, "key_id": k, "key_version": v}
                                     for n, s, k, v in cur.fetchall()}
    cur.execute("SELECT NAME, FILE_SIZE, ALLOCATED_SIZE FROM information_schema.INNODB_SYS_TABLESPACES "
                "WHERE NAME LIKE 'encryption_test/%'")
    info["tablespace_bytes"] = {n: {"file_size": int(f), "allocated": int(a)} for n, f, a in cur.fetchall()}
    cur.close()
    return info


def max_ids(conn):
    cur = conn.cursor()
    ids = {"customer": scalar(cur, "SELECT MAX(customer_id) FROM customers"),
           "account": scalar(cur, "SELECT MAX(account_id) FROM accounts"),
           "transaction": scalar(cur, "SELECT MAX(transaction_id) FROM transactions")}
    cur.close()
    return ids


def preload(conn):
    """Touch every table and index once so the hot run starts with a warm buffer pool."""
    cur = conn.cursor()
    for sql in ("SELECT COUNT(*) FROM customers", "SELECT SUM(LENGTH(address)) FROM customers",
                "SELECT COUNT(*) FROM accounts", "SELECT SUM(balance) FROM accounts",
                "SELECT SUM(amount) FROM transactions", "SELECT COUNT(*) FROM transactions FORCE INDEX (idx_account)",
                "SELECT COUNT(*) FROM transactions FORCE INDEX (idx_created)"):
        cur.execute(sql)
        cur.fetchall()
    cur.close()


def time_query(conn, cur, sql, kind, params):
    # autocommit=True: every statement is its own transaction, so a write's time
    # includes its commit (encrypted redo log + binlog write and fsync).
    start = time.perf_counter()
    if kind == "batch":
        cur.executemany(sql, params)  # pymysql sends ONE multi-row INSERT
    else:
        cur.execute(sql, params)
        if kind == "read":
            cur.fetchall()
    return (time.perf_counter() - start) * 1000.0


def cleanup_inserts(conn):
    cur = conn.cursor()
    cur.execute("DELETE FROM transactions WHERE description = 'benchmark row' AND merchant_name = 'BenchMerchant'")
    cur.close()


def percentile(values, p):
    s = sorted(values)
    if not s:
        return float("nan")
    k = (len(s) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


# --------------------------------------------------------------------------- latency (hot / cold)

def run_latency(args, conns, ids):
    rep_idx = 0 if args.mode == "hot" else 1
    raw = []           # one row per server x query x run
    samples = {}       # (server, query) -> every measured sample, for p95
    errors = {s: 0 for s in SERVERS}

    if args.mode == "hot":
        for s in SERVERS:
            print(f"  preloading buffer pool on {s} ...")
            preload(conns[s])

    total_runs = args.warmup + args.runs
    for run in range(total_runs):
        measured = run >= args.warmup
        order = SERVERS if run % 2 == 0 else SERVERS[::-1]
        label = f"run {run - args.warmup + 1}/{args.runs}" if measured else f"warm-up {run + 1}/{args.warmup}"
        print(f"  {label} (order: {order[0]} first)")
        for name, category, sql, kind, pgen, reps in QUERIES:
            if args.only and name not in args.only:
                continue
            n = reps[rep_idx] * args.rep_scale if pgen or kind != "read" else reps[rep_idx]
            n = max(1, int(n))
            if args.max_slow_reps and (pgen is None or kind == "batch"):
                n = min(n, args.max_slow_reps)  # quick mode: fewer repeats of the slow 1M-row queries
            # same seed for both servers -> identical ids
            rng_seed = f"{args.seed}-{args.mode}-{name}-{run}"
            for s in order:
                rng = random.Random(rng_seed)
                conn = conns[s]
                cur = conn.cursor()
                times = []
                for _ in range(n):
                    params = pgen(rng, ids) if pgen else None
                    try:
                        times.append(time_query(conn, cur, sql, kind, params))
                    except pymysql.MySQLError as exc:
                        errors[s] += 1
                        print(f"    ! {s} {name}: {exc}")
                cur.close()
                if kind in ("write", "batch"):
                    cleanup_inserts(conn)  # keep both data sets identical, not timed
                if measured and times:
                    samples.setdefault((s, name), []).extend(times)
                    raw.append({"server": s, "mode": args.mode, "query": name, "category": category,
                                "run": run - args.warmup + 1, "reps": len(times),
                                "mean_ms": round(statistics.mean(times), 4),
                                "median_ms": round(statistics.median(times), 4),
                                "p95_ms": round(percentile(times, 95), 4),
                                "errors": n - len(times)})
    return raw, samples, errors


def summarise(raw, samples):
    """One row per server x mode x query. Variance is computed across the measured runs."""
    out = []
    keys = sorted({(r["server"], r["mode"], r["query"], r["category"]) for r in raw},
                  key=lambda k: (k[0], k[1], [q[0] for q in QUERIES].index(k[2]) if k[2] in [q[0] for q in QUERIES] else 99))
    for server, mode, query, category in keys:
        rows = [r for r in raw if (r["server"], r["mode"], r["query"]) == (server, mode, query)]
        means = [float(r["mean_ms"]) for r in rows]
        n = len(means)
        avg = statistics.mean(means)
        sd = statistics.stdev(means) if n > 1 else 0.0
        allv = samples.get((server, query)) if samples else None
        p95 = percentile(allv, 95) if allv else statistics.mean(float(r["p95_ms"]) for r in rows)
        out.append({"mode": mode, "query": query, "category": category,
                    "avg_ms": round(avg, 4),
                    "median_ms": round(statistics.median(means), 4),
                    "min_ms": round(min(means), 4), "max_ms": round(max(means), 4),
                    "stdev_ms": round(sd, 4),
                    "variance_ms2": round(sd * sd, 6),
                    "cv_pct": round(sd / avg * 100, 2) if avg else 0.0,
                    "ci95_ms": round(t95(n - 1) * sd / math.sqrt(n), 4) if n > 1 else 0.0,
                    "p95_ms": round(p95, 4),
                    "runs": n,
                    "reps_per_run": int(statistics.mean(int(r["reps"]) for r in rows)),
                    "errors": sum(int(r.get("errors", 0)) for r in rows)})
    return out


# --------------------------------------------------------------------------- throughput

def throughput_worker(endpoint, args, kind, ids, seconds, seed, result, idx, start_evt):
    conn = connect(endpoint, args)
    cur = conn.cursor()
    rng = random.Random(seed)
    ops = errs = 0
    lat = []
    start_evt.wait()
    end = time.perf_counter() + seconds
    while True:
        t0 = time.perf_counter()
        if t0 >= end:
            break
        try:
            if kind == "read":
                cur.execute("SELECT * FROM transactions WHERE transaction_id = %s",
                            (rng.randint(1, ids["transaction"]),))
                cur.fetchall()
            else:
                cur.execute("INSERT INTO transactions (account_id, amount, transaction_type, description, "
                            "merchant_name, status) VALUES (%s, %s, 'Debit', 'benchmark row', 'BenchMerchant', "
                            "'Completed')", (rng.randint(1, ids["account"]), round(rng.uniform(1, 500), 2)))
            ops += 1
            lat.append((time.perf_counter() - t0) * 1000.0)
        except pymysql.MySQLError:
            errs += 1
    cur.close()
    conn.close()
    result[idx] = (ops, errs, lat)


def run_throughput(args, endpoints, conns, ids):
    raw = []
    for rnd in range(1, args.tp_rounds + 1):
        for kind in ("read", "write"):
            for threads in args.threads:
                order = SERVERS if rnd % 2 == 1 else SERVERS[::-1]
                for s in order:
                    result = [None] * threads
                    start_evt = threading.Event()
                    ts = [threading.Thread(target=throughput_worker,
                                           args=(endpoints[s], args, kind, ids, args.tp_seconds,
                                                 f"{args.seed}-{kind}-{threads}-{rnd}-{i}", result, i, start_evt))
                          for i in range(threads)]
                    for t in ts:
                        t.start()
                    time.sleep(0.5)  # let all threads connect
                    t0 = time.perf_counter()
                    start_evt.set()
                    for t in ts:
                        t.join()
                    elapsed = max(time.perf_counter() - t0, args.tp_seconds)
                    ops = sum(r[0] for r in result)
                    errs = sum(r[1] for r in result)
                    lat = [x for r in result for x in r[2]]
                    if kind == "write":
                        cleanup_inserts(conns[s])
                    row = {"server": s, "mode": "throughput", "query": f"tp_{kind}_{threads}thr",
                           "category": f"{kind} throughput", "run": rnd, "threads": threads,
                           "ops": ops, "errors": errs, "ops_per_sec": round(ops / elapsed, 1),
                           "mean_ms": round(statistics.mean(lat), 4) if lat else 0,
                           "p95_ms": round(percentile(lat, 95), 4) if lat else 0}
                    raw.append(row)
                    print(f"  round {rnd} {s:<9} {kind:<5} {threads} thr: {row['ops_per_sec']:>9.1f} ops/s  "
                          f"mean {row['mean_ms']:.3f} ms  p95 {row['p95_ms']:.3f} ms  errors {errs}")
    return raw


# --------------------------------------------------------------------------- rebuild

def run_rebuild(args, conns):
    raw = []
    for rnd in range(1, args.runs + 1):
        order = SERVERS if rnd % 2 == 1 else SERVERS[::-1]
        for s in order:
            cur = conns[s].cursor()
            t0 = time.perf_counter()
            cur.execute("ALTER TABLE transactions FORCE")
            secs = time.perf_counter() - t0
            cur.close()
            raw.append({"server": s, "mode": "rebuild", "query": "alter_transactions_force",
                        "category": "table rebuild 1M", "run": rnd, "reps": 1,
                        "mean_ms": round(secs * 1000, 1), "median_ms": round(secs * 1000, 1),
                        "p95_ms": round(secs * 1000, 1), "errors": 0})
            print(f"  round {rnd} {s:<9} ALTER TABLE transactions FORCE: {secs:.2f} s")
    return raw


# --------------------------------------------------------------------------- files

def write_csv(path, rows, fields):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


SUMMARY_FIELDS = ["mode", "query", "category", "avg_ms", "median_ms", "min_ms", "max_ms", "stdev_ms",
                  "variance_ms2", "cv_pct", "ci95_ms", "p95_ms", "runs", "reps_per_run", "errors"]
RAW_FIELDS = ["server", "mode", "query", "category", "run", "reps", "mean_ms", "median_ms", "p95_ms", "errors"]
TP_FIELDS = ["server", "mode", "query", "category", "run", "threads", "ops", "errors", "ops_per_sec",
             "mean_ms", "p95_ms"]


def rebuild_summaries():
    """Recreate baseline_unencrypted.csv and encrypted.csv from every raw_*.csv present."""
    raw = []
    for path in sorted(glob.glob(os.path.join(RESULTS, "raw_*.csv"))):
        if path.endswith("raw_throughput.csv"):
            continue
        raw.extend(read_csv(path))
    mode_order = {"hot": 0, "cold": 1, "rebuild": 2}
    for server, fname in (("baseline", "baseline_unencrypted.csv"), ("encrypted", "encrypted.csv")):
        rows = summarise([r for r in raw if r["server"] == server], None)
        rows.sort(key=lambda r: mode_order.get(r["mode"], 9))
        write_csv(os.path.join(RESULTS, fname), rows, SUMMARY_FIELDS)
    print(f"Updated {os.path.join(RESULTS, 'baseline_unencrypted.csv')} and encrypted.csv")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", required=True, choices=["hot", "cold", "throughput", "rebuild"])
    p.add_argument("--baseline", default="127.0.0.1:3306", help="unencrypted server host:port")
    p.add_argument("--encrypted", default="127.0.0.1:3307", help="encrypted server host:port")
    p.add_argument("--user", default="root")
    p.add_argument("--db", default="encryption_test")
    p.add_argument("--runs", type=int, default=None, help="measured runs (default 10; 3 for rebuild)")
    p.add_argument("--warmup", type=int, default=1, help="discarded warm-up runs")
    p.add_argument("--rep-scale", type=float, default=1.0, help="scale reps of the fast queries")
    p.add_argument("--only", nargs="*", help="run only these query names")
    p.add_argument("--max-slow-reps", type=int, default=None,
                   help="cap repeats per run of the full-scan and batch queries (quick mode)")
    p.add_argument("--threads", type=int, nargs="*", default=[1, 8], help="throughput client counts")
    p.add_argument("--tp-seconds", type=int, default=15)
    p.add_argument("--tp-rounds", type=int, default=3)
    p.add_argument("--seed", default="42")
    args = p.parse_args()
    if args.runs is None:
        args.runs = 3 if args.mode == "rebuild" else 10

    endpoints = {"baseline": parse_endpoint(args.baseline), "encrypted": parse_endpoint(args.encrypted)}
    conns = {s: connect(endpoints[s], args) for s in SERVERS}

    # Sanity checks: both servers must hold the same data, and only one may be encrypted.
    env = {s: describe_server(conns[s]) for s in SERVERS}
    for s in SERVERS:
        v = env[s]["variables"]
        print(f"[{s}] {endpoints[s][0]}:{endpoints[s][1]}  MariaDB {v.get('version')}  "
              f"buffer_pool={int(v.get('innodb_buffer_pool_size', 0)) // 2**20}MB  "
              f"innodb_encrypt_tables={v.get('innodb_encrypt_tables')}  rows={env[s]['rows']}")
    if env["baseline"]["rows"] != env["encrypted"]["rows"]:
        print("WARNING: the two servers do not hold the same number of rows - results are not comparable.")
    if env["encrypted"]["variables"].get("innodb_encrypt_tables") == "OFF":
        print("WARNING: the 'encrypted' server has innodb_encrypt_tables=OFF.")
    if env["baseline"]["variables"].get("innodb_encrypt_tables") != "OFF":
        print("WARNING: the 'baseline' server has encryption enabled.")
    ids = max_ids(conns["baseline"])

    started = datetime.now()
    meta = {"mode": args.mode, "started": started.isoformat(timespec="seconds"),
            "machine": {"platform": platform.platform(), "processor": platform.processor(),
                        "python": platform.python_version(), "cpu_count": os.cpu_count()},
            "args": {k: v for k, v in vars(args).items()}, "servers": env}

    if args.mode in ("hot", "cold"):
        raw, samples, errors = run_latency(args, conns, ids)
        write_csv(os.path.join(RESULTS, f"raw_{args.mode}.csv"), raw, RAW_FIELDS)
        meta["errors"] = errors
        print(f"\n{'query':<28}{'baseline ms':>14}{'encrypted ms':>14}{'overhead':>10}")
        summ = {s: {r["query"]: r for r in summarise([x for x in raw if x["server"] == s], samples)}
                for s in SERVERS}
        for q in summ["baseline"]:
            b, e = summ["baseline"][q]["avg_ms"], summ["encrypted"][q]["avg_ms"]
            print(f"{q:<28}{b:>14.3f}{e:>14.3f}{(e - b) / b * 100:>9.1f}%")
    elif args.mode == "throughput":
        raw = run_throughput(args, endpoints, conns, ids)
        write_csv(os.path.join(RESULTS, "raw_throughput.csv"), raw, TP_FIELDS)
    else:
        raw = run_rebuild(args, conns)
        write_csv(os.path.join(RESULTS, "raw_rebuild.csv"), raw, RAW_FIELDS)
        meta["servers_after_rebuild"] = {s: describe_server(conns[s]) for s in SERVERS}

    meta["finished"] = datetime.now().isoformat(timespec="seconds")
    meta["duration_s"] = round((datetime.now() - started).total_seconds(), 1)
    with open(os.path.join(RESULTS, f"environment_{args.mode}.json"), "w") as f:
        json.dump(meta, f, indent=2, default=str)

    for c in conns.values():
        c.close()
    rebuild_summaries()
    print(f"Done in {meta['duration_s']} s")


if __name__ == "__main__":
    main()
