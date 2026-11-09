# Security Testing: Encryption and Key Management

Person 4 (Security Owner). This folder tests whether the MariaDB + HashiCorp Vault setup from `mariadb/` and `vault/` actually protects the data, and how it behaves during key rotation and Vault failures.

All tests were run on 2026-11-09 against the project setup (MariaDB in Docker, Vault on the host, 10,000 customers / 50,000 accounts / 1,000,000 transactions).

## Summary of findings

| # | Finding | Severity |
|---|---|---|
| 1 | Table files on disk are unreadable without the key. The same data unencrypted leaks names, emails, SSNs and addresses. | Confirms encryption works |
| 2 | Key rotation under load is safe: 0 failed queries, no data change, minimal slowdown. | Confirms rotation works |
| 3 | **One rotation does not re-encrypt existing data.** With `innodb_encryption_rotate_key_age = 1`, data stays on the old key until it is more than one version behind. | **High** if rotating because a key leaked |
| 4 | **MariaDB crashes if Vault refuses access while background encryption threads are running** (InnoDB assertion, signal 6). These threads are exactly what key rotation turns on. | **High** (availability) |
| 5 | With encryption threads off, MariaDB keeps serving reads and writes while Vault is down. Only operations that need a fresh key (creating a table) fail, and only when Vault actively refuses. | Good resilience |
| 6 | MariaDB refuses to start without Vault ("fails closed"). It recovers in ~2 s with identical data once Vault is back. | Good security behaviour |

## Requirements

- The project setup running (see the main `README.md`): Vault unsealed, `mariadb` container up, data loaded
- `vault` CLI logged in with the root/owner token, and `export VAULT_ADDR='http://127.0.0.1:8200'`
- `pip install pymysql`
- For `inspect_files.py` only: a second, unencrypted container for comparison:

```bash
docker run --name mariadb-plain -e MYSQL_ROOT_PASSWORD=root_password -p 3307:3306 -d mariadb:latest
sleep 20
docker exec mariadb-plain mariadb -uroot -proot_password -e "CREATE DATABASE encryption_test;"
docker exec mariadb mariadb-dump -uroot -proot_password encryption_test customers \
  | docker exec -i mariadb-plain mariadb -uroot -proot_password encryption_test
```

Run every script from the repo root, e.g. `python3 security/inspect_files.py`.

## Files

| File | What it is |
|---|---|
| `inspect_files.py` | Test 1: reads the raw table files like an attacker who stole the disk |
| `before_after.txt` | Test 1 results, with hex dumps of the plain vs encrypted file |
| `key_rotation_test.py` | Test 2: rotates the Vault key while the database is under load |
| `rotation_results.txt` | Test 2 results |
| `vault_failure_test.py` | Test 3: simulates Vault refusing access / being unreachable |
| `failure_results.md` | Test 3 results, encryption threads off (default) |
| `failure_results_threads4.md` | Test 3 results, encryption threads = 4 (the crash) |
| `crash_log_threads4.txt` | MariaDB log lines from the crash |

---

## Test 1: Is the data really encrypted on disk?

