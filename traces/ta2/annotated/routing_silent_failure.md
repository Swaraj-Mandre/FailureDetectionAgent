# Annotated routing trace (U4-2): a silent bad result, caught and recovered

Raw trace: `traces/ta2/failures.txt`, case `bad_result` (`python ta2/run.py failures`)
Fault switch: `QM_FAULT=corrupt_once`, so the first result comes back 100 times too big with **no error**. This is the failure PBA 1 could not catch.

**Question:** What was the total delivered revenue in September 2026?

---

**1. executor** writes a correct query and calls `run_sql`.
```sql
SELECT SUM(oi.quantity * p.unit_price * (1 - oi.discount_pct / 100.0)) AS total_revenue
FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN products p ON p.product_id = oi.product_id
WHERE o.status = 'delivered' AND o.order_date LIKE '2026-09%'
```

**2. tools** returns `total_revenue = 24030020.00`. No error, nothing looks broken.

**3. monitor_result** checks the number against the shop's limits, which it works out from the data at start-up (all-time gross sales Rs 12,99,209).
> `total_revenue = 24030020.00 is more than the shop's all-time gross sales`

Verdict: **failure, type `bad_result`**. The Monitor also **replaces the result in the shared history** with a quarantine note, so no agent can reuse the bad number later. This is the containment step.
Route: `after_monitor_result` reads `verdict.status = "failure"` and goes to **recovery**.

**4. recovery** has 3 retries. `bad_result` is worth retrying, so it spends one (3 to 2), asks the error guide, and sends:
```
[recovery -> executor] The last result was quarantined. Run the query again. Check the units of the
unit_price column ... not stored in paise and read as rupees ...
Try again (2 retries left).
```
It also writes one row to `failure_log.db` (type `bad_result`, the SQL, the reason, 2 retries left).
Route: `after_recovery` sees `status = "retry"` and goes back to the **executor**.

**5. executor** runs the query again. **tools** returns `240300.20`.

**6. monitor_result**: the value is within limits. Verdict **ok**, so it goes back to the executor.

**7. executor** answers: "The total delivered revenue in September 2026 was Rs. 240,300.20."

**8. monitor_answer**: the result was a single number, so the answer must contain it. It does (240300). Verdict **ok**, so the run ends.

---

**Checked:** Rs 2,40,300.20 matches the reference number in `data/schema.md`.
**Cost:** 3 executor calls, 1 RAG advice call, 1 retry used.

**The same question in PBA 1** (`traces/ta1/corrupt.txt`): the agent reported "Rs. 24,03,00,20.00" to the user. Nothing in that graph looks at the data.

**What happens if the bad data keeps coming** (fault `corrupt`, see `traces/ta2/budget.txt` for the same pattern with a locked database): every retry is quarantined, the budget hits 0, Recovery stops the team and the user gets "I couldn't get a reliable answer, even after 3 retries ..." instead of a wrong number.
