"""A small web page for the live demo. The agents are exactly the ones in graph.py.

    streamlit run ta2/app.py

Needs streamlit (pip install streamlit), only for this page. Everything else in
ta2/ runs without it.
"""
import html
import os
import sys
import uuid
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from langgraph.types import Command  # noqa: E402

from config import RETRY_BUDGET  # noqa: E402
from graph import build_team, team_turn  # noqa: E402
from memory import FailureLog, load_terms, make_checkpointer, make_store  # noqa: E402
from sql_tool import reset_faults  # noqa: E402

FAULTS = {
    "None": None,
    "Prices 100x too big, once (silent)": "corrupt_once",
    "Prices 100x too big, every time (silent)": "corrupt",
    "Garbled query, once (syntax error)": "garble_once",
    "Database busy twice, then fine": "flaky",
    "Database busy, never clears": "locked",
    "Database unreachable": "db_down",
}

EXAMPLES = [
    "Which 3 products earned the most revenue in September 2026?",
    "What was the total delivered revenue in September 2026?",
    "Show the order_id and total amount of the 5 most recent orders.",
    "Show the name, email and phone of customers who live in Pune.",
    "Delete all cancelled orders from the database.",
]

# role colours, the same as the slides and the report
ROLE_STYLE = {
    "user": ("#64748B", "#F1F5F9", "You"),
    "executor": ("#2563EB", "#DBEAFE", "Executor"),
    "monitor": ("#D97706", "#FEF3C7", "Monitor"),
    "recovery": ("#059669", "#D1FAE5", "Recovery"),
    "privacy gate": ("#7C3AED", "#EDE9FE", "Person"),
    "stop": ("#E11D48", "#FFE4E6", "Stop"),
}

st.set_page_config(page_title="QueryMend", page_icon="🔎", layout="wide")
st.markdown("""
<style>
  h1, h2, h3 { font-family: Bahnschrift, 'Segoe UI', sans-serif !important; color: #14213D; }
  .bars { display: flex; gap: 4px; margin-bottom: 4px; } .bars i { width: 26px; height: 5px; border-radius: 2px; display: block; }
  .step { display: flex; gap: 10px; align-items: baseline; margin: 3px 0; font-size: 0.92rem; }
  .who { font-weight: 600; font-size: 0.75rem; padding: 1px 8px; border-radius: 10px; min-width: 74px; text-align: center; }
  .stats { color: #64748B; font-size: 0.85rem; }
</style>""", unsafe_allow_html=True)


@st.cache_resource
def shared_memory():
    return make_checkpointer(), make_store(), FailureLog()


@st.cache_resource
def team_for(monitor_on, user_id):
    checkpointer, store, failure_log = shared_memory()
    return build_team(checkpointer=checkpointer, store=store, user_id=user_id,
                      monitor_on=monitor_on, failure_log=failure_log)


def set_fault(fault):
    os.environ.pop("QM_FAULT", None)
    if fault:
        os.environ["QM_FAULT"] = fault
    reset_faults()


def plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def step_html(event):
    role, _, text = event.partition(": ")
    colour, fill, label = ROLE_STYLE.get(role, ("#64748B", "#F1F5F9", role))
    return (f'<div class="step"><span class="who" style="color:{colour};background:{fill}">{label}</span>'
            f'<span>{html.escape(text)}</span></div>')


def summarise(result, seen_messages, seen_events):
    """Pull out what the page shows for one finished turn."""
    messages, events = result["messages"][seen_messages:], result["events"][seen_events:]
    queries = [call["args"].get("query", "") for m in messages if isinstance(m, AIMessage)
               for call in m.tool_calls if call["name"] == "run_sql"]
    return {
        "answer": messages[-1].content,
        "queries": queries,
        "events": [e for e in events if not e.startswith("user:")],
        "retries": sum(e.startswith("recovery: advice") for e in events),
        "model_calls": sum(isinstance(m, AIMessage) and m.name != "recovery" for m in messages),
        "tool_calls": sum(isinstance(m, ToolMessage) for m in messages),
    }


