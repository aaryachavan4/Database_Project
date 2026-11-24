# Vault Failure Test Results

Run at: 2026-11-24 18:44:39

Vault was never stopped or sealed. Failures were simulated by temporarily denying MariaDB's Vault policy (refused) or disconnecting the MariaDB container from the network (unreachable). Each check: reads on all three tables, an insert, and creating a new encrypted table.

Plugin settings:
```
hashicorp_key_management_cache_timeout	9223372036854775807
hashicorp_key_management_cache_version_timeout	60000
hashicorp_key_management_caching_enabled	ON
hashicorp_key_management_check_kv_version	ON
hashicorp_key_management_max_retries	3
hashicorp_key_management_timeout	15
hashicorp_key_management_use_cache_on_timeout	ON
hashicorp_key_management_vault_ca	
hashicorp_key_management_vault_url	http://host.docker.internal:8200/v1/mariadb
innodb_encryption_rotate_key_age	1
innodb_encryption_rotation_iops	100
innodb_encryption_threads	0
```

## A. Vault refuses access, MariaDB running

| Time | read customers | read accounts | read transactions | write (insert) | create new table |
|---|---|---|---|---|---|
| 0s | OK | OK | OK | OK | OK |
| 30s | OK | OK | OK | OK | OK |
| 61s | OK | OK | OK | OK | FAIL |
| 92s | OK | OK | OK | OK | FAIL |

Errors seen:
- **create new table**: `-------------- CREATE TABLE vf_new_table_4 (id INT PRIMARY KEY) ENGINE=InnoDB --------------  ERROR 1105 (HY000) at line 1: hashicorp: Unable to get key data`
- **create new table**: `-------------- CREATE TABLE vf_new_table_5 (id INT PRIMARY KEY) ENGINE=InnoDB --------------  ERROR 1105 (HY000) at line 1: hashicorp: Unable to get key data`

## B. Vault unreachable, MariaDB running

| Time | read customers | read accounts | read transactions | write (insert) | create new table |
|---|---|---|---|---|---|
| 0s | OK | OK | OK | OK | OK |
| 31s | OK | OK | OK | OK | OK |
| 61s | OK | OK | OK | OK | OK |
| 92s | OK | OK | OK | OK | OK |

## C. MariaDB restarts while Vault refuses access

- Container running after restart: **False**
- Server answering queries: **False** (waited 62s)

Relevant MariaDB log lines:
```
2026-11-24 17:43:34 0 [ERROR] Plugin 'hashicorp_key_management' init function returned error.
2026-11-24 17:43:34 0 [ERROR] Plugin 'hashicorp_key_management' registration as a ENCRYPTION failed.
2026-11-24 17:43:34 0 [ERROR] InnoDB: cannot enable encryption, encryption plugin is not available
2026-11-24 17:43:34 0 [ERROR] Plugin 'InnoDB' registration as a STORAGE ENGINE failed.
2026-11-24 17:43:34 0 [ERROR] Plugin 'hashicorp_key_management_cache' init function returned error.
2026-11-24 17:43:34 0 [ERROR] Plugin 'hashicorp_key_management_cache' registration as a INFORMATION SCHEMA failed.
2026-11-24 17:43:34 0 [ERROR] Failed to enable encryption of temporary files
2026-11-24 17:43:34 0 [ERROR] Aborting
```

## D. Recovery (access restored, MariaDB restarted)

- Server answering: **True** after 2s

| Time | read customers | read accounts | read transactions | write (insert) | create new table |
|---|---|---|---|---|---|
| 0s | OK | OK | OK | OK | OK |

- Data unchanged (CHECKSUM TABLE start vs end): **YES**
  - start: `encryption_test.customers	2113953775 encryption_test.accounts	1869731828 encryption_test.transactions	771748433`
  - end: `encryption_test.customers	2113953775 encryption_test.accounts	1869731828 encryption_test.transactions	771748433`

## Known MariaDB bug

With `innodb_encryption_threads = 4` (see `failure_results_threads4.md` and `crash_log_threads4.txt`), MariaDB crashed in scenario A with `InnoDB: Failing assertion: key_version != ENCRYPTION_KEY_VERSION_INVALID` in `fil0crypt.cc`. This matches the open MariaDB bug [MDEV-38141](https://jira.mariadb.org/browse/MDEV-38141): the same assertion, reproduced with the Hashicorp key management plugin, `innodb-encryption-threads=4` and `innodb-encryption-rotate-key-age=1`. The same assertion has also been reported before with other key plugins (e.g. [MDEV-9948](https://jira.mariadb.org/browse/MDEV-9948)).
