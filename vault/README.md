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

From the project root, start Vault:

```bash
vault server -config=vault/config.hcl
```

Keep this terminal running.

In another terminal, set the Vault address:

```bash
export VAULT_ADDR='http://127.0.0.1:8200'
```

Check the Vault status:

```bash
vault status
```

## Setup Checklist

Follow these steps in order:

1. Start the Vault server:

   ```bash
   vault server -config=vault/config.hcl
   ```

2. In another terminal, set the Vault address:

   ```bash
   export VAULT_ADDR='http://127.0.0.1:8200'
   ```

3. From the project root, run the one-run Vault setup:

   ```bash
   bash vault/setup.sh
   ```

4. If Vault has not been initialized before, the script initializes it with five unseal-key shares and a threshold of three.

   The unseal keys are saved locally outside the repository at:

   ```text
   ~/.database-project-vault-keys
   ```

5. If Vault is sealed, the script asks for three different unseal keys.

   The keys are entered through Vault's hidden prompt and are not written directly into shell history.

6. The script then:

   - creates or verifies the `mariadb/` KV v2 secrets engine;
   - writes the `mariadb-policy`;
   - creates the MariaDB encryption key if it does not already exist;
   - creates a restricted MariaDB Vault token.

7. The MariaDB Vault token is printed once.

   Copy it and export it:

   ```bash
   export MARIADB_VAULT_TOKEN='paste_token_here'
   ```

8. Set the MariaDB root password:

   ```bash
   export MYSQL_ROOT_PASSWORD='your_password'
   ```

9. Start MariaDB:

   ```bash
   bash mariadb/start.sh
   ```

Never commit Vault tokens, unseal keys, encryption key values, passwords, `vault/data/`, or the local key file.

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

This displays key versions without displaying the secret key value.

The setup script creates the initial key using:

```bash
openssl rand -hex 32
```

This provides a 32-byte encryption key.

## Vault Policy

MariaDB uses a restricted Vault policy called:

```text
mariadb-policy
```

The policy allows MariaDB to read the encryption keys but does not give administrative access to Vault.

The policy used by the project is:

```hcl
path "mariadb/data/*" {
  capabilities = ["read"]
}

path "sys/mounts/mariadb/tune" {
  capabilities = ["read"]
}
```

The policy is created automatically by:

```bash
bash vault/setup.sh
```

## MariaDB Integration

MariaDB connects to Vault using the HashiCorp key-management plugin.

The relevant settings in `mariadb/hashicorp.cnf` are:

```ini
[mariadb]
plugin_load_add=hashicorp_key_management.so
hashicorp-key-management-vault-url=http://host.docker.internal:8200/v1/mariadb
hashicorp-key-management-caching-enabled=ON
```

The Vault token is not stored inside `hashicorp.cnf`.

Instead, it is passed to the MariaDB container using the `VAULT_TOKEN` environment variable.

The restricted token is created by `vault/setup.sh` and exported locally as:

```bash
export MARIADB_VAULT_TOKEN='paste_token_here'
```

## Encryption Configuration

The MariaDB configuration enables encryption for tables, logs and temporary data.

The important settings are:

```ini
innodb_encrypt_tables=FORCE
innodb_encrypt_log=ON
innodb_encrypt_temporary_tables=ON
encrypt_tmp_disk_tables=ON
encrypt_tmp_files=ON
```

## Verify the HashiCorp Plugin

Set the MariaDB password first:

```bash
export MYSQL_ROOT_PASSWORD='your_password'
```

Then run:

```bash
docker exec mariadb mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e \
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
docker exec mariadb mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e \
"SHOW VARIABLES LIKE 'innodb_encrypt_tables';"
```

The expected value is:

```text
FORCE
```

The encrypted tablespaces can be checked with:

```bash
docker exec mariadb mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e \
"SELECT NAME, ENCRYPTION_SCHEME FROM information_schema.INNODB_TABLESPACES_ENCRYPTION WHERE NAME LIKE 'encryption_test/%';"
```

The project tables should report an active encryption scheme.

## Connection Test

The connection test checks that:

- MariaDB is reachable;
- the HashiCorp key-management plugin is active;
- the encryption configuration is enabled;
- the `customers`, `accounts`, and `transactions` tablespaces are encrypted.

Run:

```bash
python3 vault/connection-test.py
```

If a Python virtual environment is being used:

```bash
source .venv/bin/activate
python vault/connection-test.py
```

A successful test should report all checks as passing.

## WSL2 Verification

A complete WSL2 Ubuntu run was verified on November 18, 2026.

The workflow successfully:

- connected to an initialized and unsealed Vault instance;
- created a restricted MariaDB Vault token;
- built the MariaDB Docker image;
- loaded 10,000 customers;
- loaded 50,000 accounts;
- loaded 1,000,000 transactions;
- verified that the HashiCorp plugin was active;
- confirmed that the three project tablespaces were encrypted;
- passed the connection test.

The successful run ended with:

```text
Done. MariaDB is running with encryption on.
```

On WSL2, `mariadb/Dockerfile` copies the MariaDB configuration with mode `644`:

```dockerfile
COPY --chmod=644 hashicorp.cnf /etc/mysql/conf.d/hashicorp.cnf
```

This prevents MariaDB from ignoring `hashicorp.cnf` as a world-writable configuration file.

## Key Rotation

The MariaDB encryption key is stored at:

```text
mariadb/1
```

A Vault KV v2 key rotation test was performed.

Before rotation, the recorded test showed:

```text
Vault key version:    3
MariaDB key version:  3
```

After rotation, the active Vault key version became:

```text
5
```

During the recorded test, MariaDB continued to serve the tested read and write operations without requiring a restart.

The encrypted tablespaces for `accounts`, `customers`, and `transactions` reported:

```text
CURRENT_KEY_ID       1
CURRENT_KEY_VERSION  5
```

The MariaDB key-management cache also reported:

```text
KEY_ID       1
KEY_VERSION  5
```

The current Vault key metadata can be checked with:

```bash
vault kv metadata get -mount=mariadb 1
```

MariaDB's cached key can be checked with:

```bash
docker exec mariadb mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e \
"SELECT * FROM information_schema.HASHICORP_KEY_MANAGEMENT_CACHE;"
```

The encrypted tablespace versions can be checked with:

```bash
docker exec mariadb mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e \
"SELECT NAME, CURRENT_KEY_ID, CURRENT_KEY_VERSION FROM information_schema.INNODB_TABLESPACES_ENCRYPTION WHERE NAME LIKE 'encryption_test/%';"
```

## Vault Failure Behaviour

MariaDB uses Vault key caching.

During testing, normal reads and writes could continue while Vault was temporarily unavailable when the required encryption key was already cached.

However, operations that require communication with the key manager, including some key-rotation operations, can fail when Vault is unavailable.

MariaDB should therefore not be expected to operate indefinitely without access to Vault.

## Security Notes

The following values must never be committed to Git:

- Vault root tokens;
- MariaDB Vault tokens;
- Vault unseal keys;
- MariaDB root passwords;
- encryption key values;
- `.env` files containing secrets;
- `vault/data/`;
- `~/.database-project-vault-keys`.

The MariaDB Vault token uses the restricted `mariadb-policy` instead of the Vault root policy.

The encryption key itself is stored in Vault rather than inside the repository or MariaDB configuration files.

After setup, administrative/root Vault credentials should not be used by the MariaDB container.
