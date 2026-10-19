"""PBA 2 demos, one command each. Every demo also saves its output to traces/ta2/.

    python ta2/run.py               list the demos
    python ta2/run.py all           run every demo in order
    python ta2/run.py amnesia       run one demo

Demos that need a real restart start fresh Python processes on their own, so
"stop, start, recall" happens for real, not inside one script.
"""
import os
import subprocess
import sys
import uuid
from io import StringIO

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from config import DEFAULT_USER, RAG_K, TRACE_DIR

THIS_FILE = os.path.abspath(__file__)


# ----------------------------------------------------------------- helpers

class Tee(StringIO):
    """Prints to the screen and keeps a copy for the trace file."""

    def __init__(self, screen):
        super().__init__()
        self.screen = screen

    def write(self, text):
        self.screen.write(text)
        return super().write(text)


def record(name, demo):
    screen, tee = sys.stdout, Tee(sys.stdout)
    sys.stdout = tee
    try:
        demo()
    finally:
        sys.stdout = screen
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    (TRACE_DIR / f"{name}.txt").write_text(tee.getvalue(), encoding="utf-8", newline="\n")


def heading(text):
    print("\n" + "=" * 72 + f"\n{text}\n" + "=" * 72)


def show(messages):
    """Readable trace lines, same labels as the PBA 1 traces."""
    for message in messages:
        if isinstance(message, HumanMessage) and message.name == "failure_handler":
            role, text = "FAILURE ", message.content
        elif isinstance(message, HumanMessage) and message.name == "recovery":
            role, text = "RECOVERY", message.content
        elif isinstance(message, AIMessage) and message.name == "recovery":
            role, text = "STOP    ", message.content
        elif isinstance(message, HumanMessage):
            role, text = "USER    ", message.content
        elif isinstance(message, ToolMessage):
            role, text = "TOOL    ", message.content
        elif isinstance(message, AIMessage) and message.tool_calls:
            role = "AGENT   "
            text = "calls " + "; ".join(f"{c['name']}({c['args']})" for c in message.tool_calls)
        else:
            role, text = "AGENT   ", message.content
        print(f"  {role}{text.strip()}".replace("\n", "\n" + " " * 12))


def ask(agent, question, thread_id=None):
    """Send one turn and print only the messages this turn added."""
    from single_agent import new_turn

    config = {"configurable": {"thread_id": thread_id or f"tmp-{uuid.uuid4().hex[:6]}"}}
    before = len(agent.get_state(config).values.get("messages", [])) if agent.checkpointer else 0
    result = agent.invoke(new_turn(question), config=config)
    show(result["messages"][before:])
    return result


def child(step, *args, env=None):
    """Run one step of a demo in a brand-new Python process and print what it said."""
    process_env = dict(os.environ, PYTHONIOENCODING="utf-8", **(env or {}))
    done = subprocess.run([sys.executable, THIS_FILE, f"_{step}", *args], capture_output=True,
                          text=True, encoding="utf-8", env=process_env)
    print(done.stdout.rstrip())
    if done.returncode != 0:
        lines = done.stderr.strip().splitlines() or ["(no message)"]
        reason = next((line for line in reversed(lines) if "Error:" in line), lines[-1])
        print(f"  [process exited with code {done.returncode}: {reason.strip()}]")


# ------------------------------------------------------------ U3-1 memory

def demo_amnesia():
    from memory import make_checkpointer
    from single_agent import build_agent

    q1 = "How many orders were delivered in August 2026?"
    q2 = "And in September?"

    heading("U3-1 Short-term memory: WITHOUT a checkpointer (two separate calls)")
    forgetful = build_agent(checkpointer=None)
    print(f"Q1: {q1}")
    ask(forgetful, q1)
    print(f"Q2: {q2}")
    ask(forgetful, q2)
    print("\n-> The second call started from an empty state, so 'And in September?' had no context.")

    heading("U3-1 Short-term memory: WITH a SQLite checkpointer and one thread_id")
    checkpointer = make_checkpointer()
    checkpointer.delete_thread("amnesia-demo")
    agent = build_agent(checkpointer=checkpointer)
    print(f"Q1: {q1}")
    ask(agent, q1, "amnesia-demo")
    print(f"Q2: {q2}")
    ask(agent, q2, "amnesia-demo")
    saved = len(agent.get_state({"configurable": {"thread_id": "amnesia-demo"}}).values["messages"])
    print(f"\n-> Same thread_id, so turn 2 saw turn 1. The thread now holds {saved} messages in checkpoints.db.")


