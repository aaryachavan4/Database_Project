# MariaDB Encryption with HashiCorp Vault

MariaDB encrypts its tables on disk. The encryption key lives in HashiCorp Vault and MariaDB fetches it at startup. No token or key is stored in this repo.

## Prerequisites
- Docker Desktop
- HashiCorp Vault CLI, with a Vault server running
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
MARIADB_VAULT_TOKEN=$(vault token create -policy=mariadb-policy -ttl=720h -field=token)
```
Keep this only in your terminal. Do not save it to a file or commit it.

## 4. Build the image
```bash
cd mariadb
docker build -t mariadb-encryption .
```

## 5. Start MariaDB
```bash
docker rm -f mariadb
docker run --name mariadb -e MYSQL_ROOT_PASSWORD=root_password -e VAULT_TOKEN="$MARIADB_VAULT_TOKEN" -p 3306:3306 -d mariadb-encryption
```

## 6. Check that encryption is on
```bash
sleep 20; docker ps
docker exec mariadb mariadb -uroot -proot_password -e "SHOW VARIABLES LIKE 'innodb_encrypt%'; SELECT PLUGIN_NAME, PLUGIN_STATUS FROM information_schema.PLUGINS WHERE PLUGIN_NAME LIKE 'hashicorp%';"
```
Expected: `innodb_encrypt_tables` is `FORCE`, the log and temporary table settings are `ON`, and both `hashicorp_key_management` plugins are `ACTIVE`.

## 7. Load the data and encrypt the tables
```bash
docker exec -i mariadb mariadb -uroot -proot_password < schema.sql
python3 load_data.py
docker exec -i mariadb mariadb -uroot -proot_password < encrypted-config.sql
```
Expected: `ENCRYPTION_SCHEME` is `1` for `customers`, `accounts` and `transactions`.

## 8. Verify
```bash
cd ../vault
python3 connection-test.py
```
All checks should print PASS.

## Key rotation
To rotate the key, write a new version to Vault, then flush MariaDB's key cache:
```bash
export VAULT_ADDR='http://127.0.0.1:8200'
vault kv put mariadb/1 data="$(openssl rand -hex 16)"
docker exec mariadb mariadb -uroot -proot_password -e "FLUSH HASHICORP_KEY_MANAGEMENT_CACHE; SELECT * FROM information_schema.HASHICORP_KEY_MANAGEMENT_CACHE;"
```
The cache output should show the new `KEY_VERSION`. Do not delete old key versions, since existing data may still need them.

Existing data is re-encrypted in the background, which needs `innodb_encryption_threads` above 0:
```bash
docker exec mariadb mariadb -uroot -proot_password -e "SET GLOBAL innodb_encryption_threads = 4;"
```
Re-encryption is finished when this returns no rows:
```bash
docker exec mariadb mariadb -uroot -proot_password -e "SELECT SPACE, NAME FROM information_schema.INNODB_TABLESPACES_ENCRYPTION WHERE ROTATING_OR_FLUSHING = 1;"
```

## Troubleshooting
- **"token must be specified"**: The token wasn't passed. Repeat step 5 with `-e VAULT_TOKEN=...`.
- **"encryption plugin is not available"**: The plugin failed to load. Run `docker logs mariadb 2>&1 | grep -i hashicorp` and check the first error.
- **"Key not found (key id: 2)"**: This is a note, not a failure, when the plugin is active and the encryption checks pass.
- **Settings show `OFF`**: The `innodb_encrypt` lines in `hashicorp.cnf` are commented out, or the image wasn't rebuilt. Uncomment them, then rebuild with `docker build --no-cache`.
- **Port 3306 already in use**: Stop your local MariaDB first.
- **Container can't reach Vault**: Vault must listen on an address Docker can reach. Check the `address` line in `vault/config.hcl`. Changing it affects the shared config, so agree on it with the team first.

## Notes
- `hashicorp.cnf` holds settings only. The token is passed at run time.
- Each Vault has its own key. Data encrypted with one Vault's key can't be read with another's.
- `SET GLOBAL` settings reset when the container restarts, so run them again after a restart.