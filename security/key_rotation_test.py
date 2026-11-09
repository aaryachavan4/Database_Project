#!/usr/bin/env python3
"""
key_rotation_test.py - rotates the MariaDB encryption key in Vault while the
database is under load, and checks that nothing breaks.

It follows the rotation procedure from the main README:
  1. write a new key version to Vault (mariadb/1)
  2. flush MariaDB's key cache so it picks up the new version
  3. turn on background encryption threads so existing data is re-encrypted
  4. wait until no table is still rotating

Finding from earlier runs: with innodb_encryption_rotate_key_age = 1, ONE
rotation does not re-encrypt existing data. Pages are only re-encrypted once
they are more than rotate_key_age versions behind. So the test runs in two steps:
  A. rotate once and watch for OBSERVE_SECONDS that the data stays on the old key
  B. rotate a second time and time how long full re-encryption takes

While that happens, worker threads keep reading from customers, accounts and
transactions and writing to a scratch table. The test then checks:
  - did any query fail during the rotation?
  - did query latency change (before / single rotation / re-encrypting / after)?
  - is every table now fully on the newest key version?
  - is the data unchanged (CHECKSUM TABLE before == after)?
  - were all rows written during the rotation actually saved?

Results are written to security/rotation_results.txt.

Requirements:
  - Vault running, unsealed, and the vault CLI logged in (root or owner token)
  - container "mariadb" running with the project data loaded
  - pip install pymysql

Usage (from the repo root):
  python3 security/key_rotation_test.py
"""

import json
import os
import random
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pymysql

DB = "encryption_test"
PASSWORD = os.environ.get("MYSQL_ROOT_PASSWORD", "root_password")
TABLES = ["customers", "accounts", "transactions"]
SCRATCH = "rotation_load_test"

READER_THREADS = 4
ENCRYPTION_THREADS = 4     # innodb_encryption_threads during the test
ROTATION_IOPS = 1000       # innodb_encryption_rotation_iops during the test (default is 100)
BASELINE_SECONDS = 15      # load before rotating
OBSERVE_SECONDS = 60       # watch after the first rotation (expect no re-encryption)
AFTER_SECONDS = 15         # load after rotation finished
TIMEOUT_SECONDS = 900      # give up waiting for re-encryption after this

OUT_FILE = Path(__file__).resolve().parent / "rotation_results.txt"
os.environ.setdefault("VAULT_ADDR", "http://127.0.0.1:8200")


# ---------------------------------------------------------------- helpers

def connect():
    return pymysql.connect(host="127.0.0.1", port=3306, user="root", password=PASSWORD,
                           database=DB, autocommit=True, connect_timeout=5,
                           read_timeout=60, write_timeout=60)


def sql(query, args=None):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(query, args)
            return cur.fetchall()
    finally:
        conn.close()


def vault(*args, stdin=None):
    return subprocess.run(["vault", *args], input=stdin, capture_output=True,
                          text=True, check=True).stdout


def vault_key_version():
    meta = json.loads(vault("kv", "metadata", "get", "-format=json", "-mount=mariadb", "1"))
    return int(meta["data"]["current_version"])


def rotate_key_in_vault():
    # "data=-" makes vault read the new key from stdin, so the key never
    # appears on the command line or in the process list.
    vault("kv", "put", "-mount=mariadb", "1", "data=-", stdin=secrets.token_hex(16))


def encryption_status():
    rows = sql("""SELECT NAME, MIN_KEY_VERSION, CURRENT_KEY_VERSION, ROTATING_OR_FLUSHING,
                         KEY_ROTATION_PAGE_NUMBER, KEY_ROTATION_MAX_PAGE_NUMBER
                  FROM information_schema.INNODB_TABLESPACES_ENCRYPTION
                  WHERE NAME IN %s""", ([f"{DB}/{t}" for t in TABLES],))
    return {r[0].split("/")[1]: {"min": r[1], "current": r[2], "rotating": r[3],
                                 "page": r[4], "max_page": r[5]} for r in rows}