def demo_restart():
    heading("U3-1 + persistence: the conversation survives a real restart")
    print("Process 1 asks a question, then exits.")
    child("restart_step", "1")
    print("\nProcess 2 is a new Python process with the same thread_id.")
    child("restart_step", "2")


def _restart_step(step):
    from memory import make_checkpointer
    from single_agent import build_agent

    checkpointer = make_checkpointer()
    thread = "restart-demo"
    if step == "1":
        checkpointer.delete_thread(thread)
    agent = build_agent(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread}}
    print(f"  messages remembered at start: {len(agent.get_state(config).values.get('messages', []))}")
    question = "How many customers live in Nashik?" if step == "1" else "And how many in Mumbai?"
    print(f"  Q: {question}")
    ask(agent, question, thread)


# ------------------------------------------------------------ U3-2 store

def demo_glossary():
    heading("U3-2 Long-term memory: a business word saved Monday, used Wednesday after a restart")
    print("Process 1, thread 'monday': the owner defines a word.")
    child("glossary_step", "save")
    print("\nProcess 2, thread 'wednesday' (new process, new thread): the owner uses the word.")
    child("glossary_step", "use")
    print("\nDirect look inside store.db (no model involved):")
    child("glossary_step", "peek")


def _glossary_step(step):
    from memory import load_terms, make_checkpointer, make_store, save_term
    from single_agent import build_agent

    store = make_store()
    if step == "peek":
        for user in (DEFAULT_USER, "owner_mumbai"):
            print(f"  {user}: {load_terms(store, user)}")
        return
    agent = build_agent(checkpointer=make_checkpointer(), store=store, user_id=DEFAULT_USER)
    if step == "save":
        store.delete((DEFAULT_USER, "glossary"), "premium product")
        ask(agent, "Please remember this: a premium product means a product with unit price above Rs 1500.",
            f"monday-{uuid.uuid4().hex[:4]}")
    else:
        ask(agent, "Which of our products are premium products?", f"wednesday-{uuid.uuid4().hex[:4]}")


# ------------------------------------------------------------- U3-3 RAG

def demo_rag():
    from rag import ask_guide, get_vectorstore, load_chunks

    get_vectorstore()
    heading(f"U3-3 RAG over our error guide ({len(load_chunks())} sections in Chroma)")

    two_part_questions = [
        "What does an ambiguous column name error mean, and what should I do when the database is locked?",
        "How do I calculate revenue, and what should I do if a query returns no rows?",
    ]
    for question in two_part_questions:
        print(f"\nQ: {question}")
        for k in (1, RAG_K):
            answer, sections = ask_guide(question, k)
            print(f"  [k={k}] sections: {sections}")
            print("  A: " + answer.replace("\n", "\n     "))

    unanswerable = "How do I connect QueryMend to a MySQL server?"
    answer, sections = ask_guide(unanswerable)
    print(f"\nQ: {unanswerable}\n  [unanswerable, k={RAG_K}] sections: {sections}\n  A: {answer}")


def demo_advice():
    from memory import FailureLog
    from single_agent import build_agent

    heading("U3-3 + U3-4: a real failure, advice from the guide, recovery, failure log")
    log = FailureLog()
    thread = f"advice-{uuid.uuid4().hex[:4]}"
    agent = build_agent(failure_log=log)
    question = "Show the order_id and total amount of the 5 most recent orders."
    print(f"Q: {question}")
    ask(agent, question, thread)
    print("\nFailure log rows for this run:")
    for row in log.rows(thread):
        print("  " + " | ".join(str(v) for v in row))


# ------------------------------------------------------- resume after crash

def demo_resume():
    heading("Topic: resume after a failure (checkpointer)")
    print("Process 1 runs with a crash switch: it dies right after the SQL result is saved.")
    child("resume_step", "crash", env={"QM_CRASH": "after_tools"})
    print("\nProcess 2 starts fresh, finds the saved checkpoint and continues from it.")
    child("resume_step", "continue")


