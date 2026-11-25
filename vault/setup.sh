#!/usr/bin/env bash
set -euo pipefail

export VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"

# Store initialization material outside the repository.
KEY_FILE="${HOME}/.database-project-vault-keys"
ROOT_TOKEN=""

echo "Checking Vault..."
if ! vault status >/dev/null 2>&1; then
    echo "Vault is not reachable."
    echo "Start the Vault server first, then run this script again."
    exit 1
fi

echo
echo "Checking whether Vault is initialized..."

INITIALIZED="$(vault status -format=json | python3 -c \
'import json,sys; print(str(json.load(sys.stdin)["initialized"]).lower())')"

if [ "$INITIALIZED" = "false" ]; then
    echo "Vault is not initialized. Initializing with 5 shares and threshold 3..."

    INIT_JSON="$(vault operator init -key-shares=5 -key-threshold=3 -format=json)"

    # Save only the unseal keys outside the repository.
    printf '%s\n' "$INIT_JSON" | python3 -c '
import json, sys
data = json.load(sys.stdin)
for key in data["unseal_keys_b64"]:
    print(key)
' > "$KEY_FILE"

    chmod 600 "$KEY_FILE"

    ROOT_TOKEN="$(printf '%s\n' "$INIT_JSON" | python3 -c \
'import json,sys; print(json.load(sys.stdin)["root_token"])')"

    unset INIT_JSON

    echo "Vault initialized."
    echo "Unseal keys were saved locally to:"
    echo "$KEY_FILE"
    echo "Do not commit or share this file."
else
    echo "Vault is already initialized."
fi

echo
echo "Checking whether Vault is sealed..."

SEALED="$(vault status -format=json | python3 -c \
'import json,sys; print(str(json.load(sys.stdin)["sealed"]).lower())')"

if [ "$SEALED" = "true" ]; then
    echo "Vault is sealed."
    echo "Enter three different unseal keys."
    echo

    for i in 1 2 3; do
        echo "Unseal key $i of 3:"
        vault operator unseal
    done
else
    echo "Vault is already unsealed."
fi

echo
echo "Authenticating for administrative setup..."

if [ -n "$ROOT_TOKEN" ]; then
    export VAULT_TOKEN="$ROOT_TOKEN"
else
    if ! vault token lookup >/dev/null 2>&1; then
        echo "Enter a Vault root/admin token."
        vault login
    fi
fi

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
echo "Creating MariaDB Vault token..."

MARIADB_TOKEN="$(vault token create \
    -policy=mariadb-policy \
    -ttl=720h \
    -field=token)"

echo
echo "Vault setup complete."
echo
echo "MariaDB Vault token (shown once):"
echo "$MARIADB_TOKEN"

unset MARIADB_TOKEN
unset ROOT_TOKEN

echo
echo "Save the MariaDB token privately and export it as MARIADB_VAULT_TOKEN."
echo
echo "Local unseal-key file:"
echo "$KEY_FILE"
echo
echo "Never commit:"
echo "- Vault tokens"
echo "- unseal keys"
echo "- encryption key values"
echo "- vault/data/"