def cached_key_versions():
    try:
        return sql("SELECT KEY_ID, KEY_VERSION FROM information_schema.HASHICORP_KEY_MANAGEMENT_CACHE")
    except Exception as e:
        return [("n/a", str(e))]


def checksums():
    return {name.split(".")[1]: value
            for name, value in sql(f"CHECKSUM TABLE {', '.join(TABLES)}")}


def primary_key(table):
    col = sql("""SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE
                 WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND CONSTRAINT_NAME='PRIMARY'
                 ORDER BY ORDINAL_POSITION LIMIT 1""", (DB, table))[0][0]
    lo, hi = sql(f"SELECT MIN(`{col}`), MAX(`{col}`) FROM `{table}`")[0]
    return col, lo, hi


# ---------------------------------------------------------------- load

class Load:
    def __init__(self, pks):
        self.pks = pks
        self.phase = "before"
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.samples = []      # (phase, kind, ms, ok)
        self.errors = []       # (phase, kind, message)
        self.written = 0

    def record(self, phase, kind, started, ok, err=None):
        ms = (time.perf_counter() - started) * 1000
        with self.lock:
            self.samples.append((phase, kind, ms, ok))
            if err is not None:
                self.errors.append((phase, kind, str(err)[:150]))

    def _loop(self, kind, make_query, on_success=None):
        conn = None
        while not self.stop.is_set():
            query, args = make_query()
            phase = self.phase
            started = time.perf_counter()
            try:
                if conn is None:
                    conn = connect()
                with conn.cursor() as cur:
                    cur.execute(query, args)
                    cur.fetchall()
                self.record(phase, kind, started, True)
                if on_success:
                    on_success()
            except Exception as e:
                self.record(phase, kind, started, False, e)
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
                time.sleep(0.2)
            if kind == "write":
                time.sleep(0.01)
        if conn:
            conn.close()

    def read_query(self):
        table = random.choice(TABLES)
        col, lo, hi = self.pks[table]
        x = random.randint(lo, hi)
        if random.random() < 0.9:
            return f"SELECT * FROM `{table}` WHERE `{col}` = %s", (x,)
        return f"SELECT COUNT(*) FROM `{table}` WHERE `{col}` BETWEEN %s AND %s", (x, x + 1000)

    def write_query(self):
        return f"INSERT INTO `{SCRATCH}` (payload) VALUES (%s)", (secrets.token_hex(8),)

    def count_write(self):
        with self.lock:
            self.written += 1

    def start(self):
        self.threads = [threading.Thread(target=self._loop, args=("read", self.read_query))
                        for _ in range(READER_THREADS)]
        self.threads.append(threading.Thread(target=self._loop,
                                             args=("write", self.write_query, self.count_write)))
        for t in self.threads:
            t.start()

    def finish(self):
        self.stop.set()
        for t in self.threads:
            t.join()

    def stats(self, phase, kind, seconds):
        ok = sorted(ms for p, k, ms, good in self.samples if p == phase and k == kind and good)
        bad = sum(1 for p, k, ms, good in self.samples if p == phase and k == kind and not good)
        if not ok:
            return f"{kind:<5}  ops: 0  errors: {bad}"
        avg = sum(ok) / len(ok)
        p95 = ok[int(0.95 * (len(ok) - 1))]
        return (f"{kind:<5}  ops: {len(ok):>7}  ({len(ok) / seconds:7.1f}/s)  "
                f"errors: {bad:>3}  avg: {avg:6.2f} ms  p95: {p95:6.2f} ms")


# ---------------------------------------------------------------- main

