# HashiCorp Vault Integration

This folder contains the HashiCorp Vault configuration and integration files used for MariaDB data-at-rest encryption.

## Purpose

HashiCorp Vault is used as the external key management system for MariaDB.

MariaDB uses the `hashicorp_key_management` plugin to retrieve encryption keys from Vault.

The active Vault KV v2 mount is:

```text
mariadb/
```

The MariaDB encryption key is stored under key ID:

```text
mariadb/1
```

## Files

The main files used for the Vault integration are:

```text
vault/config.hcl
vault/connection-test.py
vault/README.md
```

The MariaDB side uses:

```text
mariadb/hashicorp.cnf
```

## Starting Vault

Set the Vault address:

```bash
export VAULT_ADDR='http://127.0.0.1:8200'
```

Start Vault:

```bash
vault server -config=vault/config.hcl
```

Vault must be initialized and unsealed before MariaDB can use it.

Check the status:

```bash
vault status
```

A working Vault instance should show:

```text
Initialized    true
Sealed         false
```

If Vault is sealed, unseal it using:

```bash
vault operator unseal
```

The command must be executed using the required number of different unseal keys.

Unseal keys and Vault tokens must never be stored in Git.

## Vault Secrets Engines

The project uses two Vault secrets engines:

```text
mariadb/   KV v2
transit/   Transit
```

The MariaDB HashiCorp plugin uses the `mariadb/` KV v2 engine for the real database encryption key.

The Transit engine is also configured as part of the project environment.

Available secrets engines can be checked using:

```bash
vault secrets list
```

## MariaDB Key

The MariaDB encryption key is stored at:

```text
mariadb/1
```

The metadata can be checked without displaying the actual encryption key:

```bash
vault kv metadata get -mount=mariadb 1
```

This shows the current key version and the history of previous versions.

The actual secret value should not be printed or shared.

## MariaDB Integration

MariaDB loads the HashiCorp key management plugin using:

```ini
plugin_load_add=hashicorp_key_management.so
```

The Vault endpoint is configured using:

```ini
hashicorp-key-management-vault-url=http://host.docker.internal:8200/v1/mariadb
```

Key caching is enabled:

```ini
hashicorp-key-management-caching-enabled=ON
```

The MariaDB Docker container receives its Vault authentication token through the `VAULT_TOKEN` environment variable.

The token is not stored directly inside `hashicorp.cnf`.

## Encryption Configuration

The MariaDB encryption configuration includes:

```ini
innodb_encrypt_tables=FORCE
innodb_encrypt_log=ON
innodb_encrypt_temporary_tables=ON
encrypt_tmp_disk_tables=ON
encrypt_tmp_files=ON
```

This enables encryption for InnoDB tables, logs, temporary tables, and temporary files.

## Verify the HashiCorp Plugin

The plugin status can be checked using:

```bash
docker exec mariadb mariadb -uroot -proot_password -e \
"SELECT PLUGIN_NAME, PLUGIN_STATUS FROM information_schema.PLUGINS WHERE PLUGIN_NAME LIKE 'hashicorp%';"
```

Expected output:

```text
hashicorp_key_management        ACTIVE
hashicorp_key_management_cache  ACTIVE
```

## Verify Encryption Settings

Run:

```bash
docker exec mariadb mariadb -uroot -proot_password -e \
"SHOW VARIABLES LIKE 'innodb_encrypt%';"
```

The important settings are:

```text
innodb_encrypt_log                ON
innodb_encrypt_tables             FORCE
innodb_encrypt_temporary_tables   ON
```

## Connection Test

The file:

```text
vault/connection-test.py
```

is used to verify that the encrypted MariaDB database is available and that the project tables can still be queried successfully.

Run:

```bash
python vault/connection-test.py
```

The test checks the database connection and reads rows from:

```text
customers
accounts
transactions
```

## Week 3: Key Rotation Test

A live key rotation test was performed using the Vault
