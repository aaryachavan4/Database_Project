# MariaDB Encryption with HashiCorp Vault

MariaDB encrypts its data at rest using the `hashicorp_key_management` plugin.

HashiCorp Vault stores the encryption key, and MariaDB retrieves the key at runtime. Vault tokens, unseal keys, database passwords, and encryption key values must never be committed to this repository.

The integration is:

```text
MariaDB
   |
   v
hashicorp_key_management
   |
   v
HashiCorp Vault KV v2
   |
   v
mariadb/1
```

## Prerequisites

Before starting, make sure the following are available:

- Docker Desktop
- WSL2 with Ubuntu, or another supported Linux environment
- HashiCorp Vault CLI
- Python 3
- a Vault server that can be started and unsealed

### Python dependencies on Ubuntu / WSL

Install the required Python packages with:

```bash
sudo apt update
sudo apt install python3-pymysql python3-fake-factory
```

Verify them with:

```bash
python3 -c "import pymysql; from faker import Faker; print('Python dependencies OK')"
```

Expected output:

```text
Python dependencies OK
```

### Alternative: Python virtual environment

If the system packages are not available, use a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pymysql faker
```

For the complete Vault setup procedure, see:

```text
vault/README.md
```

---

## 1. Start and Unseal Vault

Set the Vault address:

```bash
export VAULT_ADDR='http://127.0.0.1:8200'
```

Start the Vault server from the project directory:

```bash
vault server -config=vault/config.hcl
```

Keep that terminal running.

In another terminal, set the Vault address again:

```bash
export VAULT_ADDR='http://127.0.0.1:8200'
```

Check Vault:

```bash
vault status
```

A usable Vault should report:

```text
Initialized    true
Sealed         false
```

If Vault has already been initialized but is sealed, unseal it using three different unseal keys:

```bash
vault operator unseal
vault operator unseal
vault operator unseal
```

If required, log in:

```bash
vault login
```

Do not store Vault tokens or unseal keys in the repository.

---

## 2. Configure Vault for MariaDB

The recommended method is to run the project setup script from the repository root:

```bash
./vault/setup.sh
```

The script:

- checks that Vault is available
- creates the `mariadb/` KV v2 secrets engine if it does not already exist
- creates or updates `mariadb-policy`
- grants MariaDB read-only access to its encryption key
- creates encryption key ID `1` only if it does not already exist
- leaves an existing key unchanged

The MariaDB Vault policy is:

```hcl
path "mariadb/data/*" {
  capabilities = ["read"]
}

path "sys/mounts/mariadb/tune" {
  capabilities = ["read"]
}
```

MariaDB therefore cannot create, update, or delete encryption keys.

### Manual Vault setup

If the setup script cannot be used, the equivalent manual setup is:

```bash
vault secrets enable -path=mariadb -version=2 kv

vault policy write mariadb-policy - <<'EOF'
path "mariadb/data/*" {
  capabilities = ["read"]
}

path "sys/mounts/mariadb/tune" {
  capabilities = ["read"]
}
EOF

vault kv put mariadb/1 data="$(openssl rand -hex 32)"
```

The key is stored at:

```text
mariadb/1
```

`openssl rand -hex 32` generates 32 random bytes for the encryption key.

Do not repeat the initial key-creation command on an existing installation unless a deliberate key rotation is being performed.

---

## 3. Set the MariaDB Password

The startup scripts read the MariaDB root password from the environment.

Set it before running the project:

```bash
export MYSQL_ROOT_PASSWORD='your_password'
```

Do not hard-code the real password in scripts or documentation.

---

## 4. Create the MariaDB Vault Token

A MariaDB token can be created manually with:

```bash
export MARIADB_VAULT_TOKEN=$(vault token create \
  -policy=mariadb-policy \
  -ttl=720h \
  -field=token)