def main():
    print("Checking setup...")
    try:
        old_version = vault_key_version()
    except Exception as e:
        sys.exit(f"ERROR: can't read mariadb/1 from Vault. Is Vault unsealed and are you logged in?\n{e}")
    try:
        status_before = encryption_status()
    except Exception as e:
        sys.exit(f"ERROR: can't reach MariaDB on 127.0.0.1:3306. Is the 'mariadb' container running?\n{e}")
    old_threads = sql("SELECT @@innodb_encryption_threads")[0][0]
    old_iops = sql("SELECT @@innodb_encryption_rotation_iops")[0][0]
    pks = {t: primary_key(t) for t in TABLES}
    print(f"Vault key version now: {old_version}")

    print("Taking checksums of all tables (before)...")
    sums_before = checksums()

    sql(f"DROP TABLE IF EXISTS `{SCRATCH}`")
    sql(f"""CREATE TABLE `{SCRATCH}` (id INT AUTO_INCREMENT PRIMARY KEY,
            payload VARCHAR(32) NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")

    load = Load(pks)
    load.start()
    print(f"Load running ({READER_THREADS} readers + 1 writer). Baseline for {BASELINE_SECONDS}s...")
    time.sleep(BASELINE_SECONDS)

    key_age = sql("SELECT @@innodb_encryption_rotate_key_age")[0][0]
    sql(f"SET GLOBAL innodb_encryption_rotation_iops = {ROTATION_IOPS}")
    sql(f"SET GLOBAL innodb_encryption_threads = {ENCRYPTION_THREADS}")

    # ---- step A: one rotation, exactly as the README describes ----
    load.phase = "single"
    rotate_key_in_vault()
    single_version = vault_key_version()
    sql("FLUSH HASHICORP_KEY_MANAGEMENT_CACHE")
    print(f"\nStep A: one rotation, Vault key {old_version} -> {single_version}. "
          f"Watching for {OBSERVE_SECONDS}s (rotate_key_age = {key_age})...")
    time.sleep(OBSERVE_SECONDS)
    status_single = encryption_status()
    single_reencrypted = all(s["min"] >= single_version for s in status_single.values())
    for t, s in status_single.items():
        print(f"  {t:<14} data on v{s['min']}, MariaDB knows v{s['current']}")
    print("  -> data WAS re-encrypted" if single_reencrypted
          else "  -> data still on the OLD key after one rotation")

    # ---- step B: rotate again and time the re-encryption ----
    load.phase = "during"
    t0 = time.time()
    rotate_key_in_vault()
    new_version = vault_key_version()
    sql("FLUSH HASHICORP_KEY_MANAGEMENT_CACHE")
    print(f"\nStep B: second rotation, Vault key {single_version} -> {new_version}. "
          "Waiting for re-encryption...")

    timed_out = False
    last_print = 0
    while True:
        status = encryption_status()
        done = all(s["min"] >= new_version and not s["rotating"] for s in status.values())
        if done:
            break
        if time.time() - t0 > TIMEOUT_SECONDS:
            timed_out = True
            break
        if time.time() - last_print >= 5:
            parts = [f"{t}: v{s['min']}{' (rotating)' if s['rotating'] else ''}"
                     for t, s in status.items()]
            print(f"  {time.time() - t0:5.0f}s  " + ",  ".join(parts))
            last_print = time.time()
        time.sleep(1)
    rotation_seconds = time.time() - t0
    print(f"Re-encryption {'TIMED OUT' if timed_out else 'finished'} after {rotation_seconds:.1f}s")

    # ---- after ----
    load.phase = "after"
    print(f"Keeping load running for {AFTER_SECONDS}s after rotation...")
    time.sleep(AFTER_SECONDS)
    load.finish()

    status_after = encryption_status()
    cache_after = cached_key_versions()
    print("Taking checksums of all tables (after)...")
    sums_after = checksums()
    saved = sql(f"SELECT COUNT(*) FROM `{SCRATCH}`")[0][0]
    sql(f"DROP TABLE `{SCRATCH}`")
    sql(f"SET GLOBAL innodb_encryption_threads = {old_threads}")
    sql(f"SET GLOBAL innodb_encryption_rotation_iops = {old_iops}")

    # ---- report ----
    read_errors = sum(1 for p, k, ms, ok in load.samples if not ok)
    sums_ok = sums_before == sums_after
    writes_ok = saved == load.written
    all_rotated = all(s["min"] >= new_version for s in status_after.values())

    lines = ["KEY ROTATION UNDER LOAD - RESULTS",
             f"Run at: {datetime.now():%Y-%m-%d %H:%M:%S}",
             f"Load: {READER_THREADS} reader threads (random primary-key lookups + range counts on "
             f"{', '.join(TABLES)}), 1 writer thread (inserts into a scratch table)",
             f"Settings during rotation: innodb_encryption_threads={ENCRYPTION_THREADS}, "
             f"innodb_encryption_rotation_iops={ROTATION_IOPS}",
             "",
             f"innodb_encryption_rotate_key_age = {key_age}",
             "",
             f"STEP A - one rotation (README procedure), Vault key v{old_version} -> v{single_version}",
             f"  Waited {OBSERVE_SECONDS}s. Data re-encrypted: {'YES' if single_reencrypted else 'NO'}"]
    for t, s in status_single.items():
        lines.append(f"  {t:<14} data on v{s['min']}, MariaDB knows v{s['current']}")
    if not single_reencrypted:
        lines.append(f"  FINDING: after one rotation, existing data is still encrypted with key "
                     f"v{old_version}. Pages are only re-encrypted once they are more than "
                     f"{key_age} version(s) behind.")
    lines += ["",
              f"STEP B - second rotation, Vault key v{single_version} -> v{new_version}",
              f"  Re-encryption time: {rotation_seconds:.1f} s" + ("  (TIMED OUT)" if timed_out else ""),
              f"  Key cache after rotation (KEY_ID, KEY_VERSION): {list(cache_after)}",
              "",
              "Key version per table (oldest page version used):"]
    for t in TABLES:
        b, a = status_before.get(t, {}), status_after.get(t, {})
        lines.append(f"  {t:<14} start: v{b.get('min')}   end: v{a.get('min')}"
                     f"   {'OK' if a.get('min', 0) >= new_version else 'NOT ROTATED'}")
    lines += ["", "Query performance:"]
    for phase, secs in [("before", BASELINE_SECONDS), ("single", OBSERVE_SECONDS),
                        ("during", rotation_seconds), ("after", AFTER_SECONDS)]:
        lines.append(f"  [{phase:<6}] {load.stats(phase, 'read', secs)}")
        lines.append(f"  [{'':<6}] {load.stats(phase, 'write', secs)}")
    lines += ["",
              f"Data unchanged (CHECKSUM TABLE before == after): {'YES' if sums_ok else 'NO'}",
              f"  before: {sums_before}",
              f"  after:  {sums_after}",
              f"Rows written during test: {load.written}, rows found afterwards: {saved}"
              f"  {'OK' if writes_ok else 'MISMATCH'}",
              f"Failed queries: {read_errors}"]
    for phase, kind, msg in load.errors[:10]:
        lines.append(f"  [{phase}] {kind}: {msg}")
    lines.append("")

    passed = not timed_out and all_rotated and sums_ok and writes_ok and read_errors == 0
    if passed:
        lines.append("VERDICT: PASS - all data re-encrypted with the new key under load, "
                     "no failed queries, no data change.")
        if not single_reencrypted:
            lines.append(f"NOTE: this needed two rotations. A single rotation (as in the README) "
                         f"leaves existing data on the old key while rotate_key_age = {key_age}.")
    else:
        lines.append("VERDICT: FAIL - see the details above.")

    report = "\n".join(lines)
    print("\n" + report)
    OUT_FILE.write_text(report + "\n")
    print(f"\nSaved to {OUT_FILE}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()