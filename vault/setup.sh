#!/usr/bin/env bash

set -e

export VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"

echo "Checking Vault status..."
vault status

echo "Checking transit secrets engine..."
if ! vault secrets list -format=json | grep -q '"transit/"'; then
    vault secrets enable transit
fi

echo "Checking MariaDB KV v2 secrets engine..."
if ! vault secrets list -format=json | grep -q '"mariadb/"'; then
    vault secrets enable -path=mariadb -version=2 kv
fi

echo "Checking transit key..."
if ! vault read transit/keys/mariadb-key >/dev/null 2>&1; then
    vault write -f transit/keys/mariadb-key
fi

echo "Creating MariaDB Vault policy..."

cat >/tmp/mariadb-policy.hcl <<'EOF'
path "mariadb/data/*" {
  capabilities = ["read"]
}

path "sys/mounts/mariadb/tune" {
  capabilities = ["read"]
}
EOF

vault policy write mariadb /tmp/mariadb-policy.hcl
rm -f /tmp/mariadb-policy.hcl

echo
echo "Vault setup complete."
echo
echo "The MariaDB encryption key itself is intentionally NOT generated here"
echo "to avoid overwriting an existing production/test key."
echo
echo "To create the initial key manually on a fresh environment, run:"
echo
echo 'vault kv put -mount=mariadb 1 data=$(openssl rand -hex 32)'
echo
echo
echo "To create a MariaDB token, run:"
echo
echo "vault token create -policy=mariadb"
echo
echo
echo "Do not commit the generated token, unseal keys, or secret values."
