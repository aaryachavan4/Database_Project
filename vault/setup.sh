#!/usr/bin/env bash

set -e

export VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"

echo "Checking Vault status..."
vault status

echo
echo "Checking MariaDB KV v2 secrets engine..."

if ! vault secrets list -format=json | grep -q '"mariadb/"'; then
    echo "Creating MariaDB KV v2 secrets engine..."
    vault secrets enable -path=mariadb -version=2 kv
else
    echo "MariaDB KV v2 secrets engine already exists."
fi

echo
echo "Creating MariaDB Vault policy..."

vault policy write mariadb-policy - <<'EOF'
path "mariadb/data/*" {
  capabilities = ["read"]
}

path "sys/mounts/mariadb/tune" {
  capabilities = ["read"]
}
EOF

echo
echo "Checking MariaDB encryption key..."

if vault kv get -mount=mariadb 1 >/dev/null 2>&1; then
    echo "MariaDB encryption key already exists. Leaving it unchanged."
else
    echo "Creating initial MariaDB encryption key..."

    MARIADB_KEY="$(openssl rand -hex 32)"
    vault kv put -mount=mariadb 1 data="$MARIADB_KEY" >/dev/null
    unset MARIADB_KEY

    echo "MariaDB encryption key created."
fi

echo
echo "Vault setup complete."

echo
echo "To create a MariaDB Vault token, run:"
echo
echo "vault token create -policy=mariadb-policy"

echo
echo "Do not commit:"
echo "- Vault tokens"
echo "- unseal keys"
echo "- encryption key values"
echo "- vault/data/"