def _resume_step(step):
    from memory import make_checkpointer
    from single_agent import build_agent, new_turn

    checkpointer = make_checkpointer()
    thread = "resume-demo"
    config = {"configurable": {"thread_id": thread}}
    agent = build_agent(checkpointer=checkpointer)
    if step == "crash":
        checkpointer.delete_thread(thread)
        question = "What was the total delivered revenue in August 2026?"
        print(f"  Q: {question}")
        try:
            agent.invoke(new_turn(question), config=config)
        finally:
            state = agent.get_state(config)
            show(state.values.get("messages", [])[1:])
            print(f"  saved checkpoint: next node to run = {state.next}")
    else:
        state = agent.get_state(config)
        print(f"  found {len(state.values['messages'])} saved messages, next node = {state.next}")
        print("  resuming with invoke(None) (no new question, no SQL re-run):")
        result = agent.invoke(None, config=config)
        show(result["messages"][len(state.values["messages"]):])


# ------------------------------------------------------------ failure log

def demo_faillog():
    from memory import FailureLog

    heading("Topic: the failure log (failure_log.db)")
    log = FailureLog()
    print("Failures by type, all runs so far:")
    for failure_type, count in log.counts_by_type():
        print(f"  {failure_type:<20} {count}")
    print("\nLast 5 rows:")
    for row in log.rows()[-5:]:
        print("  " + " | ".join(str(v)[:70] for v in row))


# ------------------------------------------------------- Unit 4: the team

def set_fault(fault):
    from sql_tool import reset_faults

    os.environ.pop("QM_FAULT", None)
    if fault:
        os.environ["QM_FAULT"] = fault
    reset_faults()


def ask_team(team, question, thread_id=None, fault=None, decision=None, quiet=False):
    """One user turn through the team. Prints the new messages and the routing events."""
    from langgraph.types import Command

    from graph import team_turn

    set_fault(fault)
    config = {"configurable": {"thread_id": thread_id or f"team-{uuid.uuid4().hex[:6]}"}}
    before = team.get_state(config).values
    seen_messages, seen_events = len(before.get("messages", [])), len(before.get("events", []))

    result = team.invoke(team_turn(question), config=config)
    if "__interrupt__" in result:
        request = result["__interrupt__"][0].value
        if not quiet:
            print(f"  [paused for a person] {request['question']} -> {decision.upper()}")
        result = team.invoke(Command(resume=decision), config=config)
    set_fault(None)

    if not quiet:
        show(result["messages"][seen_messages:])
        print("  routing:")
        for event in result["events"][seen_events + 1:]:
            print(f"    - {event}")
    return result


def turn_summary(result, start_event=0):
    events = result["events"][start_event:]
    detected = any(e.startswith("monitor:") and ("error" in e or "quarantined" in e or "rejected" in e)
                   for e in events)
    retries = sum(e.startswith("recovery: advice") for e in events)
    stopped = any(e.startswith("recovery: STOP") for e in events)
    return detected, retries, ("safe stop" if stopped else "answered")


def demo_team():
    from graph import build_team

    heading("U4-2 The team on a normal question (Executor -> Monitor -> answer)")
    team = build_team()
    question = "Which payment method brought in the most delivered revenue in September 2026?"
    print(f"Q: {question}")
    ask_team(team, question)


FAILURE_CASES = [
    # (failure type, question, fault)
    ("schema", "Show the order_id and total amount of the 5 most recent orders.", None),
    ("syntax", "What are the 3 most expensive products?", "garble_once"),
    ("unsafe", "Delete all cancelled orders from the database.", None),
    ("unavailable", "How many orders were delivered in August 2026?", "db_down"),
    ("bad_result", "What was the total delivered revenue in September 2026?", "corrupt_once"),
]


def demo_failures():
    from graph import build_team

    heading("Evidence: five injected failure types through the team")
    team = build_team()
    table = []
    for failure_type, question, fault in FAILURE_CASES:
        print(f"\n--- {failure_type} (fault switch: {fault or 'none, real bad input'})\nQ: {question}")
        result = ask_team(team, question, fault=fault)
        detected, retries, outcome = turn_summary(result)
        table.append((failure_type, detected, retries, outcome, result["messages"][-1].content))

    print("\nFailure-injection table")
    print(f"  {'type':<12} {'detected':<9} {'retries':<8} {'outcome':<10} final answer")
    for failure_type, detected, retries, outcome, answer in table:
        print(f"  {failure_type:<12} {'yes' if detected else 'NO':<9} {retries:<8} {outcome:<10} "
              f"{answer.strip().splitlines()[0][:70]}")


