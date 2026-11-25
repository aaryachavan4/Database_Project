#!/usr/bin/env bash
set -uo pipefail

# Checks that the encrypted MariaDB setup is working.
# Prints PASS or FAIL for each check and exits non-zero if any check fails.
# Usage: bash mariadb/verify.sh   (needs MYSQL_ROOT_PASSWORD and a running container)

CONTAINER="${CONTAINER:-mariadb}"
: "${MYSQL_ROOT_PASSWORD:?Set MYSQL_ROOT_PASSWORD first: export MYSQL_ROOT_PASSWORD=your_password}"

FAILED=0

run_sql() {
  docker exec -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" "$CONTAINER" mariadb -uroot -N -e "$1" 2>/dev/null
}

report() {
  if [ "$2" = "yes" ]; then
    echo "PASS $1"
  else
    echo "FAIL $1"
    FAILED=1
  fi
}

PLUGIN=$(run_sql "SELECT PLUGIN_STATUS FROM information_schema.PLUGINS WHERE PLUGIN_NAME='hashicorp_key_management';")
if [ "$PLUGIN" = "ACTIVE" ]; then
  report "hashicorp_key_management plugin is ACTIVE" yes
else
  report "hashicorp_key_management plugin is ACTIVE" no
fi

ENC=$(run_sql "SELECT VARIABLE_VALUE FROM information_schema.GLOBAL_VARIABLES WHERE VARIABLE_NAME='INNODB_ENCRYPT_TABLES';")
if [ "$ENC" = "ON" ] || [ "$ENC" = "FORCE" ]; then
  report "innodb_encrypt_tables is ON or FORCE" yes
else
  report "innodb_encrypt_tables is ON or FORCE" no
fi

for TABLE in customers accounts transactions; do
  SCHEME=$(run_sql "SELECT ENCRYPTION_SCHEME FROM information_schema.INNODB_TABLESPACES_ENCRYPTION WHERE NAME='encryption_test/$TABLE';")
  if [ "$SCHEME" = "1" ]; then
    report "$TABLE is encrypted (ENCRYPTION_SCHEME 1)" yes
  else
    report "$TABLE is encrypted (ENCRYPTION_SCHEME 1)" no
  fi
done

exit $FAILED