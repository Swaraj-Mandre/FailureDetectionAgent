"""PBA 2, Unit 3 milestone: the PBA 1 agent plus memory, RAG and a failure log.

Still ONE agent (the SOP says to finish and test Unit 3 before adding agents).
Compared with ta1/agent.py:
- a SQLite checkpointer remembers the conversation per thread_id and lets a
  crashed run continue from its last saved step
- a SQLite store keeps the user's business glossary across threads and restarts
- handle_failure asks the error guide (RAG) for specific advice instead of
  using fixed hints
- every failure is written to the failure log
"""
import os

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from config import CHAT_MODEL, DEFAULT_USER, RETRY_BUDGET
from memory import FailureLog, glossary_text, make_glossary_tool
from rag import advise
from sql_tool import run_sql

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
- If the user tells you what a business word means, save it with remember_term.
- When you have the result, answer in one or two short sentences.

Example. Question: revenue per category in August 2026?
SELECT p.category, SUM(oi.quantity * p.unit_price * (1 - oi.discount_pct / 100.0)) AS revenue
FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
JOIN products p ON p.product_id = oi.product_id
WHERE o.status = 'delivered' AND o.order_date LIKE '2026-08%'
GROUP BY p.category ORDER BY revenue DESC"""

STOP_MESSAGES = {
    "unsafe": "Do not call the tool again. Tell the user plainly that you can only read data "
              "and cannot change or delete anything. Do not give them SQL to run themselves.",
    "unavailable": "Do not call the tool again. Tell the user the database cannot be reached "
                   "right now and they should try again later. Do not guess any numbers.",
    "gave_up": "Stop calling the tool. Tell the user you could not get a reliable answer, "
               "and say in one line what kept failing.",
}


class AgentState(MessagesState):
    failures: int
    tools_locked: bool


def latest_tool_results(messages):
    results = []
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            break
        results.append(message)
    return list(reversed(results))


def is_failure(message):
    return message.content.startswith("Error:") or message.status == "error"


def classify(error):
    text = error.lower()
    if "read-only" in text:
        return "unsafe"
    if "could not connect" in text or "not found. run" in text:
        return "unavailable"
    if "locked" in text:
        return "busy"
    if "no such column" in text or "no such table" in text or "ambiguous column" in text:
        return "schema"
    if "syntax error" in text or "no such function" in text or "statement" in text or "must start" in text:
        return "syntax"
    if "invoking tool" in text or "validation error" in text:
        return "arguments"
    return "other"


def sql_for(messages, tool_call_id):
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                if call["id"] == tool_call_id:
                    return call["args"].get("query", "")
    return ""


def build_agent(checkpointer=None, store=None, user_id=DEFAULT_USER, failure_log=None):
    """Compile the graph. Leave checkpointer out to see the agent forget (amnesia demo)."""
    failure_log = failure_log or FailureLog()
    tools = [run_sql]
    if store is not None:
        tools.append(make_glossary_tool(store, user_id))

    llm = ChatOllama(model=CHAT_MODEL, temperature=0)
    llm_with_tools = llm.bind_tools(tools)

    def call_model(state: AgentState):
        # Crash switch for the resume demo: die right after a tool result was saved.
        if os.environ.get("QM_CRASH") == "after_tools" and isinstance(state["messages"][-1], ToolMessage):
            raise RuntimeError("Simulated crash: the process died after the tool ran.")

        prompt = SYSTEM_PROMPT + (glossary_text(store, user_id) if store is not None else "")
        model = llm if state.get("tools_locked") else llm_with_tools
        return {"messages": [model.invoke([SystemMessage(content=prompt)] + state["messages"])]}

    def route_after_tools(state: AgentState):
        if any(is_failure(m) for m in latest_tool_results(state["messages"])):
            return "handle_failure"
        return "agent"

    def handle_failure(state: AgentState, config: RunnableConfig):
        failed = next(m for m in latest_tool_results(state["messages"]) if is_failure(m))
        error, sql = failed.content, sql_for(state["messages"], failed.tool_call_id)
        kind = classify(error)
        failures = state.get("failures", 0) + 1
        left = RETRY_BUDGET - failures

        if kind in ("unsafe", "unavailable"):
            text, locked, action = f"The tool refused: {error}\n{STOP_MESSAGES[kind]}", True, "stopped: not retryable"
        elif left <= 0:
            text, locked, action = (f"The tool failed {failures} times. Last error: {error}\n"
                                    f"{STOP_MESSAGES['gave_up']}"), True, "stopped: retry budget used up"
        else:
            advice, sections = advise(error, sql)
            text = (f"The tool failed with: {error}\nAdvice from the error guide: {advice}\n"
                    f"Write a corrected query and call run_sql again ({left} {'try' if left == 1 else 'tries'} left).")
            locked, action = False, f"retry with advice from: {', '.join(sections)}"

        thread_id = config.get("configurable", {}).get("thread_id")
        failure_log.add(thread_id, "single_agent", kind, sql, error, action, max(left, 0))
        notice = HumanMessage(content=text, name="failure_handler")
        return {"messages": [notice], "failures": failures, "tools_locked": locked}

    builder = StateGraph(AgentState)
    builder.add_node("agent", call_model)
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("handle_failure", handle_failure)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_conditional_edges("tools", route_after_tools,
                                  {"handle_failure": "handle_failure", "agent": "agent"})
    builder.add_edge("handle_failure", "agent")
    return builder.compile(checkpointer=checkpointer, store=store)


def new_turn(question):
    """Input for one user turn. Counters reset each turn; the messages keep growing per thread."""
    return {"messages": [HumanMessage(content=question)], "failures": 0, "tools_locked": False}