```

Check that the variable contains a token without printing the token itself:

```bash
echo ${#MARIADB_VAULT_TOKEN}
```

The normal `mariadb/start.sh` workflow can also create the required MariaDB Vault token automatically.

Never save the token in:

- source files
- committed `.env` files
- configuration files
- README files
- Git history

---

## 5. Start MariaDB

From the repository root, run:

```bash
bash mariadb/start.sh
```

Before running it, make sure:

- Vault is running
- Vault is unsealed
- `MYSQL_ROOT_PASSWORD` is set
- the Python dependencies are installed

The startup script:

1. checks Vault
2. obtains a MariaDB Vault token
3. builds the MariaDB image
4. starts the container
5. waits for MariaDB
6. verifies the Vault plugin
7. loads the schema and sample data
8. verifies encryption
9. runs the connection test

A successful run ends with:

```text
Done. MariaDB is running with encryption on.
```

The full WSL2 Ubuntu startup workflow was successfully tested on November 18, 2026.

---

## 6. Sample Data

The project loads:

```text
Customers:       10,000
Accounts:        50,000
Transactions: 1,000,000
```

`mariadb/load_data.py` generates the sample data using Faker.

A successful load reports:

```text
Data loading successful!
```

---

## 7. Verify the HashiCorp Plugin

Check that the HashiCorp plugins are active:

```bash
docker exec mariadb mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e \
"SELECT PLUGIN_NAME, PLUGIN_STATUS
 FROM information_schema.PLUGINS
 WHERE PLUGIN_NAME LIKE 'hashicorp%';"
```

Expected output:

```text
hashicorp_key_management        ACTIVE
hashicorp_key_management_cache  ACTIVE
```

---

## 8. Verify Encryption Settings

Run:

```bash
docker exec mariadb mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e \
"SHOW VARIABLES LIKE 'innodb_encrypt%';"
```

The important settings are:

```text
innodb_encrypt_tables             FORCE
innodb_encrypt_log                ON
innodb_encrypt_temporary_tables   ON
```

The project also enables encryption for temporary disk tables and temporary files.

Check the encrypted tablespaces with:

```bash
docker exec mariadb mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e \
"SELECT NAME, ENCRYPTION_SCHEME
 FROM information_schema.INNODB_TABLESPACES_ENCRYPTION
 WHERE NAME LIKE 'encryption_test/%';"
```

Expected tables:

```text
encryption_test/customers
encryption_test/accounts
encryption_test/transactions
```

with:

```text
ENCRYPTION_SCHEME = 1
```

---

## 9. Connection Test

The test script is:

```text
vault/connection-test.py
```

It verifies:

- `innodb_encrypt_tables` is `ON` or `FORCE`
- the HashiCorp key-management plugin is active
- the `customers` tablespace is encrypted
- the `accounts` tablespace is encrypted
- the `transactions` tablespace is encrypted

Make sure the password is available:

```bash
export MYSQL_ROOT_PASSWORD='your_password'
```

Run:

```bash
python3 vault/connection-test.py
```

Expected output:

```text
PASS innodb_encrypt_tables is ON or FORCE
PASS hashicorp plugin is active
PASS customers is encrypted
PASS accounts is encrypted
PASS transactions is encrypted
All checks passed
```

---

## 10. Key Rotation

Vault KV v2 stores multiple versions of the same key entry.

Create a new key version with:

```bash
export VAULT_ADDR='http://127.0.0.1:8200'

vault kv put mariadb/1 data="$(openssl rand -hex 32)"
```

Do not delete previous Vault key versions because existing encrypted pages may still require them.

Flush the MariaDB key cache:

```bash
docker exec mariadb mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e \
"FLUSH HASHICORP_KEY_MANAGEMENT_CACHE;
 SELECT * FROM information_schema.HASHICORP_KEY_MANAGEMENT_CACHE;"
```

The cache should report the newer key version.

### Background re-encryption

Enable encryption threads with:

```bash
docker exec mariadb mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e \
"SET GLOBAL innodb_encryption_threads = 4;"
```

Check whether tablespaces are still rotating:

```bash
docker exec mariadb mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e \
"SELECT SPACE, NAME
 FROM information_schema.INNODB_TABLESPACES_ENCRYPTION
 WHERE ROTATING_OR_FLUSHING = 1;"
```

Rotation is complete when this query returns no rows.

The project uses:

```text
innodb_encryption_rotate_key_age = 1
```

During testing, more than one new Vault key version was required before all encrypted tablespaces reported the newer current key version.

---

## 11. Vault Availability and Caching

Key caching is enabled with:

```ini
hashicorp-key-management-caching-enabled=ON
```

During testing, MariaDB continued to serve normal reads and writes during a temporary Vault outage when the required encryption key was already cached.

However, caching does not make Vault optional.

If Vault becomes unavailable while active encryption-rotation threads require access to the key manager, MariaDB can fail.

Vault should therefore remain available during active key-management and rotation operations.

---

## Troubleshooting

### Vault is not reachable or is sealed

If `mariadb/start.sh` reports:

```text
Vault is not reachable or is sealed.
```

check:

```bash
export VAULT_ADDR='http://127.0.0.1:8200'
vault status
```

Start or unseal Vault before running the MariaDB startup script.

### MYSQL_ROOT_PASSWORD is not set

If the script reports:

```text
Set MYSQL_ROOT_PASSWORD first
```

run:

```bash
export MYSQL_ROOT_PASSWORD='your_password'
```

then rerun:

```bash
bash mariadb/start.sh
```

### Python module not found

If you see:

```text
ModuleNotFoundError: No module named 'pymysql'
```

install the Ubuntu/WSL dependencies:

```bash
sudo apt update
sudo apt install python3-pymysql python3-fake-factory
```

or activate the project virtual environment:

```bash
source .venv/bin/activate
```

### HashiCorp plugin is not active

Check:

```bash
docker logs mariadb 2>&1 | grep -i hashicorp
```

Possible causes include:

- Vault is sealed
- Vault is unreachable
- the Vault token is invalid
- the policy is missing
- the encryption key is missing
- `hashicorp.cnf` was ignored

### World-writable `hashicorp.cnf`

MariaDB ignores configuration files that are world-writable.

A log message such as:

```text
Warning: World-writable config file '/etc/mysql/conf.d/hashicorp.cnf' is ignored
```

means the HashiCorp configuration was not loaded.

The Dockerfile copies the configuration using:

```dockerfile
COPY --chmod=644 hashicorp.cnf /etc/mysql/conf.d/hashicorp.cnf
```

This ensures the file has safe permissions inside the Docker image.

### Invalid or expired Vault token

If the logs contain:

```text
403
permission denied
invalid token
```

create a new token using:

```bash
vault token create -policy=mariadb-policy
```

and restart MariaDB through the normal startup workflow.

### Key not found (key id: 2)

A message such as:

```text
Key not found (key id: 2)
```

is not necessarily a failure.

If key ID `1` is available, the HashiCorp plugin is active, and the encryption checks pass, MariaDB can still be operating normally.

### Port 3306 already in use

Stop the local service or container already using port `3306`, then start the project again.

### Docker cannot reach Vault

Vault must listen on an address Docker can reach.

The MariaDB configuration uses:

```text
host.docker.internal:8200
```

Check:

```text
vault/config.hcl
```

before changing the shared Vault networking configuration.

---

## Security Notes

Never commit or share:

- Vault root tokens
- MariaDB Vault tokens
- Vault unseal keys
- encryption key values
- MariaDB passwords
- committed `.env` files containing secrets
- `vault/data/`

The repository should contain only configuration, scripts, documentation, and non-secret test results.

---

## Notes

- `hashicorp.cnf` contains configuration only.
- The Vault token is passed at runtime.
- The MariaDB Vault policy is `mariadb-policy`.
- MariaDB has read-only access to Vault encryption keys.
- The encryption key is stored at `mariadb/1`.
- Key generation uses `openssl rand -hex 32`.
- Each independent Vault instance has its own encryption keys.
- Data encrypted using one Vault key cannot be decrypted using an unrelated key.
- `SET GLOBAL` settings may reset after a MariaDB restart.
- `load_data.py` generates the project's sample data using Faker.
- `start.sh` performs the normal startup and verification workflow.
