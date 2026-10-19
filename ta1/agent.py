"""PBA 1: a single agent that detects and recovers from its own tool failures.

Same graph shape as Unit II Session 4, with refined failure handling:

    START -> agent --(tool call?)--> tools --(route_after_tools)--> agent
               |                                  |
               '--(no tool call)--> END           '--> handle_failure --> agent

What changed from the slide version (agent_v1_slide.py), and why, is written
up in REFINEMENT_NOTES.md. The system prompt is identical in both files.

    python ta1/agent.py "Top 3 products by revenue in September"
"""
import re
import sys

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_ollama import ChatOllama
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from sql_tool import run_sql

MODEL = "qwen2.5:3b"
MAX_FAILURES = 3  # retryable failures allowed before the agent gives up

SYSTEM_PROMPT = """You answer questions about the Sahyadri Stores shop database.
Always use the run_sql tool to get the data. Never make up numbers.

Tables (SQLite):
- customers(customer_id, name, city, email, phone, joined_on)
- products(product_id, name, category, unit_price)
- orders(order_id, customer_id, order_date, status, payment_method)
- order_items(order_id, product_id, quantity, discount_pct)

Rules:
- Revenue of a line is oi.quantity * p.unit_price * (1 - oi.discount_pct / 100.0),
  using order_items oi joined to products p. There is no amount or total column.
- Count only orders with status = 'delivered' for revenue and sales.
- order_date is text like '2026-09-14'. All orders are from April to September 2026.
- Money is in Indian rupees (Rs).
- When you have the result, answer in one or two short sentences.

Example. Question: revenue per category in August 2026?
SELECT p.category, SUM(oi.quantity * p.unit_price * (1 - oi.discount_pct / 100.0)) AS revenue
FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
JOIN products p ON p.product_id = oi.product_id
WHERE o.status = 'delivered' AND o.order_date LIKE '2026-08%'
GROUP BY p.category ORDER BY revenue DESC"""

# Failure kind -> what to tell the model. Unsafe and unavailable are not worth
# retrying: a new query cannot make a blocked DELETE allowed or bring the
# database back, so the agent explains instead of trying again.
HINTS = {
    "schema": "Check every table and column against the schema. quantity and discount_pct "
              "are in order_items, unit_price is in products. If a column exists in two "
              "tables, prefix it with the table name or alias.",
    "syntax": "This is SQLite. There is no YEAR() or MONTH(). For a month use "
              "order_date LIKE '2026-09%'. strftime() returns text, so compare it with "
              "text like '09', not a number. Send one SELECT only.",
    "arguments": "Call run_sql with a single argument named query that holds the SQL text.",
    "busy": "The database was busy for a moment. You may send the same query again.",
    "other": "Read the error carefully and fix the query.",
}
STOP_MESSAGES = {
    "unsafe": "Do not call the tool again. Tell the user plainly that you can only read data "
              "and cannot change or delete anything. Do not give them SQL to run themselves.",
    "unavailable": "Do not call the tool again. Tell the user the database cannot be reached "
                   "right now and they should try again later. Do not guess any numbers.",
    "gave_up": "Stop calling the tool. Tell the user you could not get a reliable answer, "
               "and say in one line what kept failing.",
}


class AgentState(MessagesState):
    failures: int        # retryable tool failures so far
    tools_locked: bool   # True once retrying is pointless; the model must answer in words


tools = [run_sql]
llm = ChatOllama(model=MODEL, temperature=0)
llm_with_tools = llm.bind_tools(tools)


def call_model(state: AgentState):
    """The agent node. This is the Thought step."""
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    # Once tools are locked the model gets no tools at all, so it cannot keep retrying.
    model = llm if state.get("tools_locked") else llm_with_tools
    return {"messages": [model.invoke(messages)]}


def latest_tool_results(messages):
    """All ToolMessages produced after the last agent turn (there can be more than one)."""
    results = []
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            break
        results.append(message)
    return list(reversed(results))


def is_failure(message):
    # Our tool starts every error with "Error:". ToolNode marks bad arguments with status="error".
    return message.content.startswith("Error:") or message.status == "error"


def route_after_tools(state: AgentState):
    """Send the run to handle_failure if any tool result from this turn is an error."""
    if any(is_failure(m) for m in latest_tool_results(state["messages"])):
        return "handle_failure"
    return "agent"


def classify(error):
    text = error.lower()
    if "read-only" in text:
        return "unsafe"
    if "could not connect" in text or "not found. run" in text:
        return "unavailable"
    if "no such column" in text or "no such table" in text or "ambiguous column" in text:
        return "schema"
    if "syntax error" in text or "no such function" in text or "statement" in text or "must start" in text:
        return "syntax"
    if "locked" in text:
        return "busy"
    if "invoking tool" in text or "validation error" in text:
        return "arguments"
    return "other"


def failed_queries(messages):
    """SQL text of every tool call whose result was an error, oldest first."""
    calls = {}
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                calls[call["id"]] = call["args"].get("query", "")
    return [calls.get(m.tool_call_id, "") for m in messages
            if isinstance(m, ToolMessage) and is_failure(m)]


def handle_failure(state: AgentState):
    error = next(m for m in latest_tool_results(state["messages"]) if is_failure(m)).content
    kind = classify(error)
    failures = state.get("failures", 0) + 1

    if kind in ("unsafe", "unavailable"):
        text, locked = f"The tool refused: {error}\n{STOP_MESSAGES[kind]}", True
    elif failures >= MAX_FAILURES:
        text, locked = f"The tool failed {failures} times. Last error: {error}\n{STOP_MESSAGES['gave_up']}", True
    else:
        text = f"The tool failed with: {error}\nHint: {HINTS[kind]}"
        tried = failed_queries(state["messages"])
        if kind != "busy" and tried.count(tried[-1]) > 1:
            text += "\nYou already sent this exact query before. Change it."
        left = MAX_FAILURES - failures
        text += f"\nWrite a corrected query and call run_sql again ({left} {'try' if left == 1 else 'tries'} left)."
        locked = False

    notice = HumanMessage(content=text, name="failure_handler")
    return {"messages": [notice], "failures": failures, "tools_locked": locked}


def build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("agent", call_model)
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("handle_failure", handle_failure)

    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_conditional_edges("tools", route_after_tools,
                                  {"handle_failure": "handle_failure", "agent": "agent"})
    builder.add_edge("handle_failure", "agent")
    return builder.compile()


graph = build_graph()


def ask(question, recursion_limit=25):
    """Run one question through the graph and return the final state."""
    return graph.invoke({"messages": [HumanMessage(content=question)], "failures": 0, "tools_locked": False},
                        config={"recursion_limit": recursion_limit})


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "Which 3 products earned the most revenue in September 2026?"
    result = ask(question)
    print(result["messages"][-1].content)
