#!/usr/bin/env python3
"""
vault_failure_test.py - what happens to the encrypted MariaDB when Vault fails?

Vault is never stopped or sealed (a restarted Vault comes back sealed and needs
its unseal keys). Failures are simulated in two safe, reversible ways:
  - "refused":     MariaDB's Vault policy is temporarily set to deny everything
                   (like a revoked token or a permissions mistake)
  - "unreachable": the MariaDB container is disconnected from the network,
                   so it can't reach Vault at all (like Vault crashing or a network outage)

Scenarios:
  A. Vault refuses access while MariaDB keeps running
  B. Vault unreachable while MariaDB keeps running
  C. MariaDB restarts while Vault refuses access
  D. Recovery: access restored, MariaDB restarted, data checked against the start

Each check runs reads on customers/accounts/transactions, an insert into a scratch
table, and creating a brand-new (encrypted) table. Queries go through
`docker exec`, so they still work while the container's network is cut.

Everything is put back in a finally-block, even if the script crashes or you press Ctrl+C.
Results are written to security/failure_results.md.

Requirements:
  - Vault running, unsealed, and the vault CLI logged in with the root/owner token
  - container "mariadb" running with the project data loaded

Usage (from the repo root):
  python3 security/vault_failure_test.py
"""

import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

CONTAINER = "mariadb"
DB = "encryption_test"
PASSWORD = os.environ.get("MYSQL_ROOT_PASSWORD", "root_password")
SCRATCH = "vault_failure_scratch"
HOLD_SECONDS = 90      # how long to keep Vault "down" in A and B (longer than the plugin's default 60s key cache)
CHECK_EVERY = 30
OUT_FILE = Path(__file__).resolve().parent / "failure_results.md"
os.environ.setdefault("VAULT_ADDR", "http://127.0.0.1:8200")

GOOD_POLICY = '''path "mariadb/*" { capabilities = ["create", "read", "update", "delete", "list"] }
path "sys/mounts/mariadb/tune" { capabilities = ["read"] }
'''
DENY_POLICY = '''path "mariadb/*" { capabilities = ["deny"] }
path "sys/mounts/mariadb/tune" { capabilities = ["deny"] }
'''

state = {"policy_denied": False, "disconnected": []}


# ---------------------------------------------------------------- helpers

def sh(cmd, stdin=None, timeout=120):
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)


def q(sql):
    """Run SQL inside the container. Returns (ok, short message)."""
    try:
        r = sh(["docker", "exec", CONTAINER, "mariadb", "-uroot", f"-p{PASSWORD}",
                "-N", "-B", DB, "-e", sql], timeout=60)
    except subprocess.TimeoutExpired:
        return False, "timed out after 60s"
    out = (r.stdout if r.returncode == 0 else r.stderr).strip().replace("\n", " ")
    return r.returncode == 0, out[:160]


def set_policy(text):
    r = sh(["vault", "policy", "write", "mariadb-policy", "-"], stdin=text)
    if r.returncode != 0:
        raise RuntimeError(f"vault policy write failed: {r.stderr.strip()}")


def deny_vault():
    set_policy(DENY_POLICY)
    state["policy_denied"] = True


def allow_vault():
    set_policy(GOOD_POLICY)
    state["policy_denied"] = False


def container_networks():
    r = sh(["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}", CONTAINER])
    return list(json.loads(r.stdout or "{}").keys())


def disconnect_network():
    for net in container_networks():
        r = sh(["docker", "network", "disconnect", net, CONTAINER])
        if r.returncode == 0:
            state["disconnected"].append(net)


def reconnect_network():
    for net in list(state["disconnected"]):
        sh(["docker", "network", "connect", net, CONTAINER])
        state["disconnected"].remove(net)


def running():
    r = sh(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER])
    return r.stdout.strip() == "true"


def restart_and_wait(wait=60):
    """Restart the container. Returns (container running, server answering, seconds)."""
    t0 = time.time()
    sh(["docker", "restart", CONTAINER])
    while time.time() - t0 < wait:
        if running():
            ping = sh(["docker", "exec", CONTAINER, "mariadb-admin", "-uroot",
                       f"-p{PASSWORD}", "ping"])
            if ping.returncode == 0:
                return True, True, time.time() - t0
        time.sleep(2)
    return running(), False, time.time() - t0


