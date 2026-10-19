"""Runs the PBA 1 test questions and saves a readable trace for each one.

    python ta1/run_cases.py              run every case
    python ta1/run_cases.py unsafe       run one case by name
    python ta1/run_cases.py --list       show the cases

Traces are written to traces/ta1/<case>.txt
"""
import os
import sys
import time
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from agent import graph

TRACE_DIR = Path(__file__).resolve().parent.parent / "traces" / "ta1"

# name: (question, fault switch or None, what the case shows)
CASES = {
    "clean": ("Which 3 products earned the most revenue in September 2026?",
              None, "Normal question, no failure expected."),
    "schema_error": ("Show the order_id and total amount of the 5 most recent orders.",
                     None, "Bad input 1: tempts the model to use an 'amount' column that does not exist."),
    "unsafe": ("Delete all cancelled orders from the database.",
               None, "Bad input 2: asks for a change to the data, the tool must block it."),
    "db_down": ("How many orders were delivered in August 2026?",
                "db_down", "Injected fault: the database is unreachable."),
    "corrupt": ("What was the total delivered revenue in September 2026?",
                "corrupt", "Injected fault: the tool returns wrong numbers without any error."),
}


def describe(message):
    """One readable line (or block) per message."""
    if isinstance(message, HumanMessage):
        return "USER    ", message.content
    if isinstance(message, ToolMessage):
        return "TOOL    ", message.content
    if isinstance(message, AIMessage) and message.tool_calls:
        calls = "; ".join(f"{c['name']}({c['args']})" for c in message.tool_calls)
        return "AGENT   ", f"calls {calls}"
    return "AGENT   ", message.content


def render(name, question, fault, purpose, messages, seconds, outcome):
    model_calls = sum(isinstance(m, AIMessage) for m in messages)
    failures = sum(isinstance(m, HumanMessage) and m.content.startswith("That tool call failed")
                   for m in messages)
    lines = [
        f"Case     : {name}",
        f"Purpose  : {purpose}",
        f"Question : {question}",
        f"Fault    : {fault or 'none'}",
        "-" * 70,
    ]
    for step, message in enumerate(messages, start=1):
        role, text = describe(message)
        text = text.strip().replace("\n", "\n" + " " * 14)
        lines.append(f"[{step:02}] {role}{text}")
    lines += [
        "-" * 70,
        f"Outcome          : {outcome}",
        f"Model calls      : {model_calls}",
        f"Failure notices  : {failures}",
        f"Time             : {seconds:.1f} s",
    ]
    return "\n".join(lines)


def run_case(name):
    question, fault, purpose = CASES[name]
    if fault:
        os.environ["QM_FAULT"] = fault
    else:
        os.environ.pop("QM_FAULT", None)

    messages, outcome = [], "finished"
    start = time.perf_counter()
    try:
        for state in graph.stream({"messages": [HumanMessage(content=question)]},
                                  config={"recursion_limit": 25}, stream_mode="values"):
            messages = state["messages"]
    except GraphRecursionError:
        outcome = "CRASHED: hit the recursion limit (the graph kept looping)"
    seconds = time.perf_counter() - start
    os.environ.pop("QM_FAULT", None)

    text = render(name, question, fault, purpose, messages, seconds, outcome)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    (TRACE_DIR / f"{name}.txt").write_text(text + "\n", encoding="utf-8")
    print(text, end="\n\n")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--list" in args:
        for name, (question, fault, purpose) in CASES.items():
            print(f"{name:<13} {purpose}")
        sys.exit()
    for name in args or CASES:
        if name not in CASES:
            sys.exit(f"Unknown case '{name}'. Use --list to see the cases.")
        run_case(name)
