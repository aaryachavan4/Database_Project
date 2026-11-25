#!/usr/bin/env bash
set -euo pipefail

# Rotates the MariaDB encryption key in Vault, then flushes MariaDB's key cache.
# Prerequisites: Vault is unsealed, the mariadb container is running, and
# VAULT_TOKEN is set to an admin token (rotation writes to Vault, so the
# MariaDB token is not enough).
# Usage: bash mariadb/rotate.sh

cd "$(dirname "$0")"
: "${MYSQL_ROOT_PASSWORD:?Set MYSQL_ROOT_PASSWORD first: export MYSQL_ROOT_PASSWORD=your_password}"
export VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
CONTAINER="${CONTAINER:-mariadb}"

run_sql() {
  docker exec -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" "$CONTAINER" mariadb -uroot "$@"
}

echo "1. Writing a new key version to Vault..."
vault kv put mariadb/1 data="$(openssl rand -hex 16)" >/dev/null
echo "   New key version created."

echo "2. Flushing MariaDB's key cache..."
run_sql -e "FLUSH HASHICORP_KEY_MANAGEMENT_CACHE;"

echo "3. Current key cache:"
run_sql -e "SELECT * FROM information_schema.HASHICORP_KEY_MANAGEMENT_CACHE;"

echo "4. Enabling background re-encryption (innodb_encryption_threads = 4)..."
run_sql -e "SET GLOBAL innodb_encryption_threads = 4;"

echo "Done. Check KEY_VERSION above. Wait for re-encryption to finish before rotating again."