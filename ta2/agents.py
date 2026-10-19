"""The PBA 2 team: Executor, Monitor and Recovery, plus the privacy gate.

Executor  turns the question into SQL and runs it. The only agent that touches
          the database. (LLM)
Monitor   checks every tool result before anyone uses it, and every final
          answer before the user sees it. Plain Python rules, no LLM, so it is
          fast and gives the same verdict every time.
Recovery  decides what happens after a failure: specific advice from the error
          guide and another try, or a safe stop when retrying cannot help or the
          retry budget is used up. (rules + RAG)

The functions here hold the logic. graph.py wires them into one LangGraph.
"""
import re
import sqlite3

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from config import RETRY_BUDGET, SHOP_DB
from rag import advise
from single_agent import classify, is_failure, latest_tool_results, sql_for

EXECUTOR_RULES = """
- Always call run_sql first, even if you think a request is not allowed. The tool decides what may run.
- Messages from the monitor or recovery agent are about your last attempt. Follow their advice
  and call run_sql again. Never repeat a number the monitor has quarantined."""

SENSITIVE_WORDS = ("email", "phone")
QUARANTINE_TEXT = "[Quarantined by the Monitor: this result failed a sanity check and must not be used.]"


def ok(event):
    return {"verdict": {"status": "ok"}, "events": [event]}


# ------------------------------------------------------------------ Monitor

def load_limits():
    """What is physically possible in this shop. Worked out from the data, not typed in."""
    with sqlite3.connect(f"file:{SHOP_DB}?mode=ro", uri=True) as conn:
        gross = conn.execute("SELECT SUM(oi.quantity * p.unit_price) FROM order_items oi "
                             "JOIN products p ON p.product_id = oi.product_id").fetchone()[0]
        largest_order = conn.execute("SELECT MAX(total) FROM (SELECT SUM(oi.quantity * p.unit_price) AS total "
                                     "FROM order_items oi JOIN products p ON p.product_id = oi.product_id "
                                     "GROUP BY oi.order_id)").fetchone()[0]
        largest_table = max(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                            for t in ("customers", "products", "orders", "order_items"))
    return {"gross": gross, "max_order": largest_order, "max_count": largest_table}


def parse_rows(text):
    """Turn the tool's text table back into (columns, rows). None if it is not a table."""
    lines = text.splitlines()
    if len(lines) < 3 or "row(s)" not in lines[0]:
        return None
    columns = [c.strip() for c in lines[1].split("|")]
    rows = [[c.strip() for c in line.split("|")] for line in lines[2:]]
    return columns, rows


def to_number(cell):
    try:
        return float(cell)
    except ValueError:
        return None


def sanity_problems(columns, rows, limits):
    """Reasons why a result cannot be true. Empty list means it looks fine."""
    problems = []
    per_order = any(c.lower().endswith("order_id") for c in columns)   # one row = one order
    for row in rows:
        for column, cell in zip(columns, row):
            value, name = to_number(cell), column.lower()
            # ids, phone numbers and the like are labels, not amounts
            if value is None or name == "id" or name.endswith("_id") or "phone" in name:
                continue
            if value < 0:
                problems.append(f"{column} = {cell} is negative")
            elif value > limits["gross"]:
                problems.append(f"{column} = {cell} is more than the shop's all-time gross sales "
                                f"(Rs {limits['gross']:,.0f})")
            elif per_order and value > limits["max_order"]:
                problems.append(f"{column} = {cell} is more than the largest real order "
                                f"(Rs {limits['max_order']:,.0f})")
            elif re.search(r"count|num|number", name) and value > limits["max_count"]:
                problems.append(f"{column} = {cell} is more than the largest table ({limits['max_count']} rows)")
    return problems[:3]


AGGREGATE = re.compile(r"\b(sum|count|avg|total|group_concat)\s*\(", re.IGNORECASE)


