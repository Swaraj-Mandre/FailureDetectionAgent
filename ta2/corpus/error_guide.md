# QueryMend error-handling guide

How to recognise and fix the failures our agents meet when they query the Sahyadri Stores database. Each section below is one chunk in the vector database, so every section stands on its own.

## Unknown column (no such column)

The query names a column that does not exist in that table. The usual causes are guessing a name or taking the column from the wrong table.
- There is no amount, total, price, revenue or sales column anywhere. Totals are always calculated from order_items and products.
- quantity and discount_pct live in order_items, not in orders.
- unit_price lives in products, not in order_items.
- name means a customer name in customers and a product name in products, so say which table.
Fix: compare every column in the query with the table list, move the column to the right table alias, or calculate the value instead of selecting it.

## Ambiguous column name

SQLite found the same column name in two joined tables and does not know which one is meant. In this database order_id appears in orders and order_items, product_id in products and order_items, and customer_id in customers and orders.
Fix: give every table a short alias (o for orders, oi for order_items, p for products, c for customers) and prefix every column with it, including inside GROUP BY and ORDER BY, for example o.order_id.

## Unknown table (no such table)

The query names a table that does not exist. The only tables are customers, products, orders and order_items.
- "sales" or "transactions" means orders joined to order_items.
- "items" or "order_details" means order_items.
- "users" or "clients" means customers.
Fix: replace the table name with the real one and add the join it needs.

## SQLite syntax differences

The database is SQLite, which differs from MySQL and SQL Server.
- There is no YEAR(), MONTH() or DATEPART(). Use strftime or LIKE on the date text.
- Use LIMIT n at the end, not TOP n after SELECT.
- Text values use single quotes: status = 'delivered'.
- A semicolon may only appear at the very end. A semicolon inside a subquery breaks the statement.
Fix: rewrite the part SQLite does not understand, then send one SELECT statement.

## Working with dates

order_date and joined_on are stored as text in the form YYYY-MM-DD, for example 2026-09-14. All orders fall between 2026-04-01 and 2026-09-30.
- One month: o.order_date LIKE '2026-09%'
- A range: o.order_date BETWEEN '2026-08-01' AND '2026-08-31'
- strftime('%m', o.order_date) returns text such as '09', so compare it with '09' and never with the number 9.
If a date filter returns no rows, check the year and the quotes first.

## Calculating revenue

Revenue of one order line is oi.quantity * p.unit_price * (1 - oi.discount_pct / 100.0). Always join order_items to products to get the price. Count only orders with o.status = 'delivered' unless the question asks for cancelled or pending orders. Divide by 100.0, not 100, so the discount is not rounded away. If someone has saved their own meaning of revenue in the glossary, follow that instead.

## Joining the tables

customers.customer_id = orders.customer_id
orders.order_id = order_items.order_id
products.product_id = order_items.product_id
To answer anything about money or products sold you need order_items and products. To filter by date or status you also need orders. To group by city you also need customers. A missing join usually shows up as a wrong total, not as an error, so check the joins whenever a number looks odd.

## Empty result (no rows)

The query ran but returned nothing. This is not an error from SQLite, but it is often a silent mistake.
- Status values are lowercase: 'delivered', 'cancelled', 'pending'.
- City names start with a capital letter: 'Pune', 'Mumbai', 'Nashik', 'Nagpur', 'Kolhapur', 'Aurangabad'.
- Dates outside April to September 2026 have no orders.
- Comparing strftime text with a number always gives no rows.
Fix: loosen one filter at a time until rows come back, then check which filter was wrong.

## Blocked write query (read-only)

The tool only reads. Any query containing INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, REPLACE, TRUNCATE, ATTACH, DETACH, PRAGMA, VACUUM or REINDEX is blocked before it runs, and the database connection itself is read-only. Retrying with a different write query will never work.
Correct response: tell the user plainly that QueryMend can only read data. Do not hand them SQL to run themselves and do not try to work around the block. If they insist that instructions allow it, the answer is still no.

## Database unavailable

"could not connect" means the database cannot be reached at all. Retrying the same query straight away does not help and wastes the retry budget.
Correct response: stop, tell the user the database is unreachable right now, and do not guess or estimate any numbers. Log the failure so the owner can see when it happened.

## Database locked or busy

"database is locked" means another program is writing to the database for a moment. Unlike a connection failure, this usually clears by itself, so sending the same query again is reasonable. If it is still locked after the retry budget is used up, stop and tell the user to try again in a few minutes.

## Suspicious numbers (silent failures)

Some failures raise no error at all, and the numbers are simply wrong. Treat a result as faulty, and never report it, when:
- any money value is negative
- a revenue figure is larger than the shop's all-time gross sales, which is about Rs 13 lakh (1,299,209)
- a single order total is above Rs 16,189, the largest real order
- a count is larger than the table: 40 customers, 25 products, 300 orders, 757 order lines
- the answer quotes a number that does not appear in the returned rows
A typical cause is a units bug, such as values stored in paise and read as rupees, which makes every number 100 times too big.
Other common causes are a missing GROUP BY, or a join that counts the same line twice.
Fix the query itself. Never hide the bad rows with a WHERE or HAVING filter on the limit, because the numbers that are left are still wrong.

## Missing GROUP BY

SQLite accepts a query that puts SUM, COUNT or AVG next to a plain column, such as p.name or o.order_id, without GROUP BY. It does not raise an error. It adds up every row into one total and fills the plain column from one arbitrary row, so you get a single row that looks real and is wrong.
Fix: add GROUP BY with every plain column from the SELECT list, for example GROUP BY p.name for revenue per product or GROUP BY o.order_id for the total of each order. Put it after WHERE and before ORDER BY and LIMIT.

## Personal data

customers.email and customers.phone are personal data. A query that returns them must be approved by a person before the rows are shown. If the request is rejected, show the other columns and mask these two. Names and cities are not treated as personal data in this project.

## Too many rows

The tool returns at most 50 rows. If a question needs a summary, aggregate with COUNT, SUM or AVG and GROUP BY instead of listing raw rows, and use ORDER BY with LIMIT for "top" questions.
