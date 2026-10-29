#!/usr/bin/env bash
set -euo pipefail

# Starts the encrypted MariaDB container, loads the data and checks encryption.
# Prerequisite: Vault is running and unsealed (see vault/README.md).
# Usage: bash mariadb/start.sh

cd "$(dirname "$0")"
export MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-root_password}"
export VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
IMAGE=mariadb-encryption
CONTAINER=mariadb

run_sql() {
  docker exec -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" "$CONTAINER" mariadb -uroot "$@"
}

echo "1. Checking Vault..."
if ! vault status >/dev/null 2>&1; then
  echo "Vault is not reachable or is sealed. Start and unseal it first (see vault/README.md)." >&2
  exit 1
fi

echo "2. Getting a MariaDB token..."
if [ -z "${MARIADB_VAULT_TOKEN:-}" ]; then
  MARIADB_VAULT_TOKEN=$(vault token create -policy=mariadb-policy -ttl=720h -field=token)
fi
export MARIADB_VAULT_TOKEN

echo "3. Building the image..."
docker build -t "$IMAGE" .

echo "4. Starting MariaDB..."
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run --name "$CONTAINER" --add-host=host.docker.internal:host-gateway \
  -e MYSQL_ROOT_PASSWORD="$MYSQL_ROOT_PASSWORD" \
  -e VAULT_TOKEN="$MARIADB_VAULT_TOKEN" \
  -p 3306:3306 -d "$IMAGE" >/dev/null

echo "5. Waiting for MariaDB to be ready..."
for i in $(seq 1 60); do
  if run_sql -e "SELECT 1" >/dev/null 2>&1; then
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "MariaDB did not start. Run: docker logs $CONTAINER" >&2
    exit 1
  fi
  sleep 2
done

echo "6. Checking the Vault plugin..."
PLUGIN=$(run_sql -N -e "SELECT PLUGIN_STATUS FROM information_schema.PLUGINS WHERE PLUGIN_NAME='hashicorp_key_management';" 2>/dev/null || true)
if [ "$PLUGIN" != "ACTIVE" ]; then
  echo "The Vault plugin is not active. Run: docker logs $CONTAINER" >&2
  exit 1
fi

echo "7. Loading the data (skipped if already loaded)..."
TABLES=$(run_sql -N -e "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='encryption_test' AND TABLE_NAME='transactions';" 2>/dev/null || echo 0)
if [ "$TABLES" = "0" ]; then
  docker exec -i -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" "$CONTAINER" mariadb -uroot < schema.sql
  python3 load_data.py
  docker exec -i -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" "$CONTAINER" mariadb -uroot < encrypted-config.sql
else
  echo "   Data already present, skipping."
fi

echo "8. Checking encryption..."
run_sql -e "SHOW VARIABLES LIKE 'innodb_encrypt_tables';"
run_sql -e "SELECT NAME, ENCRYPTION_SCHEME FROM information_schema.INNODB_TABLESPACES_ENCRYPTION WHERE NAME LIKE 'encryption_test/%';"

echo "9. Running the connection check..."
python3 ../vault/connection-test.py

echo "Done. MariaDB is running with encryption on."