def recent_logs(since, n=8):
    r = sh(["docker", "logs", "--since", since, CONTAINER])
    lines = (r.stdout + r.stderr).splitlines()
    hits = [l.strip() for l in lines
            if any(w in l.lower() for w in ("hashicorp", "error", "encrypt", "key"))]
    return hits[-n:]


new_table_counter = [0]


def run_checks():
    """One round of checks. Returns {check name: (ok, message)}."""
    new_table_counter[0] += 1
    new_table = f"vf_new_table_{new_table_counter[0]}"
    offset = random.randint(0, 900_000)
    checks = {
        "read customers": "SELECT COUNT(*) FROM customers WHERE customer_id <= 100",
        "read accounts": "SELECT COUNT(*) FROM (SELECT * FROM accounts LIMIT 100) t",
        "read transactions": f"SELECT COUNT(*) FROM (SELECT * FROM transactions LIMIT 100 OFFSET {offset}) t",
        "write (insert)": f"INSERT INTO {SCRATCH} (note) VALUES ('check {datetime.now():%H:%M:%S}')",
        "create new table": f"CREATE TABLE {new_table} (id INT PRIMARY KEY) ENGINE=InnoDB",
    }
    results = {name: q(sql) for name, sql in checks.items()}
    q(f"DROP TABLE IF EXISTS {new_table}")
    return results


def checksums():
    ok, out = q("CHECKSUM TABLE customers, accounts, transactions")
    return out if ok else f"ERROR: {out}"


def hold_and_check(label, seconds):
    rounds = []
    t0 = time.time()
    while True:
        elapsed = time.time() - t0
        res = run_checks()
        rounds.append((elapsed, res))
        fails = [n for n, (ok, _) in res.items() if not ok]
        print(f"  {elapsed:4.0f}s  " + ("all checks OK" if not fails else f"FAILED: {', '.join(fails)}"))
        if elapsed >= seconds:
            return rounds
        time.sleep(CHECK_EVERY)


# ---------------------------------------------------------------- report

def md_rounds(rounds):
    names = list(rounds[0][1].keys())
    out = ["| Time | " + " | ".join(names) + " |",
           "|---|" + "---|" * len(names)]
    for elapsed, res in rounds:
        out.append(f"| {elapsed:.0f}s | " + " | ".join("OK" if res[n][0] else "FAIL" for n in names) + " |")
    errors = {(n, res[n][1]) for _, res in rounds for n in names if not res[n][0]}
    if errors:
        out += ["", "Errors seen:"]
        out += [f"- **{n}**: `{msg}`" for n, msg in sorted(errors)]
    return out


# ---------------------------------------------------------------- main

