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

The web app does not use the switch. It passes the fault of one question in the run's config
("fault" and "run"), so a failure planted by one visitor never reaches another.
"""
import os
import re
import sqlite3
import time

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from config import SHOP_DB as DB_PATH

MAX_ROWS = 50
MAX_SECONDS = 10       # a join without a condition can run for hours, so it is cut off
MAX_FETCH = 20_000
MAX_CELL = 300         # longer text in one cell is cut, so one row cannot fill the model's memory
MAX_VALUE = 1_000_000  # bytes one value may grow to inside SQLite
MAX_TOTAL = 5_000_000  # letters in all the rows of one result
_calls = {}            # run -> how many queries reached execute() since the last reset

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

    # text in quotes is data, not a command: an item can be called 'Drop Shoulder T-shirt'
    outside = re.sub(r"'(?:[^']|'')*'", "''", text)
    if ";" in outside:
        return "Error: only one SQL statement is allowed at a time."

    words = set(re.findall(r"[a-z_]+", outside.lower()))
    blocked = sorted(words.intersection(BLOCKED_WORDS))
    if blocked:
        return (f"Error: this tool is read-only. '{blocked[0].upper()}' would change "
                "the database, so the query was blocked.")

    first_word = text.split()[0].lower()
    if first_word not in ("select", "with"):
        return "Error: the query must start with SELECT (or WITH)."
    return None


READING = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}


def only_reading(action, name, *_):
    """SQLite asks this before it runs a query. Reading tables is allowed and nothing else.
    The pragma_ tables are refused too, because they tell where the file lies on this computer."""
    allowed = action in READING and not str(name).lower().startswith("pragma_")
    return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY


def format_rows(columns, rows):
    def cell(value):
        if isinstance(value, float):
            return f"{value:.2f}"
        # | and line breaks separate cells and rows here. Inside a value they would shift the columns,
        # and a private column could end up where it is not hidden.
        return re.sub(r"\s*[\r\n\v\f\x1c-\x1e\x85\u2028\u2029]\s*", " ", str(value).replace("|", "/"))[:MAX_CELL]

    columns = [cell(c) for c in columns]
    lines = [" | ".join(columns)]
    lines += [" | ".join(cell(v) for v in row) for row in rows[:MAX_ROWS]]
    note = f" (showing first {MAX_ROWS})" if len(rows) > MAX_ROWS else ""
    return f"{len(rows)} row(s){note}:\n" + "\n".join(lines)


def corrupt(rows):
    return [tuple(v * 100 if isinstance(v, (int, float)) else v for v in row) for row in rows]


def reset_faults(run=""):
    _calls.pop(run, None)


def execute(query, db_path=DB_PATH, config=None):
    """Run an already-checked query on a read-only connection."""
    settings = (config or {}).get("configurable", {})
    run = settings.get("run", "")
    fault = settings["fault"] if "fault" in settings else os.environ.get("QM_FAULT")
    _calls[run] = count = _calls.get(run, 0) + 1
    if fault == "garble_once" and count == 1:
        query += " )"
    if fault == "db_down":
        return "Error: could not connect to the database (connection refused)."
    if fault == "locked" or (fault == "flaky" and count <= 2):
        return "Error: database is locked (another program is writing to it)."

    if not db_path.exists():
        return "Error: shop.db not found. Run python data/make_shop_db.py first."

    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5)
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE)
        conn.set_authorizer(only_reading)
        stop_at = time.monotonic() + MAX_SECONDS
        conn.set_progress_handler(lambda: time.monotonic() > stop_at, 10_000)
        cursor = conn.execute(query)
        columns = [c[0] for c in cursor.description]
        rows, size = [], 0
        for row in cursor:
            rows.append(row)
            size += sum(len(v) for v in row if isinstance(v, (str, bytes)))
            if size > MAX_TOTAL:
                return "Error: the query returns too much text. Ask for fewer columns or fewer rows."
            if len(rows) == MAX_FETCH:
                break
    except sqlite3.Error as exc:
        if "interrupted" in str(exc):
            return f"Error: the query was too slow and was stopped after {MAX_SECONDS} seconds."
        return f"Error: {exc}. Check the table and column names against the schema."
    finally:
        if conn is not None:
            conn.close()   # on Windows an open file cannot be deleted with its shop

    if not rows:
        return "The query ran fine but returned no rows."
    if fault == "corrupt" or (fault == "corrupt_once" and count == 1):
        rows = corrupt(rows)
    return format_rows(columns, rows)


@tool
def run_sql(query: str, config: RunnableConfig) -> str:
    """Run one read-only SQL SELECT query on the shop database and return the rows.
    Use it for every question about customers, products, orders or sales."""
    problem = check_query(query)
    if problem:
        return problem
    return execute(query.strip().rstrip(";"), config=config)


def make_run_sql(db_path):
    """The same tool, reading an uploaded shop instead of the sample one."""

    @tool("run_sql")
    def run_shop_sql(query: str, config: RunnableConfig) -> str:
        """Run one read-only SQL SELECT query on the shop's data and return the rows.
        Use it for every question about the shop."""
        problem = check_query(query)
        if problem:
            return problem
        return execute(query.strip().rstrip(";"), db_path, config)

    return run_shop_sql
