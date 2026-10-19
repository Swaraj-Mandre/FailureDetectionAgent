"""Wires Executor, Monitor and Recovery into one LangGraph.

    START -> executor --(tool call)--> tools -> monitor_result --(ok)--------------> executor
                |                                     |--(personal data)--> privacy_gate -> executor
                |                                     '--(failure)--> recovery
                '--(final answer)--> monitor_answer --(ok)--> END
                                          '--(failure)--> recovery

    recovery --(retries left)--> executor
             '--(not retryable, or budget used up)--> stop -> END

The retry budget is the hop limit: every trip through recovery costs one retry,
and at zero the team stops instead of looping.
"""
import operator
from typing import Annotated

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

import agents
from config import CHAT_MODEL, DEFAULT_USER, RETRY_BUDGET
from memory import FailureLog, glossary_text, make_checkpointer, make_glossary_tool
from single_agent import SYSTEM_PROMPT, latest_tool_results
from sql_tool import run_sql


class TeamState(MessagesState):
    retries_left: int
    verdict: dict                               # the Monitor's latest verdict
    last_problem: str                           # the last real failure, quoted when the team stops
    events: Annotated[list[str], operator.add]  # one line per routing decision, for the traces


def team_turn(question):
    """Input for one user turn. The retry budget refills every turn."""
    return {"messages": [HumanMessage(content=question)], "retries_left": RETRY_BUDGET,
            "verdict": {}, "last_problem": "", "events": [f"user: {question}"]}


def build_team(checkpointer=None, store=None, user_id=DEFAULT_USER, monitor_on=True, failure_log=None):
    """monitor_on=False turns the Monitor into a pass-through, for the containment demo."""
    checkpointer = checkpointer or make_checkpointer()   # the privacy gate needs one to pause
    failure_log = failure_log or FailureLog()
    limits = agents.load_limits()

    tools = [run_sql] + ([make_glossary_tool(store, user_id)] if store is not None else [])
    executor_llm = ChatOllama(model=CHAT_MODEL, temperature=0).bind_tools(tools)

    # ---- Executor
    def executor(state: TeamState):
        prompt = SYSTEM_PROMPT + agents.EXECUTOR_RULES
        if store is not None:
            prompt += glossary_text(store, user_id)
        reply = executor_llm.invoke([SystemMessage(content=prompt)] + state["messages"])
        event = "executor: calls run_sql" if reply.tool_calls else "executor: writes the answer"
        return {"messages": [reply], "events": [event]}

    def after_executor(state: TeamState):
        return "tools" if state["messages"][-1].tool_calls else "monitor_answer"

    # ---- Monitor
    def monitor_result(state: TeamState):
        if not monitor_on:
            return agents.ok("monitor: OFF, result passed on unchecked")
        return agents.check_tool_results(state["messages"], limits)

    def monitor_answer(state: TeamState):
        if not monitor_on:
            return agents.ok("monitor: OFF, answer passed on unchecked")
        return agents.check_answer(state["messages"])

    def after_monitor_result(state: TeamState):
        return {"ok": "executor", "failure": "recovery", "needs_approval": "privacy_gate"}[state["verdict"]["status"]]

    def after_monitor_answer(state: TeamState):
        return "recovery" if state["verdict"]["status"] == "failure" else END

    # ---- Privacy gate (human in the loop)
    def privacy_gate(state: TeamState):
        columns = state["verdict"]["columns"]
        decision = interrupt({"question": f"The answer includes personal data {columns}. Show it?",
                              "options": ["approve", "reject"]})
        if decision == "approve":
            return {"verdict": {"status": "ok"}, "events": ["privacy gate: a person APPROVED, data shown"]}
        result = next(m for m in latest_tool_results(state["messages"]) if m.name == "run_sql")
        masked = ToolMessage(content=agents.mask_sensitive(result.content, columns),
                             tool_call_id=result.tool_call_id, id=result.id, name=result.name)
        return {"messages": [masked], "verdict": {"status": "ok"},
                "events": [f"privacy gate: a person REJECTED, {columns} masked"]}

    # ---- Recovery
    def recovery(state: TeamState, config: RunnableConfig):
        verdict = state["verdict"]
        status, left, advice, sections = agents.decide_recovery(verdict, state["retries_left"])
        thread_id = config.get("configurable", {}).get("thread_id")

        if status == "stop":
            why = "not retryable" if verdict["kind"] in ("unsafe", "unavailable") else "retry budget used up"
            failure_log.add(thread_id, "monitor", verdict["kind"], verdict.get("sql"), verdict["detail"],
                            f"recovery stopped: {why}", left)
            return {"verdict": {**verdict, "status": "stop"}, "retries_left": left,
                    "events": [f"recovery: STOP ({why})"]}

        source = f"guide sections {sections}" if sections else "the monitor's finding"
        failure_log.add(thread_id, "monitor", verdict["kind"], verdict.get("sql"), verdict["detail"],
                        f"recovery advised a retry using {source}", left)
        return {"messages": [agents.advice_message(advice, left)], "retries_left": left,
                "verdict": {**verdict, "status": "retry"},
                "events": [f"recovery: advice from {source}, {left} {'retry' if left == 1 else 'retries'} left"]}

    def after_recovery(state: TeamState):
        return "executor" if state["verdict"]["status"] == "retry" else "stop"

    def stop(state: TeamState):
        return {"messages": [agents.stop_message(state["verdict"], state.get("last_problem"))],
                "events": ["stop: safe final answer written, no guessing"]}

    builder = StateGraph(TeamState)
    builder.add_node("executor", executor)
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("monitor_result", monitor_result)
    builder.add_node("monitor_answer", monitor_answer)
    builder.add_node("privacy_gate", privacy_gate)
    builder.add_node("recovery", recovery)
    builder.add_node("stop", stop)

    builder.add_edge(START, "executor")
    builder.add_conditional_edges("executor", after_executor, ["tools", "monitor_answer"])
    builder.add_edge("tools", "monitor_result")
    builder.add_conditional_edges("monitor_result", after_monitor_result, ["executor", "recovery", "privacy_gate"])
    builder.add_edge("privacy_gate", "executor")
    builder.add_conditional_edges("monitor_answer", after_monitor_answer, ["recovery", END])
    builder.add_conditional_edges("recovery", after_recovery, ["executor", "stop"])
    builder.add_edge("stop", END)
    return builder.compile(checkpointer=checkpointer, store=store)