def ask(question, team, fault):
    config = {"configurable": {"thread_id": st.session_state.thread}}
    before = team.get_state(config).values
    seen = (len(before.get("messages", [])), len(before.get("events", [])))
    set_fault(fault)
    try:
        result = team.invoke(team_turn(question), config=config)
    finally:
        set_fault(None)
    turn = {"question": question, "fault": fault, "seen": seen}
    if "__interrupt__" in result:
        turn["pending"] = result["__interrupt__"][0].value["question"]
    else:
        turn.update(summarise(result, *seen))
    st.session_state.turns.append(turn)


def resume(decision, team):
    turn = st.session_state.turns[-1]
    config = {"configurable": {"thread_id": st.session_state.thread}}
    result = team.invoke(Command(resume=decision), config=config)
    turn.pop("pending")
    turn.update(summarise(result, *turn["seen"]))


if "thread" not in st.session_state:
    st.session_state.thread = f"web-{uuid.uuid4().hex[:8]}"
    st.session_state.turns = []

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown('<div class="bars"><i style="background:#2563EB"></i><i style="background:#D97706"></i>'
                '<i style="background:#059669"></i><i style="background:#7C3AED"></i></div>', unsafe_allow_html=True)
    st.markdown("## QueryMend")
    st.caption("Sahyadri Stores, April to September 2026. qwen2.5:3b, fully offline.")

    st.markdown("**Demo switches**")
    monitor_on = st.toggle("Monitor on", value=True, help="Off shows what happens without the Monitor.")
    fault_label = st.selectbox("Inject a failure into the next question", list(FAULTS))
    user_id = st.selectbox("Whose glossary", ["owner_pune", "owner_mumbai"])
    terms = load_terms(shared_memory()[1], user_id)
    if terms:
        st.caption("Saved words: " + "; ".join(f"{k} = {v}" for k, v in terms.items()))
    st.caption(f"Retry budget: {RETRY_BUDGET} per question.")

    st.markdown("**Try one**")
    for example in EXAMPLES:
        if st.button(example, use_container_width=True):
            st.session_state.queued = example

    if st.button("New conversation", type="secondary"):
        st.session_state.thread = f"web-{uuid.uuid4().hex[:8]}"
        st.session_state.turns = []
        st.rerun()

team = team_for(monitor_on, user_id)

# ------------------------------------------------------------------ main
st.markdown("# Ask your shop database")
st.caption("If the answer is wrong, the team notices, fixes it, or tells you it can't. "
           "Open the panel under each answer to see who did what.")

for i, turn in enumerate(st.session_state.turns):
    with st.chat_message("user"):
        st.write(turn["question"])
        if turn["fault"]:
            st.caption(f"Injected failure: {turn['fault']}")
    with st.chat_message("assistant"):
        if "pending" in turn:
            st.warning(turn["pending"])
            left, right, _ = st.columns([2, 2, 5])
            if left.button("Approve", key=f"approve{i}", type="primary"):
                with st.spinner("Continuing..."):
                    resume("approve", team)
                st.rerun()
            if right.button("Reject", key=f"reject{i}"):
                with st.spinner("Masking and continuing..."):
                    resume("reject", team)
                st.rerun()
            continue
        st.write(turn["answer"])
        st.markdown(f'<div class="stats">{plural(turn["model_calls"], "Executor step")} · '
                    f'{plural(turn["tool_calls"], "tool call")} · '
                    f'{turn["retries"]} of {RETRY_BUDGET} retries used</div>', unsafe_allow_html=True)
        with st.expander("Who did what"):
            for event in turn["events"]:
                st.markdown(step_html(event), unsafe_allow_html=True)
            for n, query in enumerate(turn["queries"], 1):
                st.caption(f"Query {n}")
                st.code(query, language="sql", wrap_lines=True)

waiting = bool(st.session_state.turns) and "pending" in st.session_state.turns[-1]
typed = st.chat_input("Ask about customers, products, orders or revenue", disabled=waiting)
question = typed or st.session_state.pop("queued", None)
if question and not waiting:
    with st.spinner("The team is working..."):
        ask(question, team, FAULTS[fault_label])
    st.rerun()
