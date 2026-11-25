# Evaluation (EV)

Every number below comes from one command:

```bash
python ta2/evaluate.py
```

It takes about 4 minutes on an RTX 4060 and writes `traces/eval/results.json` (every run, with the full final answer) and `traces/eval/tables.md`. Model: qwen2.5:3b at temperature 0, run locally through Ollama.

## What we measured

1. **TA1 vs TA2 on the same 5 cases.** A clean question, a question that needs a schema fix, an unsafe request, a database that is down, and a result that is silently wrong (every money value 100 times too big). Each case runs 3 times on each system.
2. **Five injected failure types** through the TA2 team: schema, syntax, unsafe, unavailable, silent bad result. 3 runs each.
3. **Error propagation (U4-4).** One corrupted result in turn 1, then a follow-up question in turn 2 that reuses it. 10 runs with the Monitor off and 10 with it on.

Answers are judged by fixed rules in `judge()`, not by a model:

- **correct**: the right numbers or product names are in the answer (checked against `data/schema.md`)
- **safe**: no wrong number, and the answer says plainly that it could not help (read-only, database down, not reliable)
- **wrong**: a wrong number, or SQL that changes data handed to the user

For the schema case only the top 3 orders are checked. Orders 291, 293 and 295 share the date 28 Sep 2026, so 4th and 5th place can fairly come out in either order.

## Results

### 1. Same cases, TA1 vs TA2 (3 runs each)

| Case | TA1 | TA2 | Model calls TA1 / TA2 | Time (s) TA1 / TA2 |
|---|---|---|---|---|
| clean | 3/3 correct | 3/3 correct | 2.0 / 3.0 | 2.1 / 3.1 |
| schema_error | 2/3 correct, 1/3 wrong | 3/3 correct | 3.0 / 5.0 | 3.6 / 8.1 |
| unsafe | 3/3 safe | 3/3 safe | 2.0 / 1.0 | 0.8 / 0.3 |
| db_down | 3/3 safe | 3/3 safe | 2.0 / 1.0 | 0.7 / 0.5 |
| corrupt | 3/3 wrong | 3/3 safe | 2.0 / 6.0 | 1.3 / 4.3 |

TA1 gave a wrong number to the user in 4 of 15 runs. TA2 gave none.

### 2. Injected failures through the team (3 runs each)

| Failure type | Detected | Outcome | Avg retries | Avg time (s) |
|---|---|---|---|---|
| schema | 3/3 | 3/3 correct | 2.0 | 6.2 |
| syntax | 3/3 | 3/3 correct | 1.0 | 2.4 |
| unsafe | 3/3 | 3/3 safe | 0.0 | 0.4 |
| unavailable | 3/3 | 3/3 safe | 0.0 | 0.6 |
| bad_result | 3/3 | 3/3 correct | 1.0 | 3.2 |

### 3. Error propagation, Monitor off vs on (10 two-turn runs each)

| Monitor | Bad value kept in shared history | Wrong number in turn 1 | Wrong number carried into turn 2 | Turn 1 correct | Avg model calls | Avg time (s) |
|---|---|---|---|---|---|---|
| off | 10/10 | 10/10 | 10/10 | 0/10 | 4.0 | 3.0 |
| on | 0/10 | 0/10 | 0/10 | 10/10 | 5.0 | 3.3 |

## What got better

- **Silent failures stop at the Monitor.** With the x100 fault, TA1 reported Rs 24,03,00,20 three times out of three. TA2 caught it every time and said it could not get a reliable answer instead of guessing.
- **One bad result no longer spreads.** With the Monitor off, the corrupted value stayed in the shared history and came back in the follow-up answer in 10 of 10 runs. With it on, the value was quarantined before any agent could read it, and turn 1 was correct in 10 of 10 runs.
- **Unsafe and unreachable cases are cheaper.** TA2 stops these with a fixed message after one model call, where TA1 needs a second call to word its refusal.

## What got worse

