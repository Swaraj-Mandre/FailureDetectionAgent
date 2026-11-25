# QueryMend error guide for any shop

Used for shops that a shopkeeper uploaded. It never names a table or column, because every shop's data is different. Each section below is one chunk in the vector database and stands on its own.

## Unknown column (no such column)

The query names a column that the table does not have. The usual causes are a guessed name, or a column that is listed under a different table.
Fix: find the column in the table list. If it belongs to another table, join that table on the link that is listed, and put the table name in front of the column. If no table has it, calculate it from columns that exist, for example quantity times price.

## Ambiguous column name

Two joined tables have a column with the same name, and SQLite does not know which one is meant.
Fix: give every table a short alias and put the alias in front of every column, also inside GROUP BY and ORDER BY.

## Unknown table (no such table)

The query names a table that does not exist.
Fix: use only the table names in the list, spelled exactly the same, including underscores.

## SQLite syntax differences

The database is SQLite, which differs from MySQL and SQL Server. There is no YEAR(), MONTH(), DATEPART() or TOP. Use LIKE or strftime on date text, and LIMIT at the end. Text values use single quotes. Names with spaces or Marathi letters need double quotes.
Fix: rewrite the part SQLite does not understand, then send one SELECT statement.

## Working with dates

Dates are stored as text in the form YYYY-MM-DD. One month is LIKE '2026-09%'. A range is BETWEEN '2026-08-01' AND '2026-08-31'. Before a date is column < '2027-01-01'. strftime returns text, so compare it with '09' and never with the number 9.
Fix: check the year, the quotes and the date range shown next to the column.

## Totals and prices

If a column already holds the amount or total of each line, add up that column and do not multiply it again. If there is only a quantity and a price, multiply them first and then add up. For profit, subtract the cost price from the selling price inside brackets before multiplying by the quantity.

## Average per bill

One bill usually has many rows, one per item. The average of the rows is not the average bill.
Fix: first add up each bill in a subquery with GROUP BY on the bill number, then take AVG of those totals. Count bills with COUNT(DISTINCT bill number).

## Joining the tables

Join only on the links that are listed. A missing join shows up as an unknown column. A wrong join, or a join to a table with several matching rows, counts the same line twice and makes the total too big.
Fix: use the listed link, and join only the tables the question needs.

## Empty result (no rows)

The query ran but returned nothing. This is often a silent mistake: a text value spelled differently, a wrong capital letter, or a date outside the range of the data.
Fix: use the exact values shown next to the column. If the spelling is unsure, use LIKE with % on both sides.

## Missing GROUP BY

SQLite accepts SUM, COUNT or AVG next to a plain column without GROUP BY. It adds up every row and fills the plain column from one arbitrary row, so the result looks real and is wrong.
Fix: add GROUP BY with every plain column from the SELECT list, after WHERE and before ORDER BY and LIMIT.

## Suspicious numbers (silent failures)

Some failures raise no error and the numbers are simply wrong. Treat a result as faulty when it is larger than everything in the shop's data added together, when a count is larger than the largest table, or when a value is negative and nothing in the data is. Common causes are a units mistake that makes numbers 100 times too big, or a join that counts lines twice.
Fix the query itself. Never hide the bad rows with a filter, because the numbers that are left are still wrong.

## Blocked write query (read-only)

The tool only reads. Any query that would change the data is blocked before it runs. Retrying with a different write query will never work. Tell the user plainly that QueryMend can only read data.

## Database locked or busy

"database is locked" means another program is using the file for a moment. Sending the same query again is reasonable.

## Query too slow

The query was stopped because it took too long. The usual cause is joining two tables without a link, which pairs every row with every other row.
Fix: add the listed link as the join condition, or remove the table that is not needed.

## Personal data

Phone numbers, emails, addresses and ID numbers are personal data. A query that returns them must be approved by a person first. Leave those columns out unless the question asks for them.

## Too many rows

The tool returns at most 50 rows. If the question needs a summary, use COUNT, SUM or AVG with GROUP BY instead of listing rows.
