"""run_sql: the single tool our PBA 1 agent is allowed to use.

The tool checks every query before running it, so it can genuinely fail on
bad input (step 2 of the topic). Every failure comes back as a message that
starts with "Error:" so the graph can notice it.

Fault switch (for repeatable tests, PBA 2 reuses it):
    QM_FAULT=db_down   pretend the database is unreachable
    QM_FAULT=corrupt   return rows with every number flipped negative, no error
"""
import os
import re
import sqlite3
from pathlib import Path

from langchain_core.tools import tool

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "shop.db"
MAX_ROWS = 50

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
    return [tuple(-v if isinstance(v, (int, float)) else v for v in row) for row in rows]


def execute(query):
    """Run an already-checked query on a read-only connection."""
    if os.environ.get("QM_FAULT") == "db_down":
        return "Error: could not connect to the database (connection refused)."

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
    if os.environ.get("QM_FAULT") == "corrupt":
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