- **Recovery costs time.** On cases that need a fix, TA2 uses about 2 to 3 times the model calls and time of TA1 (schema 5.0 vs 3.0 calls, corrupt 6.0 vs 2.0). The slowest case is still under 9 seconds.
- **The clean case now has one retry.** The TA2 Executor's prompt is the TA1 prompt plus the team rules. With that small change, the model leaves out GROUP BY on the clean question every time, and the Monitor has to send it back once. TA1 gets it right first time. The answer is right in both, but TA2 pays one extra call for it.
- **A persistent fault uses up the whole budget.** When the data stays corrupt, TA2 retries 3 times before it stops. A smarter Recovery could stop sooner once the same check fails twice with the same numbers.

## What the evaluation found in our own code

The first full run showed TA2 3/3 wrong on the clean case. Reading the saved answers showed the cause: the Executor wrote `SELECT p.name, SUM(...)` with no GROUP BY. SQLite runs that without an error and returns one row, with the whole month's revenue next to one arbitrary product name. It is a silent failure that our number bounds could not see, because the total itself was real.

The same bug explains TA1's wrong schema answer (Rs 8,13,186.80 for one order) and the extra retries in the schema case. In that case the max-order bound did catch the number, but the advice from the guide then told the model to filter out large totals. That hid the symptom and left the query wrong.

Two fixes, both in the Monitor and the guide, not in the judge:

1. A new Monitor rule reads the SQL as well as the rows. If SUM, COUNT or AVG sits next to a plain column with no GROUP BY, the result is quarantined and Recovery says exactly which GROUP BY to add.
2. The error guide gained a "Missing GROUP BY" section, and the "Suspicious numbers" section now says never to hide bad rows with a WHERE or HAVING filter.

After the fix, TA2 went from 3/3 wrong to 3/3 correct on the clean case, and the schema case dropped from 3 retries to 2.

## Limits of this evaluation

- 3 runs per case is small. At temperature 0 the repeats are close to identical, so they show stability more than spread. We saw the model's output change after the Ollama server reloaded the model, which is why every demo and table here comes from fresh runs.
- One model, one database, one machine. A larger model would make fewer SQL mistakes, and then the Monitor would fire less often.
- The judge checks numbers and names with fixed rules. It can miss a correct answer that is worded oddly. Every answer is saved in `results.json` so anyone can check by hand.

## Uploaded shops

```bash
python ta2/evaluate_uploads.py known      # and blind1, blind2
```

Every question has a query written by us that gives the right answer. An answer is right only when the name and the number are both right. Each set runs twice, with the Monitor's any-shop rules (`checks.py`) off and on. Saved in `traces/eval/uploads_*.json`.

How the tests were kept honest: the questions and right answers of a blind set were written before the work it measures, the model was not asked about them until the code was frozen, and the number reported is the first run. The first runs are kept in `uploads_blind1_first_run.json` and `uploads_blind2_first_run.json`.

| Set | Shops | Questions | Before | First run, rules off | First run, rules on | Now, rules on |
|---|---|---|---|---|---|---|
| known | 5, used while building | 43 | | | | 43 |
| blind1 | 3 | 36 | 23 | 23 | 24 | 35 (no longer blind) |
| blind2 | 3 | 36 | 29 | 32 | 33 | 35 (no longer blind) |

"Before" is commit 9bdf0d8 on the same files. For blind2 it was given the new splitting of names such as BillNo, so the old figure is slightly generous.

What we learned:

- **Rules on queries were not enough.** After the first round, blind1 went from 23 to 24 only. The Executor did not know what the columns meant.
- **Meaning helped most.** Working out from the numbers which column is the pieces, the price of one piece, the line total, the bill number and the unpaid money, and telling the Executor, took blind2 from 29 to 32 with the rules off.
- **The rules then fix a few more and can be wrong themselves.** One rule talked the model out of the correct UPI filter for a question about online payments. It now knows such words, and when a filter is dropped after a doubt the answer is marked Please verify.
- **Still wrong on the first run of blind2 (3):** the most milk on a date answered with rupees, the stock valued at MRP when the cost was asked, and haircuts answered with the money. None of the three was marked.
- **Limits of this test:** 11 invented shops, one person writing the questions, English only, one model, one run per question.

Hand-written queries for the rules alone: 21 of 21 wrong queries were caught and 29 of 29 right queries raised no alarm. These were used while building.

The sample shop does not use any of this. `evaluate.py` was run again after the change and every outcome in the tables above was the same. Timings were slower by up to 2.4 s, and the containment run with the Monitor on took 6.0 model calls instead of 5.0.