**Threat:** an attacker gets the database files without going through MariaDB (stolen disk, leaked backup, access to the server's file system). They don't need a password, they just open the files.

**Method:** `inspect_files.py` restarts both containers (so all data is flushed from memory to disk), copies the raw `.ibd` table files out with `docker cp`, and searches them for real values taken from the tables (names, emails, phone numbers, SSNs). The same customers are stored in an unencrypted MariaDB as a control, so "0 matches" means something.

**Results:**

| File | Size | Real values found |
|---|---|---|
| Encrypted `customers.ibd` | 3.1 MB | **0 / 77** |
| Encrypted `accounts.ibd` | 7.3 MB | **0 / 5** |
| Encrypted `transactions.ibd` | 151.0 MB | **0 / 32** |
| Plain `customers.ibd` (control) | 9.4 MB | **77 / 77** |

At the same offset in the file, the plain copy shows a phone number, an SSN and a full address in clear text, while the encrypted copy is random bytes (see `before_after.txt`).

**Notes:**
- MariaDB keeps recent changes in memory and writes them to disk later. Copying a file right after inserting data gave a false "0 matches" even on the plain database, so the script restarts both containers first.
- `accounts` only had 5 searchable text values because its columns are mostly numbers, which InnoDB stores in binary anyway.
- This protects data **at rest** only. Anyone who logs into MariaDB with valid credentials still sees everything in clear text.

## Test 2: Key rotation under load

**Why:** keys get rotated when they may have leaked, or on a schedule (common in compliance rules). The database is live, so rotation must not break running queries.

**Method:** `key_rotation_test.py` runs 4 reader threads (primary-key lookups and range counts on all three tables) and 1 writer thread (inserts into a scratch table) the whole time. It follows the rotation procedure from the main README: new key version in Vault, `FLUSH HASHICORP_KEY_MANAGEMENT_CACHE`, `innodb_encryption_threads = 4`, then waits until no table is rotating. Checksums of all three tables are compared before and after.

**First run (one rotation, v1 → v2):** MariaDB picked up key v2 (key cache showed version 2), but after 15 minutes every table was still on v1 and never started rotating. The test timed out. Queries were unaffected: 0 failures across ~13.8 million reads and ~84,000 writes.

**Manual check (second rotation, v2 → v3):** all three tables were re-encrypted to v3 within 30 seconds.

**Final run (two-step test, v3 → v4 → v5):**

| Step | Result |
|---|---|
| A. One rotation, v3 → v4, waited 60 s | Data **still on v3**, MariaDB knows v4 |
| B. Second rotation, v4 → v5 | All tables on v5. MariaDB reported completion after 1.1 s |

| Phase | Reads/s | Avg read | p95 read | Failed queries |
|---|---|---|---|---|
| Before rotation | 17,659 | 0.23 ms | 0.32 ms | 0 |
| After one rotation | 16,885 | 0.24 ms | 0.33 ms | 0 |
| During re-encryption | 14,114 | 0.28 ms | 0.45 ms | 0 |
| After | 15,618 | 0.25 ms | 0.37 ms | 0 |

- Checksums before and after: identical
- Rows written during the test: 8,501, rows found afterwards: 8,501

**Finding 3, one rotation is not enough:** `innodb_encryption_rotate_key_age = 1` means a page is only re-encrypted once its key is more than one version behind the latest. After a single rotation, existing data stays encrypted with the old key. If the reason for rotating is that the old key leaked, the data is **still readable with that leaked key** until a second rotation.

**Caveat:** the 1.1 s re-encryption time is what MariaDB reported. It is faster than the configured limit (`innodb_encryption_rotation_iops = 1000` for roughly 10,000 pages) would suggest, so treat it as MariaDB's own status, not as a benchmark.

## Test 3: What happens when Vault fails?

**Why:** the key lives only in Vault. If Vault goes down, does the database keep working, stop, or leak data?

**Method:** Vault was never stopped or sealed, because a restarted Vault comes back sealed and needs its unseal keys. Failures were simulated in two reversible ways:
- **Refused:** MariaDB's Vault policy temporarily set to `deny` (like a revoked token or a permissions mistake)
- **Unreachable:** the MariaDB container disconnected from the network (like Vault crashing or a network outage)

Every 30 seconds for 90 seconds, the script ran: reads on all three tables, an insert, and creating a new (encrypted) table. Queries go through `docker exec`, so they still work while the network is cut. Everything is restored at the end, even on errors.

**Results:**

| Scenario | Encryption threads = 4 | Encryption threads = 0 (default) |
|---|---|---|
| A. Vault refuses access, MariaDB running | **MariaDB crashed** within 30 s | Reads and writes OK. Creating a table fails after ~60 s |
| B. Vault unreachable, MariaDB running | Not tested (server already down) | **Everything OK** for 90 s, including new tables |
| C. MariaDB restarts while Vault refuses access | Does not start | Does not start |
| D. Access restored, MariaDB restarted | Back in 2 s, data identical | Back in 2 s, data identical |

**Finding 4, the crash:** with `innodb_encryption_threads = 4`, Vault answered `403 permission denied`, the plugin logged `Unable to get key data`, and two InnoDB threads hit the same assertion:

```
InnoDB: Assertion failure in file ./storage/innobase/fil/fil0crypt.cc line 538
InnoDB: Failing assertion: key_version != ENCRYPTION_KEY_VERSION_INVALID
mariadbd got signal 6
```

`fil0crypt.cc` is the background encryption/rotation code. With the threads off, the same scenario did not crash. So the risk is highest exactly during key rotation, which is when the threads are turned on and someone is working with Vault.

**Finding 5, refused vs unreachable:** the plugin has `use_cache_on_timeout = ON`, so when Vault is *unreachable* MariaDB keeps using its cached key and everything works. A clear *"permission denied"* is not a timeout, so once the key version cache expires (`cache_version_timeout = 60000` ms, matching the ~60 s mark), anything that needs a fresh key lookup fails with:

```
ERROR 1105 (HY000): hashicorp: Unable to get key data
```

Existing tables kept working because their keys were already cached.

**Finding 6, fails closed:** on restart without Vault access, the plugin cannot initialise and InnoDB refuses to start:

```
InnoDB: cannot enable encryption, encryption plugin is not available
Plugin 'InnoDB' registration as a STORAGE ENGINE failed.
Aborting
```

No key means no database, and no data is exposed. After access was restored, MariaDB ran crash recovery and came back with identical checksums.

---

## Recommendations

1. **Rotate twice, or document it.** With `rotate_key_age = 1`, one rotation leaves data on the old key. Update the main README's key rotation section so a single rotation isn't mistaken for re-encrypting the data. (Setting `rotate_key_age` to 0 is not the fix, as MariaDB treats 0 as "don't rotate in the background".)
2. **Check Vault is healthy right before rotating,** and set `innodb_encryption_threads` back to 0 as soon as `ROTATING_OR_FLUSHING` is 0 for every table, to keep the crash window short.
3. **Monitor Vault errors.** A revoked token or policy mistake doesn't break running queries straight away, so it can go unnoticed until the next restart, when MariaDB won't start at all.
4. **Keep the unseal keys and root token out of the repo** and somewhere safe. Without them a restarted Vault stays sealed, and the encrypted data can't be read.
5. **Keep `use_cache_on_timeout = ON`.** It is what kept the database running when Vault was unreachable.

## Limitations

- Everything ran locally on one Mac with Docker. Timings are not production numbers.
- The data is synthetic (generated with Faker).
- The plain control copy only contains `customers`. `accounts` and `transactions` were checked against their own text values, without a plain copy.
- Vault failures were simulated (policy deny, network cut). A real Vault crash or seal was not tested, to avoid locking the Vault.
- Each test was run once per configuration.