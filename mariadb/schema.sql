-- mariadb/schema.sql
CREATE DATABASE IF NOT EXISTS encryption_test;
USE encryption_test;

-- Customers table (sensitive: PII + SSN)
CREATE TABLE customers (
  customer_id INT PRIMARY KEY AUTO_INCREMENT,
  first_name VARCHAR(100),
  last_name VARCHAR(100),
  email VARCHAR(100),
  phone VARCHAR(20),
  ssn VARCHAR(11),  -- SENSITIVE
  date_of_birth DATE,
  address VARCHAR(255),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Bank accounts (sensitive: account numbers, balances)
CREATE TABLE accounts (
  account_id INT PRIMARY KEY AUTO_INCREMENT,
  customer_id INT,
  account_number VARCHAR(20),  -- SENSITIVE
  account_type VARCHAR(50),  -- checking, savings, credit
  balance DECIMAL(15,2),  -- SENSITIVE
  status VARCHAR(20),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

-- Transactions (1M+ rows - SENSITIVE)
CREATE TABLE transactions (
  transaction_id INT PRIMARY KEY AUTO_INCREMENT,
  account_id INT,
  amount DECIMAL(15,2),
  transaction_type VARCHAR(50),  -- debit, credit, transfer
  description VARCHAR(255),
  merchant_name VARCHAR(100),  -- SENSITIVE
  status VARCHAR(20),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (account_id) REFERENCES accounts(account_id),
  INDEX idx_account (account_id),
  INDEX idx_created (created_at)
);