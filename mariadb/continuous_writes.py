import pymysql
import random
import time

DURATION = 600  # seconds
MERCHANTS = ["Tesco", "Amazon", "Shell", "Lidl", "Netflix", "Apple", "Uber", "Spotify"]
TYPES = ["debit", "credit", "transfer"]

conn = pymysql.connect(host="127.0.0.1", user="root", password="root_password", database="encryption_test")
cursor = conn.cursor()

cursor.execute("SELECT account_id FROM accounts")
account_ids = [row[0] for row in cursor.fetchall()]

start = time.time()
writes = 0
errors = 0
latency_total = 0.0

while time.time() - start < DURATION:
    t0 = time.time()
    try:
        cursor.execute(
            """INSERT INTO transactions
               (account_id, amount, transaction_type, description, merchant_name, status)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                random.choice(account_ids),
                round(random.uniform(1, 1000), 2),
                random.choice(TYPES),
                "rotation test write",
                random.choice(MERCHANTS),
                "completed",
            ),
        )
        conn.commit()
        writes += 1
        latency_total += time.time() - t0
        if writes % 1000 == 0:
            print(f"Writes: {writes}, Errors: {errors}, Avg latency: {latency_total / writes * 1000:.2f} ms")
    except Exception as e:
        conn.rollback()
        errors += 1
        print(f"Error: {e}")

elapsed = time.time() - start
print(f"Complete: {writes} writes, {errors} errors, {writes / elapsed:.1f} writes/sec, "
      f"avg latency {latency_total / max(writes, 1) * 1000:.2f} ms")
cursor.close()
conn.close()
