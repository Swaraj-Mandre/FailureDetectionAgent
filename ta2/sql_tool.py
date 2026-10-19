"""run_sql for PBA 2: the same checks as the PBA 1 tool, so TA1 and TA2 are compared fairly.

Only the database path now comes from config.py. Every failure still starts
with "Error:".

Fault switch (the first three are identical to PBA 1):
    QM_FAULT=db_down       pretend the database is unreachable
    QM_FAULT=locked        pretend the database is busy (normally worth a retry), and stays busy
    QM_FAULT=corrupt       every number comes back 100 times too big (a paise vs rupees
                           bug), with no error. Only a sanity check can catch it.
Two short-lived faults, new in PBA 2, so a recovery can actually succeed:
    QM_FAULT=flaky         busy for the first 2 queries, then works
    QM_FAULT=corrupt_once  only the first result is 100 times too big, later ones are fine
    QM_FAULT=garble_once   the first query arrives garbled (a stray ')' at the end)
Call reset_faults() before each test so the counters start from zero.
"""
import os
import re
import sqlite3

from langchain_core.tools import tool

from config import SHOP_DB as DB_PATH

MAX_ROWS = 50
_calls = {"queries": 0}   # how many queries reached execute() since the last reset

# Any of these words means the query would change the database or its settings.
BLOCKED_WORDS = (
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "truncate", "attach", "detach", "pragma", "vacuum", "reindex",
)


def check_query(query):
    """Return an error message if the query must not run, otherwise None."""
    text = (query or "").strip().rstrip(";").strip()
    if not text:
        return "Error: the query is empty. Write one SELECT statement."

    if ";" in text:
        return "Error: only one SQL statement is allowed at a time."

    words = set(re.findall(r"[a-z_]+", text.lower()))
    blocked = sorted(words.intersection(BLOCKED_WORDS))
    if blocked:
        return (f"Error: this tool is read-only. '{blocked[0].upper()}' would change "
                "the database, so the query was blocked.")

    first_word = text.split()[0].lower()
    if first_word not in ("select", "with"):
        return "Error: the query must start with SELECT (or WITH)."
    return None


def format_rows(columns, rows):
    def cell(value):
        return f"{value:.2f}" if isinstance(value, float) else str(value)

    lines = [" | ".join(columns)]
    lines += [" | ".join(cell(v) for v in row) for row in rows[:MAX_ROWS]]
    note = f" (showing first {MAX_ROWS})" if len(rows) > MAX_ROWS else ""
    return f"{len(rows)} row(s){note}:\n" + "\n".join(lines)


def corrupt(rows):
    return [tuple(v * 100 if isinstance(v, (int, float)) else v for v in row) for row in rows]


def reset_faults():
    _calls["queries"] = 0


def execute(query):
    """Run an already-checked query on a read-only connection."""
    fault = os.environ.get("QM_FAULT")
    _calls["queries"] += 1
    if fault == "garble_once" and _calls["queries"] == 1:
        query += " )"
    if fault == "db_down":
        return "Error: could not connect to the database (connection refused)."
    if fault == "locked" or (fault == "flaky" and _calls["queries"] <= 2):
        return "Error: database is locked (another program is writing to it)."

    if not DB_PATH.exists():
        return "Error: shop.db not found. Run python data/make_shop_db.py first."

    try:
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5) as conn:
            cursor = conn.execute(query)
            columns = [c[0] for c in cursor.description]
            rows = cursor.fetchall()
    except sqlite3.Error as exc:
        return f"Error: {exc}. Check the table and column names against the schema."

    if not rows:
        return "The query ran fine but returned no rows."
    if fault == "corrupt" or (fault == "corrupt_once" and _calls["queries"] == 1):
        rows = corrupt(rows)
    return format_rows(columns, rows)


@tool
def run_sql(query: str) -> str:
    """Run one read-only SQL SELECT query on the shop database and return the rows.
    Use it for every question about customers, products, orders or sales."""
    problem = check_query(query)
    if problem:
        return problem
    return execute(query.strip().rstrip(";"))
