# Vault Failure Test Results

Run at: 2026-11-09 21:44:03

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
```

## A. Vault refuses access, MariaDB running

| Time | read customers | read accounts | read transactions | write (insert) | create new table |
|---|---|---|---|---|---|
| 0s | OK | OK | OK | OK | OK |
| 30s | FAIL | FAIL | FAIL | FAIL | FAIL |
| 61s | FAIL | FAIL | FAIL | FAIL | FAIL |
| 91s | FAIL | FAIL | FAIL | FAIL | FAIL |

Errors seen:
- **create new table**: `Error response from daemon: container dcea61c6a940ec8d2afe8ba77e9d17353e8f1b4dc1c720e88a44ddb2a2dbf54a is not running`
- **read accounts**: `Error response from daemon: container dcea61c6a940ec8d2afe8ba77e9d17353e8f1b4dc1c720e88a44ddb2a2dbf54a is not running`
- **read customers**: `Error response from daemon: container dcea61c6a940ec8d2afe8ba77e9d17353e8f1b4dc1c720e88a44ddb2a2dbf54a is not running`
- **read transactions**: `Error response from daemon: container dcea61c6a940ec8d2afe8ba77e9d17353e8f1b4dc1c720e88a44ddb2a2dbf54a is not running`
- **write (insert)**: `Error response from daemon: container dcea61c6a940ec8d2afe8ba77e9d17353e8f1b4dc1c720e88a44ddb2a2dbf54a is not running`

## B. Vault unreachable, MariaDB running

| Time | read customers | read accounts | read transactions | write (insert) | create new table |
|---|---|---|---|---|---|
| 0s | FAIL | FAIL | FAIL | FAIL | FAIL |
| 30s | FAIL | FAIL | FAIL | FAIL | FAIL |
| 61s | FAIL | FAIL | FAIL | FAIL | FAIL |
| 1151s | FAIL | FAIL | FAIL | FAIL | FAIL |

Errors seen:
- **create new table**: `Error response from daemon: container dcea61c6a940ec8d2afe8ba77e9d17353e8f1b4dc1c720e88a44ddb2a2dbf54a is not running`
- **read accounts**: `Error response from daemon: container dcea61c6a940ec8d2afe8ba77e9d17353e8f1b4dc1c720e88a44ddb2a2dbf54a is not running`
- **read customers**: `Error response from daemon: container dcea61c6a940ec8d2afe8ba77e9d17353e8f1b4dc1c720e88a44ddb2a2dbf54a is not running`
- **read transactions**: `Error response from daemon: container dcea61c6a940ec8d2afe8ba77e9d17353e8f1b4dc1c720e88a44ddb2a2dbf54a is not running`
- **write (insert)**: `Error response from daemon: container dcea61c6a940ec8d2afe8ba77e9d17353e8f1b4dc1c720e88a44ddb2a2dbf54a is not running`

## C. MariaDB restarts while Vault refuses access

- Container running after restart: **False**
- Server answering queries: **False** (waited 61s)

## D. Recovery (access restored, MariaDB restarted)

- Server answering: **True** after 2s

| Time | read customers | read accounts | read transactions | write (insert) | create new table |
|---|---|---|---|---|---|
| 0s | OK | OK | OK | OK | OK |

- Data unchanged (CHECKSUM TABLE start vs end): **YES**
  - start: `encryption_test.customers	1483823331 encryption_test.accounts	885270237 encryption_test.transactions	4099005701`
  - end: `encryption_test.customers	1483823331 encryption_test.accounts	885270237 encryption_test.transactions	4099005701`

