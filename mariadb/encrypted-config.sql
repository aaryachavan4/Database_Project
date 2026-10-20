-- Encryption is configured in hashicorp.cnf, which MariaDB loads at startup.
-- This file rebuilds the existing tables so their data gets encrypted,
-- then checks the result.

USE encryption_test;

ALTER TABLE customers FORCE;
ALTER TABLE accounts FORCE;
ALTER TABLE transactions FORCE;

SELECT NAME, ENCRYPTION_SCHEME
FROM information_schema.INNODB_TABLESPACES_ENCRYPTION
WHERE NAME LIKE 'encryption_test/%';