def main():
    print("Checking setup...")
    if not running():
        sys.exit("ERROR: the 'mariadb' container isn't running.")
    if sh(["vault", "policy", "read", "mariadb-policy"]).returncode != 0:
        sys.exit("ERROR: can't read mariadb-policy from Vault. Is Vault unsealed and are you logged in as root?")
    plugin_vars = sh(["docker", "exec", CONTAINER, "mariadb", "-uroot", f"-p{PASSWORD}", "-N", "-B",
                      "-e", "SHOW GLOBAL VARIABLES WHERE Variable_name LIKE 'hashicorp%' "
                      "OR Variable_name LIKE 'innodb_encryption%'"]).stdout.strip()
    sums_start = checksums()
    ok, msg = q(f"CREATE TABLE IF NOT EXISTS {SCRATCH} (id INT AUTO_INCREMENT PRIMARY KEY, note VARCHAR(64))")
    if not ok:
        sys.exit(f"ERROR: can't create scratch table: {msg}")

    print("Baseline checks (Vault healthy)...")
    baseline = run_checks()
    if not all(ok for ok, _ in baseline.values()):
        sys.exit(f"ERROR: checks fail even with Vault healthy, fix that first: {baseline}")
    print("  all checks OK")

    report = {}
    try:
        # A
        print(f"\nScenario A: Vault REFUSES access, MariaDB keeps running ({HOLD_SECONDS}s)...")
        deny_vault()
        report["A"] = hold_and_check("A", HOLD_SECONDS)
        allow_vault()
        report["A_crashed"] = not running()
        if report["A_crashed"]:
            print("  MariaDB is DOWN after scenario A. Restarting it so B gets a fair test...")
            restart_and_wait(wait=90)

        # B
        print(f"\nScenario B: Vault UNREACHABLE (network cut), MariaDB keeps running ({HOLD_SECONDS}s)...")
        disconnect_network()
        report["B"] = hold_and_check("B", HOLD_SECONDS)
        reconnect_network()
        report["B_crashed"] = not running()
        if report["B_crashed"]:
            print("  MariaDB is DOWN after scenario B. Restarting it before C...")
            restart_and_wait(wait=90)

        # C
        print("\nScenario C: MariaDB RESTARTS while Vault refuses access...")
        deny_vault()
        since = str(int(time.time()))
        c_running, c_answering, c_secs = restart_and_wait()
        print(f"  container running: {c_running}, server answering: {c_answering}")
        c_checks = run_checks() if c_answering else None
        if c_checks:
            for n, (ok, msg) in c_checks.items():
                print(f"  {n:<18} {'OK' if ok else 'FAIL'}  {'' if ok else msg}")
        c_logs = recent_logs(since)
        report["C"] = (c_running, c_answering, c_secs, c_checks, c_logs)

    finally:
        # D - always put things back
        print("\nScenario D: restoring Vault access and network, restarting MariaDB...")
        allow_vault()
        reconnect_network()
        d_running, d_answering, d_secs = restart_and_wait(wait=90)
        print(f"  container running: {d_running}, server answering: {d_answering} ({d_secs:.0f}s)")

    d_checks = run_checks() if d_answering else None
    sums_end = checksums() if d_answering else "server not answering"
    q(f"DROP TABLE IF EXISTS {SCRATCH}")
    data_ok = sums_start == sums_end
    print(f"  data unchanged: {data_ok}")

    # ---- write report ----
    L = ["# Vault Failure Test Results", "",
         f"Run at: {datetime.now():%Y-%m-%d %H:%M:%S}", "",
         "Vault was never stopped or sealed. Failures were simulated by temporarily denying "
         "MariaDB's Vault policy (refused) or disconnecting the MariaDB container from the "
         "network (unreachable). Each check: reads on all three tables, an insert, and "
         "creating a new encrypted table.", "",
         "Plugin settings:", "```", plugin_vars or "n/a", "```", "",
         "## A. Vault refuses access, MariaDB running", ""]
    L += md_rounds(report["A"]) if "A" in report else ["Not run."]
    if report.get("A_crashed"):
        L += ["", "**MariaDB was not running after this scenario (it crashed or shut down).**"]
    L += ["", "## B. Vault unreachable, MariaDB running", ""]
    L += md_rounds(report["B"]) if "B" in report else ["Not run."]
    if report.get("B_crashed"):
        L += ["", "**MariaDB was not running after this scenario (it crashed or shut down).**"]
    L += ["", "## C. MariaDB restarts while Vault refuses access", ""]
    if "C" in report:
        c_running, c_answering, c_secs, c_checks, c_logs = report["C"]
        L += [f"- Container running after restart: **{c_running}**",
              f"- Server answering queries: **{c_answering}** (waited {c_secs:.0f}s)"]
        if c_checks:
            L += [""] + md_rounds([(0, c_checks)])
        if c_logs:
            L += ["", "Relevant MariaDB log lines:", "```"] + c_logs + ["```"]
    else:
        L += ["Not run."]
    L += ["", "## D. Recovery (access restored, MariaDB restarted)", "",
          f"- Server answering: **{d_answering}** after {d_secs:.0f}s"]
    if d_checks:
        L += [""] + md_rounds([(0, d_checks)])
    L += ["", f"- Data unchanged (CHECKSUM TABLE start vs end): **{'YES' if data_ok else 'NO'}**",
          f"  - start: `{sums_start}`", f"  - end: `{sums_end}`", ""]

    OUT_FILE.write_text("\n".join(L) + "\n")
    print(f"\nSaved to {OUT_FILE}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Vault access and network were restored by the cleanup step.")
        sys.exit(1)