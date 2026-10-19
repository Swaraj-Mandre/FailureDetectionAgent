# Roles (U4-1)

Three agents and one human checkpoint. Each role has one job, and no two roles can do the same thing.

| | Executor | Monitor | Recovery | Privacy gate |
|---|---|---|---|---|
| **Goal** | Turn the user's question into SQL, run it, and answer from the rows | Make sure nothing wrong is trusted: no failed, impossible or unapproved result reaches another agent or the user | After a failure, either get a corrected retry or stop safely, never guess | Let a person decide whether personal data is shown |
| **Tools** | `run_sql`, `remember_term` (qwen2.5:3b) | None. Plain Python rules over the tool output and the shop's own limits | Error guide RAG (Chroma + nomic-embed-text), failure log | LangGraph `interrupt` |
| **Inputs** | The conversation, the glossary, advice from Recovery | Each new SQL result with the query that produced it, and the Executor's final answer | The Monitor's verdict, the failed SQL, the retry budget | Result columns that look personal (email, phone) |
| **Outputs** | A tool call, or a final answer | A verdict: ok, failure (with a type) or needs approval. Swaps a bad result for a quarantine note | Specific advice for the Executor, or a safe stop message; one failure-log row | Approve, or reject (columns masked) |
| **Limits** | The only agent that touches the database, and only through the read-only tool. Must not answer from a quarantined result | Never writes SQL and never talks to the user. Rules only, so its verdict is the same every time | At most 3 retries per question. Never retries unsafe or unreachable cases. Never edits data | Only pauses when personal data is in a result. Cannot change the query |

## How they talk

Agents share one message history and a small `verdict` record in the graph state. Messages from Recovery start with `[recovery -> executor]`, borrowing the idea of a typed message from the Unit IV ACL session (an "inform" or "request" act) without the full FIPA syntax.

## Why this split

- **One job each.** Remove the Monitor and silent bad results reach the user (see the containment demo). Remove Recovery and the Executor gets no guidance and no limit. Remove the Executor and nothing answers.
- **The Monitor uses no model.** A 3B model checking another 3B model would share its blind spots. Rules over the shop's real limits (all-time gross sales, the largest real order, table sizes, no negative money) and one rule over the SQL itself (SUM or COUNT next to a plain column with no GROUP BY, which SQLite answers with one arbitrary row) are cheap, fast and give the same verdict every time.
- **Recovery is the only place that decides to stop.** One owner for "try again or give up" means the retry budget is enforced in one spot.
