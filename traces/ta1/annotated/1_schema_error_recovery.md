# Trace 1: a broken column name, caught and fixed

Raw trace: `traces/ta1/schema_error.txt` (version 2, `python ta1/run_cases.py schema_error`)

**Question:** Show the order_id and total amount of the 5 most recent orders.
**Kind of bad input:** a query with a column name problem. `order_id` exists in both `orders` and `order_items`, so SQLite cannot tell which one is meant.

---

**[02] agent node** calls the tool:
```sql
SELECT order_id, SUM(oi.quantity * p.unit_price * (1 - oi.discount_pct / 100.0)) AS total_amount
FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN products p ON p.product_id = oi.product_id
WHERE o.status = 'delivered' AND o.order_date >= '2026-04-01' AND o.order_date < '2026-10-01'
GROUP BY order_id ORDER BY order_id DESC LIMIT 5
```
> `tools_condition` sees a tool call, so the graph goes to the `tools` node.

**[03] tools node** returns:
```
Error: ambiguous column name: order_id. Check the table and column names against the schema.
```
> The tool's checks passed (one SELECT, nothing that writes), so SQLite ran it and raised the error. The tool turned it into text starting with `Error:`.

**route_after_tools** looks at every tool result from this turn, finds the `Error:` prefix, and returns `"handle_failure"`.

**[04] handle_failure node** classifies the error as **schema** (fixable), raises the failure counter to 1, and sends:
```
The tool failed with: Error: ambiguous column name: order_id. ...
Hint: ... If a column exists in two tables, prefix it with the table name or alias.
Write a corrected query and call run_sql again (2 tries left).
```
> Version 1 would only have said "That tool call failed. Try a different approach". Here the agent gets the real error and a hint that points at the fix.

**[05] agent node** tries again with every `order_id` prefixed as `o.order_id`:
```sql
SELECT o.order_id, SUM(...) AS total_amount ... GROUP BY o.order_id ORDER BY o.order_id DESC LIMIT 5
```

**[06] tools node** returns 5 rows. **route_after_tools** finds no error and returns `"agent"`.

| order_id | total_amount |
|---|---|
| 299 | 1598.00 |
| 298 | 5127.15 |
| 297 | 381.65 |
| 295 | 260.00 |
| 293 | 2354.00 |

**[07] agent node** answers with the five orders and their totals in rupees. No tool call, so `tools_condition` ends the run.

---

**Checked against the database:** these match a hand-written query exactly (the latest 5 delivered orders, by order_id).
**Cost:** 3 model calls, 1 failure notice, about 4 seconds.
**Version 1 on the same question:** caught the same error, then wrote the corrected SQL as text and stopped without running it. No answer (`traces/ta1/v1_slide_version/schema_error.txt`).
