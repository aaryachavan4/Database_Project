import pymysql

conn = pymysql.connect(host='127.0.0.1', user='root', password='root_password', database='encryption_test')
cursor = conn.cursor()
results = []

cursor.execute("SHOW VARIABLES LIKE 'innodb_encrypt_tables'")
row = cursor.fetchone()
results.append(("innodb_encrypt_tables is ON or FORCE", row is not None and row[1] in ('ON', 'FORCE')))

cursor.execute("SELECT PLUGIN_STATUS FROM information_schema.PLUGINS WHERE PLUGIN_NAME = 'hashicorp_key_management'")
row = cursor.fetchone()
results.append(("hashicorp plugin is active", row is not None and row[0] == 'ACTIVE'))

for table in ['customers', 'accounts', 'transactions']:
    cursor.execute(
        "SELECT ENCRYPTION_SCHEME FROM information_schema.INNODB_TABLESPACES_ENCRYPTION WHERE NAME = %s",
        (f"encryption_test/{table}",)
    )
    row = cursor.fetchone()
    results.append((f"{table} is encrypted", row is not None and (row[0] or 0) > 0))

cursor.close()
conn.close()

failed = 0
for label, ok in results:
    print("PASS" if ok else "FAIL", label)
    if not ok:
        failed += 1
print("All checks passed" if failed == 0 else f"{failed} check(s) failed")
