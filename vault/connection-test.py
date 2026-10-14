import pymysql

def test_encrypted_connection():
    try:
        conn = pymysql.connect(
            host='127.0.0.1',
            user='root',
            password='root_password',
            database='encryption_test'
        )

        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM customers")
        customers = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM accounts")
        accounts = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM transactions")
        transactions = cursor.fetchone()[0]

        print("Encrypted MariaDB connection successful")
        print(f"Customers: {customers}")
        print(f"Accounts: {accounts}")
        print(f"Transactions: {transactions}")

        cursor.close()
        conn.close()

        return True

    except Exception as e:
        print(f"Connection test failed: {e}")
        return False


if __name__ == "__main__":
    test_encrypted_connection()
