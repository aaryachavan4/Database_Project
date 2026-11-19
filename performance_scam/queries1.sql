
/* Question 1: Count customers*/
SELECT COUNT(*) FROM customers;

/* Question 2: Look up one customer by primary key*/
SELECT *
FROM customers
WHERE customer_id = 5000;

/* Question 3: Find accounts belonging to a customer*/
SELECT *
FROM accounts
WHERE customer_id = 500;

/* Question 4: Find transactions belonging to an account*/
SELECT *
FROM transactions
WHERE account_id = 1000
LIMIT 100;

/*Question 5: Calculate the total transaction amount*/
SELECT SUM(amount)
FROM transactions;

/*Question 6: Join transactions with accounts*/
SELECT t.transaction_id, t.amount, a.account_type
FROM transactions AS t
JOIN accounts AS a ON t.account_id = a.account_id
LIMIT 1000;
