A project for area 10, "Encryption and key management" for the MariaDB student database projects, 2026-09

# MariaDB Encryption and Key Management

This project encrypts the tables of a MariaDB database at rest, with the encryption key stored in HashiCorp Vault. It covers the financial data model (customers, accounts, transactions), the Vault integration, and a key rotation test.

Setup: see [mariadb/README.md](mariadb/README.md) for the database and [vault/README.md](vault/README.md) for Vault.

## Quick start

1. Install Docker Desktop, the Vault CLI, and Python 3 with `pip install pymysql faker`.
2. Start Vault and create the `mariadb/` store, policy and key. Follow steps 1 and 2 in [mariadb/README.md](mariadb/README.md).
3. Create a token and start MariaDB. Follow steps 3 to 5 in [mariadb/README.md](mariadb/README.md).
4. Load the data and encrypt the tables. Follow step 7 in [mariadb/README.md](mariadb/README.md).
5. Verify the setup. Follow step 8 in [mariadb/README.md](mariadb/README.md).

## Platforms

Tested on macOS (Docker Desktop) and Windows (WSL2 Ubuntu with Docker Desktop). Run all commands in a bash-compatible shell. Linux with native Docker needs the `--add-host=host.docker.internal:host-gateway` flag shown in `mariadb/README.md` step 5. That flag is harmless on the other platforms.

Each person ran the tests on their own Vault, so the version numbers in each part belong to that person's setup and don't need to match.

## Part 1: MariaDB Setup (Person 1: Zoya)

The database runs MariaDB 13.0.2 in Docker, with the HashiCorp Vault key management plugin. It holds three tables of financial data:

- `customers`: personal details, including SSN (sensitive)
- `accounts`: account numbers and balances (sensitive)
- `transactions`: amounts, merchants and dates (sensitive, 1,000,000 rows)

Setup steps are in `mariadb/README.md`. Table encryption is set in `mariadb/hashicorp.cnf` with `innodb_encrypt_tables=FORCE`. The tables were encrypted with `encrypted-config.sql`, and all three report `ENCRYPTION_SCHEME` 1.

### Key rotation test (Zoya's Vault)

- Key `mariadb/1` was rotated from version 2 to version 3 at 21:05:03 CET, while `continuous_writes.py` was running.
- The writes figures are whole-run totals, because the log has no clock time per line. The run that included the rotation completed 1,226,609 writes with 0 errors (2,044 writes per second, average latency 0.49 ms).
- A baseline run with no rotation completed 1,759,961 writes with 0 errors (2,933 writes per second, average latency 0.34 ms).
- Re-encryption of `transactions` finished about 9.5 minutes after the rotation (between 21:14:13 and 21:14:43 CET). This was measured before the database was rebuilt. It needs `innodb_encryption_threads` above 0.
- The test rows were removed by rebuilding the database, which again holds 1,000,000 transactions. The current tables were created with key version 3.

## Part 2: Vault Integration (Person 2: Durgesh)

HashiCorp Vault is used as the external key management system for MariaDB data-at-rest encryption.

The MariaDB `hashicorp_key_management` plugin connects to a Vault KV v2 secrets engine mounted at `mariadb/`. The database encryption key is stored under key ID `1` at `mariadb/1`.

MariaDB receives its Vault authentication token through the `VAULT_TOKEN` environment variable. Vault tokens, unseal keys, and encryption key values are not stored in the repository.

The HashiCorp integration was verified successfully. Both MariaDB plugins were active:

```text
hashicorp_key_management        ACTIVE
hashicorp_key_management_cache  ACTIVE
```

MariaDB encryption was configured with:

```text
innodb_encrypt_tables             FORCE
innodb_encrypt_log                ON
innodb_encrypt_temporary_tables   ON
```

The connection test also confirmed that the `customers`, `accounts`, and `transactions` tables are encrypted and accessible.

### Key Rotation

A Vault KV v2 key rotation test was performed.

Before rotation:

```text
Vault key version:    3
MariaDB key version:  3
```

After rotation, the active Vault key version became `5`.

MariaDB continued to support both reads and writes without requiring a restart.

The encrypted tablespaces for `accounts`, `customers`, and `transactions` all reported:

```text
CURRENT_KEY_ID       1
CURRENT_KEY_VERSION  5
```

This confirmed successful Vault integration and online key rotation.

Detailed Vault setup instructions, verification commands, and rotation results are available in `vault/README.md`.