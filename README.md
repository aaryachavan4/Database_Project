## Part 2: Vault Integration

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

Detailed Vault setup instructions, verification commands, and rotation results are available in `vault/README.md`.# Database_Project
Encryption and key management 💀