def select_items(sql):
    """The comma-separated items between the first SELECT and its FROM, ignoring brackets."""
    low = sql.lower()
    start = low.find("select")
    if start < 0:
        return []
    items, depth, current, i = [], 0, "", start + len("select")
    while i < len(sql):
        ch = sql[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth == 0 and re.match(r"\sfrom\b", low[i:i + 6]):
            break
        if depth == 0 and ch == ",":
            items.append(current.strip())
            current = ""
        else:
            current += ch
        i += 1
    return [item for item in items + [current.strip()] if item]


def ungrouped_columns(sql):
    """Plain columns sitting next to SUM/COUNT/AVG in a query with no GROUP BY.

    SQLite runs such a query without any error and fills the plain column from
    one arbitrary row, so the result looks fine and is wrong. MIN and MAX are left
    out because SQLite defines that case (the row with the min or max).
    """
    low = sql.lower().strip()
    if low.startswith("with") or "group by" in low:
        return []
    items = select_items(sql)
    if not any(AGGREGATE.search(item) for item in items):
        return []
    return [item for item in items
            if not AGGREGATE.search(item) and " over" not in item.lower()
            and not re.fullmatch(r"[\d.]+|'[^']*'", item)]


def sensitive_columns(columns):
    return [c for c in columns if any(word in c.lower() for word in SENSITIVE_WORDS)]


def check_tool_results(messages, limits):
    """The Monitor's verdict on the SQL results of the latest turn."""
    for message in latest_tool_results(messages):
        if message.name != "run_sql":
            continue
        sql = sql_for(messages, message.tool_call_id)
        if is_failure(message):
            kind = classify(message.content)
            return {"verdict": {"status": "failure", "kind": kind, "detail": message.content, "sql": sql},
                    "last_problem": message.content,
                    "events": [f"monitor: tool error, type '{kind}'"]}

        parsed = parse_rows(message.content)
        if not parsed:
            continue
        columns, rows = parsed
        ungrouped = ungrouped_columns(sql)
        if ungrouped:
            quarantined = ToolMessage(content=QUARANTINE_TEXT, tool_call_id=message.tool_call_id,
                                      id=message.id, name=message.name)
            names = ", ".join(c.split(" AS ")[0].split(" as ")[0] for c in ungrouped)
            detail = f"{names} sits next to SUM/COUNT/AVG but the query has no GROUP BY"
            return {"messages": [quarantined],
                    "verdict": {"status": "failure", "kind": "no_group_by", "detail": detail,
                                "sql": sql, "group_by": names},
                    "last_problem": "the query was missing a GROUP BY, so its numbers were not used.",
                    "events": [f"monitor: silent bad result quarantined ({detail})"]}
        problems = sanity_problems(columns, rows, limits)
        if problems:
            # Containment: swap the bad rows out of the shared history so no agent can reuse them.
            quarantined = ToolMessage(content=QUARANTINE_TEXT, tool_call_id=message.tool_call_id,
                                      id=message.id, name=message.name)
            detail = "; ".join(problems)
            return {"messages": [quarantined],
                    "verdict": {"status": "failure", "kind": "bad_result", "detail": detail, "sql": sql},
                    "last_problem": "the result failed the Monitor's sanity check, so it was not used.",
                    "events": [f"monitor: silent bad result quarantined ({detail})"]}
        if sensitive_columns(columns):
            return {"verdict": {"status": "needs_approval", "columns": sensitive_columns(columns)},
                    "events": [f"monitor: personal data in result {sensitive_columns(columns)}, asking a person"]}
    return ok("monitor: result looks fine")


def numbers_in(text):
    found = []
    for raw in re.findall(r"-?\d[\d,]*(?:\.\d+)?", text):
        try:
            found.append(float(raw.replace(",", "")))
        except ValueError:
            pass
    return found


def single_value(messages):
    """The one number in this turn's last good result, if the result is a single number."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and message.name is None:
            return None   # reached the user's question, so this turn has no usable result
        if isinstance(message, ToolMessage) and message.name == "run_sql":
            parsed = parse_rows(message.content)
            if not parsed or len(parsed[1]) != 1:
                return None
            values = [to_number(c) for c in parsed[1][0]]
            values = [v for v in values if v is not None]
            return values[0] if len(values) == 1 else None
    return None


def latest_result(messages):
    """This turn's most recent run_sql result, or None if the executor never queried."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and message.name is None:
            return None
        if isinstance(message, ToolMessage) and message.name == "run_sql":
            return message
    return None


def check_answer(messages):
    """The Monitor's verdict on the Executor's final answer."""
    result = latest_result(messages)
    if result is not None and (is_failure(result) or result.content == QUARANTINE_TEXT):
        detail = f"the answer was written without a valid result (last result: {result.content[:90]})"
        return {"verdict": {"status": "failure", "kind": "unsupported_answer", "detail": detail, "sql": ""},
                "events": ["monitor: answer rejected, it is not backed by a valid result"]}

    value = single_value(messages)
    if value is None:
        return ok("monitor: answer accepted")
    answer = messages[-1].content
    tolerance = max(0.01, abs(value) * 0.001)
    if any(abs(n - value) <= tolerance for n in numbers_in(answer)):
        return ok(f"monitor: answer matches the data ({value:g})")
    detail = f"the rows say {value:g}, but the answer does not contain that number"
    return {"verdict": {"status": "failure", "kind": "answer_mismatch", "detail": detail, "sql": ""},
            "events": [f"monitor: answer does not match the data ({detail})"]}


# --------------------------------------------------------------- privacy gate

def mask_sensitive(text, columns_to_hide):
    columns, rows = parse_rows(text)
    hide = [i for i, c in enumerate(columns) if c in columns_to_hide]
    masked = [[("(hidden)" if i in hide else cell) for i, cell in enumerate(row)] for row in rows]
    lines = [text.splitlines()[0], " | ".join(columns)] + [" | ".join(r) for r in masked]
    return "\n".join(lines)


# ------------------------------------------------------------------ Recovery

def decide_recovery(verdict, retries_left):
    """What Recovery does next. Returns (next_status, retries_left, advice, guide_sections)."""
    kind = verdict["kind"]
    if kind in ("unsafe", "unavailable"):
        return "stop", retries_left, None, []
    if retries_left <= 0:
        return "stop", 0, None, []

    retries_left -= 1
    if kind == "unsupported_answer":
        return "retry", retries_left, "Do not answer yet. Run a corrected query first and answer from its rows.", []
    if kind == "no_group_by":
        return "retry", retries_left, (f"The last result was quarantined: {verdict['detail']}, so SQLite "
                                       f"returned one arbitrary row. Keep the query, add GROUP BY "
                                       f"{verdict['group_by']} before ORDER BY, and run it again."), []
    if kind == "answer_mismatch":
        return "retry", retries_left, f"Your answer must quote the exact number from the rows: {verdict['detail']}.", []
    if kind == "bad_result":
        advice, sections = advise(f"Suspicious numbers, silent failure: {verdict['detail']}", verdict["sql"])
        return "retry", retries_left, f"The last result was quarantined. Run the query again. {advice}", sections
    advice, sections = advise(verdict["detail"], verdict["sql"])
    return "retry", retries_left, advice, sections


def advice_message(advice, retries_left):
    return HumanMessage(name="recovery", content=(
        f"[recovery -> executor] {advice}\nTry again ({retries_left} "
        f"{'retry' if retries_left == 1 else 'retries'} left)."))


def stop_message(verdict, last_problem):
    """The safe final answer when the team stops. Written by rules, so it never guesses."""
    kind = verdict["kind"]
    if kind == "unsafe":
        text = "I can only read the shop data, so I can't change or delete anything. Nothing was modified."
    elif kind == "unavailable":
        text = ("The database can't be reached right now, so I can't answer without guessing. "
                "Please try again later. This has been logged.")
    else:
        text = (f"I couldn't get a reliable answer, even after {RETRY_BUDGET} retries. "
                f"The last problem was: {last_problem or verdict['detail']} I've logged it for a person to check "
                "instead of guessing.")
    return AIMessage(content=text, name="recovery")
