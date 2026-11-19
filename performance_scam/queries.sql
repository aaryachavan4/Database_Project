-- performance/queries.sql
-- The test queries used by performance/benchmark.py (Person 3).
-- Same queries, same random ids, on both the unencrypted and the encrypted server.
-- In the benchmark, values shown here as fixed numbers are replaced by seeded random ids.
-- Why each one is here is noted above it: together they cover every code path
-- where InnoDB encryption can cost time (page decrypt on read, page encrypt on
-- flush, redo log + binlog encryption on commit, encrypted temporary files).

USE encryption_test;

-- ===================================================================== READS
-- q1  small scan: count 10k rows (whole customers table)
SELECT COUNT(*) FROM customers;

-- q2  point lookup by primary key: one page read
SELECT * FROM customers WHERE customer_id = 5000;

-- q3  secondary index lookup (FK index on accounts.customer_id)
SELECT * FROM accounts WHERE customer_id = 500;

-- q4  secondary index lookup on the 1M-row table (idx_account) + row lookups
SELECT * FROM transactions WHERE account_id = 1000 LIMIT 100;

-- q5  full scan of the 1M-row table: reads every page -> worst case for decryption
SELECT SUM(amount) FROM transactions;

-- q6  join between transactions and accounts
SELECT t.transaction_id, t.amount, a.account_type
FROM transactions AS t
JOIN accounts AS a ON t.account_id = a.account_id
LIMIT 1000;

-- q7  aggregate over 1M rows with an internal temporary table (GROUP BY non-indexed column)
SELECT merchant_name, COUNT(*), SUM(amount)
FROM transactions
GROUP BY merchant_name;

-- q8  COUNT(DISTINCT) over 1M long strings: spills to temporary files on disk,
--     which are encrypted because of encrypt_tmp_files=ON / encrypt_tmp_disk_tables=ON
SELECT COUNT(DISTINCT description) FROM transactions;

-- ==================================================================== WRITES
-- Every write is committed on its own, so the timing includes the redo-log
-- write + fsync (encrypted with innodb_encrypt_log=ON) and the binlog
-- (encrypted with encrypt_binlog=ON). Benchmark rows are deleted afterwards
-- (untimed) so both servers keep identical data.

-- w1  single-row insert + COMMIT
INSERT INTO transactions (account_id, amount, transaction_type, description, merchant_name, status)
VALUES (1000, 42.00, 'Debit', 'benchmark row', 'BenchMerchant', 'Completed');
COMMIT;

-- w2  update by primary key + COMMIT
UPDATE transactions SET status = 'Completed' WHERE transaction_id = 123456;
COMMIT;

-- w3  batch insert of 1,000 rows in one transaction (benchmark uses executemany)
-- INSERT INTO transactions (...) VALUES (...), (...), ... ;  COMMIT;

-- cleanup (not timed)
DELETE FROM transactions WHERE description = 'benchmark row' AND merchant_name = 'BenchMerchant';
COMMIT;

-- ============================================================== THROUGHPUT
-- tp_read_*   N clients run this as fast as they can for 15 s:
SELECT * FROM transactions WHERE transaction_id = 123456;
-- tp_write_*  N clients run w1 (insert + COMMIT) as fast as they can for 15 s.

-- ================================================================= REBUILD
-- Cost of (re)writing a whole 1M-row table, as mariadb/encrypted-config.sql does
-- to encrypt existing data. On the encrypted server every page is encrypted.
ALTER TABLE transactions FORCE;

-- ========================================================== VERIFICATION
-- Run on each server before benchmarking:
SHOW VARIABLES LIKE 'innodb_encrypt%';
SELECT NAME, ENCRYPTION_SCHEME, CURRENT_KEY_ID, CURRENT_KEY_VERSION
FROM information_schema.INNODB_TABLESPACES_ENCRYPTION
WHERE NAME LIKE 'encryption_test/%';
SELECT NAME, FILE_SIZE FROM information_schema.INNODB_SYS_TABLESPACES
WHERE NAME LIKE 'encryption_test/%';