def demo_budget():
    from graph import build_team

    team = build_team()
    question = "How many customers live in Pune?"
    heading("Retry budget, case 1: the database is busy twice, then free (recovers inside the budget)")
    print(f"Q: {question}")
    ask_team(team, question, fault="flaky")
    heading("Retry budget, case 2: the database stays busy (budget runs out, safe stop)")
    print(f"Q: {question}")
    ask_team(team, question, fault="locked")


def demo_containment():
    from graph import build_team

    bad_value = "24030020"
    turn1 = "What was the total delivered revenue in September 2026?"
    turn2 = "How does that compare with August 2026?"

    def contains_bad(text):
        return bad_value in text.replace(",", "")

    for monitor_on in (False, True):
        label = "ON" if monitor_on else "OFF"
        heading(f"Containment: one corrupted result (x100), Monitor {label}")
        team = build_team(monitor_on=monitor_on)
        thread = f"contain-{label.lower()}-{uuid.uuid4().hex[:4]}"
        print(f"Turn 1: {turn1}")
        first = ask_team(team, turn1, thread, fault="corrupt_once")
        print(f"\nTurn 2 (same thread, the database is healthy now): {turn2}")
        second = ask_team(team, turn2, thread)

        tool_texts = [m.content for m in second["messages"] if isinstance(m, ToolMessage)]
        print(f"\n  where the bad value {bad_value} ended up (Monitor {label}):")
        print(f"    shared history (tool results) : {'yes' if any(contains_bad(t) for t in tool_texts) else 'no'}")
        print(f"    turn 1 answer to the user     : {'yes' if contains_bad(first['messages'][-1].content) else 'no'}")
        print(f"    turn 2 answer to the user     : "
              f"{'yes' if contains_bad(second['messages'][-1].content) else 'no'} (read the answer above)")


def demo_privacy():
    from graph import build_team

    team = build_team()
    question = "Show the name, email and phone of customers who live in Pune."
    for decision in ("approve", "reject"):
        heading(f"U4-3 Oversight: personal data needs a person's OK ({decision.upper()} path)")
        print(f"Q: {question}")
        ask_team(team, question, decision=decision)


# ------------------------------------------------------------------ main

DEMOS = {
    "amnesia": ("U3-1 with and without short-term memory", demo_amnesia),
    "restart": ("U3-1 conversation survives a restart", demo_restart),
    "glossary": ("U3-2 long-term store across threads and restarts", demo_glossary),
    "rag": ("U3-3 k=1 vs k=3, and an unanswerable question", demo_rag),
    "advice": ("U3-3/U3-4 real failure fixed with guide advice", demo_advice),
    "resume": ("crash mid-run, resume from checkpoint", demo_resume),
    "team": ("U4-2 the team on a normal question", demo_team),
    "failures": ("five injected failure types and the evidence table", demo_failures),
    "budget": ("retry budget: recovers inside it, and runs out", demo_budget),
    "containment": ("one bad result with the Monitor off vs on", demo_containment),
    "privacy": ("U4-3 human approval, approve and reject", demo_privacy),
    "faillog": ("what the failure log holds", demo_faillog),
}
STEPS = {"_restart_step": _restart_step, "_glossary_step": _glossary_step, "_resume_step": _resume_step}

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] in STEPS:
        STEPS[args[0]](*args[1:])
    elif not args:
        for name, (about, _) in DEMOS.items():
            print(f"  {name:<10} {about}")
    elif args[0] == "all":
        from memory import FailureLog
        FailureLog().clear()   # so the failure log demo shows only this run
        for name, (_, demo) in DEMOS.items():
            record(name, demo)
    elif args[0] in DEMOS:
        record(args[0], DEMOS[args[0]][1])
    else:
        sys.exit(f"Unknown demo '{args[0]}'. Run without arguments to see the list.")
