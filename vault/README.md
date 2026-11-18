# HashiCorp Vault Integration

This folder contains the HashiCorp Vault configuration and integration files used for MariaDB data-at-rest encryption.

## Purpose

HashiCorp Vault is used as the external key management system for MariaDB.

MariaDB uses the `hashicorp_key_management` plugin to retrieve encryption keys from Vault.

The active Vault KV v2 mount is:

```text
mariadb/
```

The MariaDB encryption key is stored under:

```text
mariadb/1
```

## Files

The main Vault-related files are:

```text
vault/config.hcl
vault/setup.sh
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

Vault must be unsealed before MariaDB can use it.

Check the status:

```bash
vault status
```

A working Vault instance should show:

```text
Initialized    true
Sealed         false
```

If Vault is sealed, run:

```bash
vault operator unseal
```

using the required number of different unseal keys.

Vault tokens and unseal keys must never be committed to Git.

## Vault Secrets Engine

The MariaDB integration uses a Vault KV v2 secrets engine mounted at:

```text
mariadb/
```

The encryption key used by MariaDB is stored at:

```text
mariadb/1
```

The key metadata can be checked safely with:

```bash
vault kv metadata get -mount=mariadb 1
```

This shows key versions without displaying the secret value itself.

## MariaDB Integration

MariaDB loads the HashiCorp key management plugin using:

```ini
plugin_load_add=hashicorp_key_management.so
```

The Vault endpoint is configured as:

```ini
hashicorp-key-management-vault-url=http://host.docker.internal:8200/v1/mariadb
```

Key caching is enabled:

```ini
hashicorp-key-management-caching-enabled=ON
```

MariaDB receives its Vault token through the `VAULT_TOKEN` environment variable.

The token is not stored directly in `hashicorp.cnf`.

## Encryption Configuration

MariaDB encryption is enabled with:

```ini
innodb_encrypt_tables=FORCE
innodb_encrypt_log=ON
innodb_encrypt_temporary_tables=ON
encrypt_tmp_disk_tables=ON
encrypt_tmp_files=ON
```

## Verify the HashiCorp Plugin

Run:

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

verifies the MariaDB encryption configuration, confirms that the HashiCorp key management plugin is active, and checks that the `customers`, `accounts`, and `transactions` tablespaces are encrypted.

Run:

```bash
python vault/connection-test.py
```

Expected successful output includes:

```text
PASS innodb_encrypt_tables is ON or FORCE
PASS hashicorp plugin is active
PASS customers is encrypted
PASS accounts is encrypted
PASS transactions is encrypted
All checks passed
```

## Week 3: Key Rotation Test

A live key rotation test was performed using the Vault KV v2 key stored at:

```text
mariadb/1
```

Before rotation:

```text
Vault current key version:   3
MariaDB cached key version:  3
```

New key versions were created in Vault.

After the rotation process, Vault reported:

```text
current_version = 5
```

MariaDB continued to serve both read and write queries without requiring a server restart.

The encrypted tablespace metadata was checked using:

```sql
SELECT NAME,
       CURRENT_KEY_ID,
       CURRENT_KEY_VERSION,
       MIN_KEY_VERSION
FROM information_schema.INNODB_TABLESPACES_ENCRYPTION
WHERE NAME LIKE 'encryption_test/%';
```

The result showed:

| Table | Current Key ID | Current Key Version | Minimum Key Version |
|---|---:|---:|---:|
| customers | 1 | 5 | 3 |
| accounts | 1 | 5 | 3 |
| transactions | 1 | 5 | 3 |

This confirmed that all three encrypted tablespaces recognized key version 5 as the current encryption key version.

A test customer row was inserted after rotation and successfully read back from the database. The test row was deleted afterward.

### Rotation Behavior

The project uses:

```text
innodb_encryption_rotate_key_age = 1
```

Testing showed that creating only one newer key version was not sufficient to move all existing encrypted data to the new version. Two key-version updates were required before the tablespaces reported the newer current key version.

### Vault Failure Behavior

With Vault unavailable, MariaDB continued to serve normal reads and writes while the required encryption key was already cached.

However, later security testing showed an important limitation: if Vault becomes unavailable while active encryption-rotation threads require access to the key manager, MariaDB can fail.

Therefore, Vault caching improves availability during a normal temporary Vault outage, but Vault availability is still important during active key rotation.

## Security Notes

Do not commit or share:

- Vault root tokens
- MariaDB Vault tokens
- Vault unseal keys
- encryption key values
- `.env` files
- `vault/data/`

Only configuration files, scripts, and documentation should be committed.
