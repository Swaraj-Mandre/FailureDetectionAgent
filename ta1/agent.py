"""PBA 1: a single agent that detects and recovers from its own tool failures.

The graph is built by hand, the same way as Unit II Session 4:

    START -> agent --(tool call?)--> tools --(route_after_tools)--> agent
               |                                  |
               '--(no tool call)--> END           '--> handle_failure --> agent

Ask one question from the command line:
    python ta1/agent.py "Top 3 products by revenue in September"
"""
import sys

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from sql_tool import run_sql

MODEL = "qwen2.5:3b"

SYSTEM_PROMPT = """You answer questions about the Sahyadri Stores shop database.
Always use the run_sql tool to get the data. Never make up numbers.

Tables (SQLite):
- customers(customer_id, name, city, email, phone, joined_on)
- products(product_id, name, category, unit_price)
- orders(order_id, customer_id, order_date, status, payment_method)
- order_items(order_id, product_id, quantity, discount_pct)

Rules:
- Revenue = quantity * unit_price * (1 - discount_pct / 100.0). There is no amount or total column.
- Count only orders with status = 'delivered' for revenue and sales.
- order_date is text like '2026-09-14'. All orders are from April to September 2026.
- When you have the result, answer in one or two short sentences."""

tools = [run_sql]
llm = ChatOllama(model=MODEL, temperature=0)
llm_with_tools = llm.bind_tools(tools)


def call_model(state: MessagesState):
    """The agent node. This is the Thought step."""
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}


def route_after_tools(state: MessagesState):
    """If the last tool result looks like an error, go fix it up."""
    last_message = state["messages"][-1]
    if "Error" in last_message.content:
        return "handle_failure"
    return "agent"


def handle_failure(state: MessagesState):
    notice = HumanMessage(content="That tool call failed. Try a different approach, or explain why.")
    return {"messages": [notice]}


def build_graph():
    builder = StateGraph(MessagesState)
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
    return graph.invoke({"messages": [HumanMessage(content=question)]},
                        config={"recursion_limit": recursion_limit})


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "Which 3 products earned the most revenue in September 2026?"
    result = ask(question)
    print(result["messages"][-1].content)
