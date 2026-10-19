# Refinement notes (PBA 1, step 5)

## The short version

The slide version of the failure handling **noticed** every tool failure, but it did not **recover** well. It sent the same message, "That tool call failed. Try a different approach, or explain why.", for every kind of failure. So the agent:

- kept rewriting a blocked DELETE, then told the user to run it by hand
- kept retrying a database that was simply down
- repeated the exact same broken query again and again
- wrote a "corrected" query as text and never ran it

We changed only the failure handling. The prompt, the tool and the graph shape are the same in both versions.

| File | Role |
|---|---|
| `agent_v1_slide.py` | version 1, exactly as in Unit II Session 4 (kept for comparison) |
| `agent.py` | version 2, refined |

## What we saw (version 1)

| Case | What went wrong | Trace |
|---|---|---|
| schema_error | Caught the ambiguous `order_id` error, then wrote the fixed SQL **as text** and stopped. The user got no answer. | `traces/ta1/v1_slide_version/schema_error.txt` |
| unsafe | DELETE was blocked, then the agent told the user to **run the DELETE themselves**. | `traces/ta1/v1_slide_version/unsafe.txt` |
| db_down | Answered "let's try a different approach" and stopped. Nothing useful for the user. | `traces/ta1/v1_slide_version/db_down.txt` |
| early test run | Sent the **identical** broken query 7 times in a row (8 model calls, 23 s) before giving up. | `traces/ta1/early_runs/clean.txt` |
| early test run | Retried a dead database 5 times, once with two identical calls in the same turn. | `traces/ta1/early_runs/db_down.txt` |

Root cause: the failure handler treated every error the same way, gave no detail about what was wrong, and had no limit. "Try a different approach" is the wrong instruction when no query can ever succeed.

## What we changed (version 2)

1. **Classify the failure.** `handle_failure` reads the error and sorts it: schema, syntax, arguments, busy, unsafe or unavailable.
2. **Stop retrying when retrying is pointless.** For unsafe and unavailable, the notice tells the agent to explain instead of retry, and the graph **removes the tool** for the next turn (`tools_locked`). The model cannot keep trying even if it wants to.
3. **Say what was wrong.** For fixable failures, the notice includes the real error and a short hint (for example "quantity is in order_items", "SQLite has no YEAR()").
4. **Retry cap.** At most 3 fixable failures, then the agent must give up and say what kept failing. A counter lives in the graph state (`failures`).
5. **Repeat detection.** If the agent sends a query it already sent and failed with, the notice says so.
6. **Tighter detection in `route_after_tools`.** Version 1 checked `"Error" in content`, which would also fire on a normal result that happens to contain the word. Version 2 checks every tool result from the latest turn (there can be more than one) for our `Error:` prefix, plus `status == "error"`, which ToolNode sets when the model passes broken arguments.

## Result (same prompt, same questions)

| Case | Version 1 | Version 2 |
|---|---|---|
| clean | correct answer | correct answer |
| schema_error | no answer (fixed SQL written as text, never run) | **recovered**: fixed the column, ran it, correct answer |
| unsafe | told the user to run DELETE by hand | **clear refusal**, no SQL handed out |
| db_down | vague reply | **clear message**: database unreachable, try later, no guessed numbers |
| retry_cap | stopped after 3 tries only because the model happened to give up | **stops after 3 tries by design**, explains the cause |
| corrupt | wrong number reported | wrong number reported (see below) |

Every row comes from `python ta1/run_cases.py` (version 2) and `python ta1/run_cases.py --v1` (version 1). Traces are in `traces/ta1/`.

## What version 2 still cannot do

When the tool returns wrong data **without an error** (the `corrupt` case: every number 100 times too big), nothing in this graph looks at the data, so the wrong number goes straight to the user. Detecting failures that raise no error needs a separate check on the result. That is the Monitor agent in PBA 2.

## Two smaller fixes found on the way

- **Prompt:** we added the exact revenue expression and one worked example query (few-shot, as in Unit II Session 1) to both versions. Without it, the 3B model sometimes grouped by order instead of product and returned a confident wrong top-3 with no error.
- **Data:** order IDs now increase with the order date, like a real shop. Before, "most recent orders" by `order_id` was quietly wrong.

## Honest limits

- With temperature 0 the runs repeat closely, but we saw small differences after the model was reloaded. We report each result from a fresh run of the scripts.
- The 3B model sometimes invents a stand-in when asked for data we do not have (for example "loyalty points"), with no error. Version 2 does not catch this either.
