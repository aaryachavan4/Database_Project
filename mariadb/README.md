# MariaDB Encryption with HashiCorp Vault

MariaDB encrypts its tables on disk. The encryption key lives in HashiCorp Vault and MariaDB fetches it at startup. No token or key is stored in this repo.

## Prerequisites
- Docker Desktop
- HashiCorp Vault CLI, with a Vault server running and unsealed (see `vault/README.md`)
- Python 3, with `pip install pymysql faker`

## 1. Start Vault
```bash
export VAULT_ADDR='http://127.0.0.1:8200'
vault status
```

## 2. Create the secrets store, policy and key (once per Vault)
Run as the Vault owner, using the root token:
```bash
vault secrets enable -path=mariadb -version=2 kv
vault policy write mariadb-policy - <<'EOF'
path "mariadb/*" { capabilities = ["create", "read", "update", "delete", "list"] }
path "sys/mounts/mariadb/tune" { capabilities = ["read"] }
EOF
vault kv put mariadb/1 data="$(openssl rand -hex 16)"
```

## 3. Create a token for MariaDB
```bash
export MYSQL_ROOT_PASSWORD=root_password
export MARIADB_VAULT_TOKEN=$(vault token create -policy=mariadb-policy -ttl=720h -field=token)
```
Keep the token only in your terminal. Do not save it to a file or commit it. The `MYSQL_ROOT_PASSWORD` line is needed for the commands later in this README, so run it in every new terminal.

## 4. Start everything with one command
From the project root:
```bash
bash mariadb/start.sh
```

The script builds the image, starts MariaDB with your Vault token, loads the schema and the sample data, runs the encryption setup, and runs the connection check. It ends with `Done. MariaDB is running with encryption on.` It removes and recreates the `mariadb` container on every run, so existing data is reset.

## What the script checks
- `innodb_encrypt_tables` is `FORCE`, and the log and temporary table settings are `ON`.
- The `hashicorp_key_management` plugin is `ACTIVE`.
- `ENCRYPTION_SCHEME` is `1` for `customers`, `accounts` and `transactions`.
- The connection check prints PASS for each table and ends with `All checks passed`.

## Key rotation
To rotate the key, write a new version to Vault, then flush MariaDB's key cache:
```bash
export VAULT_ADDR='http://127.0.0.1:8200'
vault kv put mariadb/1 data="$(openssl rand -hex 16)"
docker exec mariadb mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e "FLUSH HASHICORP_KEY_MANAGEMENT_CACHE; SELECT * FROM information_schema.HASHICORP_KEY_MANAGEMENT_CACHE;"
```
The cache output should show the new `KEY_VERSION`. Do not delete old key versions, since existing data may still need them.

Existing data is re-encrypted in the background, which needs `innodb_encryption_threads` above 0:
```bash
docker exec mariadb mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SET GLOBAL innodb_encryption_threads = 4;"
```
Re-encryption is finished when this returns no rows:
```bash
docker exec mariadb mariadb -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SELECT SPACE, NAME FROM information_schema.INNODB_TABLESPACES_ENCRYPTION WHERE ROTATING_OR_FLUSHING = 1;"
```

## Troubleshooting
- **"token must be specified"**: The token wasn't passed. Check `echo $MARIADB_VAULT_TOKEN` is not empty, then run `bash mariadb/start.sh` again.
- **"encryption plugin is not available"**: The plugin failed to load. Run `docker logs mariadb 2>&1 | grep -i hashicorp` and check the first error.
- **"Key not found (key id: 2)"**: This is a note, not a failure, when the plugin is active and the encryption checks pass.
- **Settings show `OFF`**: The `innodb_encrypt` lines in `hashicorp.cnf` are commented out, or the image wasn't rebuilt. Uncomment them, then run `docker build --no-cache -t mariadb-encryption mariadb/` and `bash mariadb/start.sh` again.
- **Port 3306 already in use**: Stop your local MariaDB first.
- **Container can't reach Vault**: Vault must listen on an address Docker can reach. Check the `address` line in `vault/config.hcl`. Changing it affects the shared config, so agree on it with the team first.

## Notes
- `hashicorp.cnf` holds settings only. The token is passed at run time.
- Each Vault has its own key. Data encrypted with one Vault's key can't be read with another's.
- `SET GLOBAL` settings reset when the container restarts, so run them again after a restart.