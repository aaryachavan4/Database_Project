"""
inspect_files.py - checks whether MariaDB table files are actually encrypted on disk.
 
It acts like an attacker who stole the disk: it copies the raw .ibd table files
out of the containers and searches them for real values taken from the tables
(names, emails, SSNs, ...). It compares the encrypted database against a plain,
unencrypted one holding the same customers, so a "0 matches" result means
something.
 
Results and hex dumps are written to security/before_after.txt.
 
Requirements:
  - Vault running and unsealed
  - container "mariadb"       : encrypted database (project setup)
  - container "mariadb-plain" : plain mariadb:latest with the customers table copied in
 
Usage (from the repo root):
  python3 security/inspect_files.py
"""
 
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
 
DB = "encryption_test"
PASSWORD = os.environ.get("MYSQL_ROOT_PASSWORD", "root_password")
 
ENCRYPTED = "mariadb"
PLAIN = "mariadb-plain"
 
ENCRYPTED_TABLES = ["customers", "accounts", "transactions"]
PLAIN_TABLES = ["customers"]  # the unencrypted control copy
 
SAMPLE_ROWS = 20   # rows per table used as search values
DUMP_BYTES = 128   # size of each hex dump in before_after.txt
OUT_FILE = Path(__file__).resolve().parent / "before_after.txt"
 
NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")
DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}")
 
 
def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
 
 
def query(container, sql):
    out = run(["docker", "exec", container, "mariadb", "-uroot", f"-p{PASSWORD}",
               "-N", "-B", DB, "-e", sql])
    return [line.split("\t") for line in out.splitlines() if line]
 
 
def restart_and_wait(containers):
    """A clean restart makes MariaDB write everything from memory to the .ibd files."""
    print(f"Restarting {', '.join(containers)} so all data is flushed to disk...")
    run(["docker", "restart", *containers])
    for c in containers:
        for _ in range(60):
            ping = subprocess.run(["docker", "exec", c, "mariadb-admin", "-uroot",
                                   f"-p{PASSWORD}", "ping"], capture_output=True)
            if ping.returncode == 0:
                break
            time.sleep(1)
        else:
            sys.exit(f"ERROR: {c} did not come back up. Is Vault running and unsealed?")
    print("Both containers are up.\n")
 
 
def search_values(table):
    """Take real text values from the table (skip numbers and dates, which
    InnoDB stores in binary, so they would never match as text anyway)."""
    values = set()
    for row in query(ENCRYPTED, f"SELECT * FROM {table} LIMIT {SAMPLE_ROWS}"):
        for v in row:
            if (len(v) >= 6 and v != "NULL" and "\\" not in v
                    and not NUMERIC.match(v) and not DATE_LIKE.match(v)):
                values.add(v)
    return sorted(values)
 
 
def copy_out(container, table, tmp):
    dest = Path(tmp) / f"{container}_{table}.ibd"
    run(["docker", "cp", f"{container}:/var/lib/mysql/{DB}/{table}.ibd", str(dest)])
    return dest.read_bytes()
 
 
def hexdump(data, start, length):
    lines = []
    for off in range(start, start + length, 16):
        chunk = data[off:off + 16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:08x}  {hex_part:<47}  {text}")
    return "\n".join(lines)
 
 
def main():
    restart_and_wait([ENCRYPTED, PLAIN])
 
    results = []   # (label, table, size, found, total)
    files = {}     # (container, table) -> bytes
 
    with tempfile.TemporaryDirectory() as tmp:
        for container, tables in [(ENCRYPTED, ENCRYPTED_TABLES), (PLAIN, PLAIN_TABLES)]:
            for table in tables:
                values = search_values(table)
                data = copy_out(container, table, tmp)
                files[(container, table)] = data
                found = [v for v in values if v.encode() in data]
                results.append((container, table, len(data), found, len(values)))
 
    # ---- summary ----
    lines = ["FILE INSPECTION RESULTS",
             f"Run at: {datetime.now():%Y-%m-%d %H:%M:%S}",
             f"Method: copied raw .ibd files out of each container and searched them for",
             f"        real text values from up to {SAMPLE_ROWS} rows of each table.",
             ""]
    leak = False
    control_ok = True
    for container, table, size, found, total in results:
        kind = "ENCRYPTED" if container == ENCRYPTED else "PLAIN    "
        if container == ENCRYPTED:
            status = "PASS (nothing readable)" if not found else "FAIL (data readable!)"
            leak |= bool(found)
        else:
            status = "readable, as expected" if found else "NOTHING FOUND (control failed)"
            control_ok &= bool(found)
        lines.append(f"[{kind}] {table + '.ibd':<18} {size / 1e6:7.1f} MB  "
                     f"{len(found):>3}/{total:<3} values found  -> {status}")
        for v in found[:5]:
            lines.append(f"            e.g. {v}")
    lines.append("")
 
    # ---- hex dumps at the same offset in both customer files ----
    plain = files.get((PLAIN, "customers"))
    enc = files.get((ENCRYPTED, "customers"))
    plain_hits = next((r[3] for r in results if r[0] == PLAIN and r[1] == "customers"), [])
    if plain and enc and plain_hits:
        offset = (plain.find(plain_hits[0].encode()) // 16) * 16
        lines += ["=" * 78,
                  f"BEFORE: plain customers.ibd, offset 0x{offset:x} (where '{plain_hits[0]}' appears)",
                  "=" * 78,
                  hexdump(plain, offset, DUMP_BYTES),
                  "",
                  "=" * 78,
                  f"AFTER: encrypted customers.ibd, same offset 0x{offset:x}",
                  "=" * 78,
                  hexdump(enc, offset, DUMP_BYTES),
                  ""]
 
    if leak:
        verdict = "VERDICT: FAIL - readable data found in an encrypted table file."
    elif not control_ok:
        verdict = "VERDICT: INCONCLUSIVE - the plain control file showed no data either."
    else:
        verdict = "VERDICT: PASS - all encrypted table files are unreadable; the plain copy leaks everything."
    lines.append(verdict)
 
    report = "\n".join(lines)
    print(report)
    OUT_FILE.write_text(report + "\n")
    print(f"\nSaved to {OUT_FILE}")
    sys.exit(1 if leak else 0)
 
 
if __name__ == "__main__":
    main()
 