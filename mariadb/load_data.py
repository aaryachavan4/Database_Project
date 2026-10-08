import pymysql
from faker import Faker
import random

fake = Faker()

conn = pymysql.connect(host='127.0.0.1', user='root', password='mariadb123', database='encryption_test')
cursor = conn.cursor()

print("Loading customers...")
customer_ids = []
for i in range(10000):
    try:
        cursor.execute(
            "INSERT INTO customers (first_name, last_name, email, phone, ssn, date_of_birth, address) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (fake.first_name(), fake.last_name(), fake.email(), fake.phone_number()[:20], fake.ssn()[:11], fake.date_of_birth(), fake.address()[:255])
        )
        customer_ids.append(i + 1)
        if (i + 1) % 1000 == 0:
            conn.commit()
            print(f"  {i + 1} customers loaded")
    except Exception as e:
        print(f"Error loading customer {i + 1}: {e}")
        conn.rollback()
        break

conn.commit()
print(f"✓ Customers complete: {len(customer_ids)} loaded\n")

if len(customer_ids) == 0:
    print("ERROR: No customers loaded, cannot continue")
    cursor.close()
    conn.close()
    exit(1)

print(f"Loading accounts (using {len(customer_ids)} customer IDs)...")
account_ids = []
for i in range(50000):
    try:
        customer_id = random.choice(customer_ids)
        cursor.execute(
            "INSERT INTO accounts (customer_id, account_number, account_type, balance, status) VALUES (%s, %s, %s, %s, %s)",
            (customer_id, fake.credit_card_number()[:20], random.choice(['Checking', 'Savings', 'Money Market']), round(random.uniform(100, 50000), 2), random.choice(['Active', 'Inactive']))
        )
        account_ids.append(i + 1)
        if (i + 1) % 5000 == 0:
            conn.commit()
            print(f"  {i + 1} accounts loaded")
    except Exception as e:
        print(f"Error loading account {i + 1}: {e}")
        conn.rollback()
        break

conn.commit()
print(f"✓ Accounts complete: {len(account_ids)} loaded\n")

if len(account_ids) == 0:
    print("ERROR: No accounts loaded, cannot continue")
    cursor.close()
    conn.close()
    exit(1)

print(f"Loading transactions (1M rows, using {len(account_ids)} account IDs)...")
merchants = ['Starbucks', 'Amazon', 'Walmart', 'Target', 'Apple', 'Netflix', 'Uber', 'PayPal', 'Best Buy', 'Whole Foods']

for i in range(1000000):
    try:
        account_id = random.choice(account_ids)
        cursor.execute(
            "INSERT INTO transactions (account_id, amount, transaction_type, description, merchant_name, status) VALUES (%s, %s, %s, %s, %s, %s)",
            (account_id, round(random.uniform(1, 500), 2), random.choice(['Debit', 'Credit', 'Transfer']), fake.sentence()[:255], random.choice(merchants), random.choice(['Completed', 'Pending']))
        )
        if (i + 1) % 100000 == 0:
            conn.commit()
            print(f"  {i + 1} transactions loaded")
    except Exception as e:
        print(f"Error loading transaction {i + 1}: {e}")
        conn.rollback()
        break

conn.commit()
print(f"✓ Transactions complete\n")

cursor.execute("SELECT COUNT(*) FROM customers")
customer_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM accounts")
account_count = cursor.fetchone()[0]
cursor.execute("SELECT COUNT(*) FROM transactions")
transaction_count = cursor.fetchone()[0]

print("="*50)
print("Final row counts:")
print(f"  Customers:   {customer_count:>10,}")
print(f"  Accounts:    {account_count:>10,}")
print(f"  Transactions:{transaction_count:>10,}")
print("="*50)

if customer_count > 0 and account_count > 0 and transaction_count > 0:
    print("\n✓ Data loading successful!")
else:
    print("\n✗ Data loading incomplete - check errors above")

cursor.close()
conn.close()