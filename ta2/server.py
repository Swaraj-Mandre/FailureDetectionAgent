"""The QueryMend web app: a FastAPI backend around the agent team in graph.py.

    python ta2/server.py            then open http://127.0.0.1:8000

The agents are exactly the ones in graph.py. This file only:
- streams every step of a run to the browser as Server-Sent Events,
- pauses and resumes the run for the privacy approval,
- serves the failure log, the evaluation results and the glossary,
- serves the built frontend (frontend/dist) so one command runs everything.

Build the frontend once first: cd frontend && npm install && npm run build
"""
import json
import os
import sqlite3
import threading
import time
import urllib.request
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command
from pydantic import BaseModel

import agents
from config import CHAT_MODEL, EMBED_MODEL, REPO_DIR, RETRY_BUDGET, SHOP_DB, STATE_DIR
from graph import build_team, team_turn
from memory import FailureLog, load_terms, make_checkpointer, make_store, open_db, save_term
from sql_tool import reset_faults

FRONTEND_DIST = REPO_DIR / "frontend" / "dist"
EVAL_RESULTS = REPO_DIR / "traces" / "eval" / "results.json"
APP_DB = STATE_DIR / "app.db"   # conversation list and finished turns, for the history sidebar
OLLAMA_URL = os.environ.get("OLLAMA_HOST_URL", "http://127.0.0.1:11434")

USERS = {
    "owner_pune": "Pune store",
    "owner_mumbai": "Mumbai store",
}

FAULTS = {
    "corrupt_once": "Prices 100x too big, once",
    "corrupt": "Prices 100x too big, every time",
    "garble_once": "A broken query, once",
    "flaky": "Database busy twice, then fine",
    "locked": "Database busy, never clears",
    "db_down": "Database unreachable",
}

EXAMPLES = [
    "Which 3 products earned the most revenue in September 2026?",
    "What was the total delivered revenue in September 2026?",
    "Show the 5 most recent orders and their totals.",
    "Show the name, email and phone of customers who live in Pune.",
    "Delete all cancelled orders from the database.",
]

# ------------------------------------------------------------------ shared state

checkpointer = make_checkpointer()
store = make_store()
failure_log = FailureLog()
teams = {}
run_lock = threading.Lock()   # QM_FAULT is process-wide, so one run at a time

app_db = open_db(APP_DB)
app_db.executescript("""
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, user_id TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS turns (
        thread_id TEXT NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY (thread_id, position));
""")

pending = {}   # thread_id -> the turn being built while a run is paused for approval


def team_for(monitor_on, user_id):
    key = (monitor_on, user_id)
    if key not in teams:
        teams[key] = build_team(checkpointer=checkpointer, store=store, user_id=user_id,
                                monitor_on=monitor_on, failure_log=failure_log)
    return teams[key]


def set_fault(fault):
    os.environ.pop("QM_FAULT", None)
    if fault:
        os.environ["QM_FAULT"] = fault
    reset_faults()


def sse(kind, **data):
    return f"data: {json.dumps({'type': kind, **data}, default=str)}\n\n"


# ------------------------------------------------------------------ one run, as a stream of events

def role_of(event):
    role, _, text = event.partition(": ")
    return ("person" if role == "privacy gate" else role), text


def events_for(node, update, turn):
    """Turn one node's state update into the events the page draws."""
    out = []
    for message in update.get("messages", []) or []:
        if isinstance(message, AIMessage) and message.tool_calls:
            for call in message.tool_calls:
                if call["name"] == "run_sql":
                    turn["queries"].append(call["args"].get("query", ""))
                    out.append(sse("sql", query=call["args"].get("query", ""), index=len(turn["queries"])))
                elif call["name"] == "remember_term":
                    out.append(sse("memory", term=call["args"].get("term"), meaning=call["args"].get("meaning")))
        if isinstance(message, ToolMessage) and message.name == "run_sql" and node == "tools":
            parsed = agents.parse_rows(message.content)
            if parsed:
                columns, rows = parsed
                # the preview never shows personal columns; only the approved answer may
                hidden = [i for i, c in enumerate(columns) if c in agents.sensitive_columns(columns)]
                rows = [["(hidden)" if i in hidden else v for i, v in enumerate(r)] for r in rows]
                preview = {"columns": columns, "rows": rows[:20], "total": len(rows), "hidden": len(hidden)}
                turn["results"].append(preview)
                out.append(sse("rows", **preview))
            else:
                turn["results"].append({"error": message.content[:400]})
                out.append(sse("tool_text", text=message.content[:400]))
    for event in update.get("events", []) or []:
        if event.startswith("user:"):
            continue
        role, text = role_of(event)
        step = {"role": role, "text": text, "node": node}
        turn["steps"].append(step)
        out.append(sse("step", **step))
    return out


def run(team, payload, config, turn):
    for chunk in team.stream(payload, config=config, stream_mode="updates"):
        for node, update in chunk.items():
            if node == "__interrupt__":
                value = update[0].value
                turn["approval"] = value["question"]
                yield sse("approval", question=value["question"])
                return
            if update:
                yield from events_for(node, update, turn)


def finish(team, config, turn):
    """The answer and the numbers for the finished turn."""
    messages = team.get_state(config).values["messages"][turn["seen"]:]
    turn["answer"] = messages[-1].content
    turn["stats"] = {
        "executor_steps": sum(isinstance(m, AIMessage) and m.name != "recovery" for m in messages),
        "tool_calls": sum(isinstance(m, ToolMessage) for m in messages),
        "retries": sum(s["role"] == "recovery" and s["text"].startswith("advice") for s in turn["steps"]),
        "seconds": round(time.time() - turn["started"], 1),
    }
    turn["outcome"] = outcome_of(turn["steps"])
    turn["table"] = answer_table(messages, turn["outcome"])
    turn.pop("approval", None)
    position = app_db.execute("SELECT COUNT(*) FROM turns WHERE thread_id = ?", (turn["thread_id"],)).fetchone()[0]
    app_db.execute("INSERT INTO turns VALUES (?, ?, ?)", (turn["thread_id"], position, json.dumps(turn)))
    return sse("answer", answer=turn["answer"], stats=turn["stats"], outcome=turn["outcome"], table=turn["table"])


def answer_table(messages, outcome):
    """The rows behind the answer, shown under it. After a person approved, personal columns are shown;
    after a rejection the stored result is already masked. A quarantined result has no rows, so no table."""
    if outcome == "stopped":
        return None
    result = next((m for m in reversed(messages) if isinstance(m, ToolMessage) and m.name == "run_sql"), None)
    parsed = agents.parse_rows(result.content) if result else None
    if not parsed or (len(parsed[0]) == 1 and len(parsed[1]) == 1):
        return None   # a single number is already in the answer
    columns, rows = parsed
    if outcome != "approved":
        hidden = [i for i, c in enumerate(columns) if c in agents.sensitive_columns(columns)]
        rows = [["(hidden)" if i in hidden else v for i, v in enumerate(r)] for r in rows]
    return {"columns": columns, "rows": rows[:20], "total": len(rows), "hidden": 0}


def outcome_of(steps):
    """One word for the turn, for the badge on the answer."""
    if any(s["role"] == "stop" for s in steps):
        return "stopped"
    if any(s["role"] == "recovery" for s in steps):
        return "recovered"
    if any(s["role"] == "person" for s in steps):
        return "approved" if any("APPROVED" in s["text"] for s in steps) else "masked"
    if any("OFF" in s["text"] for s in steps if s["role"] == "monitor"):
        return "unchecked"
    return "checked"


def guarded(stream):
    """Runs one stream under the lock and reports errors to the page instead of dropping the connection."""
    if not run_lock.acquire(blocking=False):
        yield sse("error", message="Another question is still running. Wait for it to finish.")
        return
    try:
        yield from stream
    except Exception as exc:   # the page shows it; the server keeps running
        yield sse("error", message=f"{type(exc).__name__}: {exc}")
    finally:
        set_fault(None)
        run_lock.release()


# ------------------------------------------------------------------ API

app = FastAPI(title="QueryMend")


class AskBody(BaseModel):
    thread_id: str | None = None
    question: str
    user_id: str = "owner_pune"
    monitor_on: bool = True
    fault: str | None = None


class ResumeBody(BaseModel):
    thread_id: str
    decision: str   # "approve" or "reject"


class TermBody(BaseModel):
    term: str
    meaning: str


@app.get("/api/status")
def status():
    models = []
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2) as reply:
            models = [m["name"] for m in json.load(reply)["models"]]
        ollama = True
    except OSError:
        ollama = False
    return {
        "ollama": ollama,
        "chat_model": CHAT_MODEL, "chat_model_ready": any(m.startswith(CHAT_MODEL) for m in models),
        "embed_model": EMBED_MODEL, "embed_model_ready": any(m.startswith(EMBED_MODEL) for m in models),
        "database": SHOP_DB.exists(),
    }


@app.get("/api/setup")
def setup():
    return {
        "users": [{"id": k, "name": v} for k, v in USERS.items()],
        "faults": [{"id": k, "name": v} for k, v in FAULTS.items()],
        "examples": EXAMPLES,
        "retry_budget": RETRY_BUDGET,
    }


@app.get("/api/shop")
def shop():
    """A few facts about the shop database for the welcome screen."""
    with sqlite3.connect(f"file:{SHOP_DB}?mode=ro", uri=True) as conn:
        count = lambda table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        first, last = conn.execute("SELECT MIN(order_date), MAX(order_date) FROM orders").fetchone()
        return {"name": "Sahyadri Stores", "customers": count("customers"), "products": count("products"),
                "orders": count("orders"), "first_order": first, "last_order": last}


@app.post("/api/ask")
def ask(body: AskBody):
    if body.user_id not in USERS:
        raise HTTPException(400, "Unknown store.")
    if body.fault and body.fault not in FAULTS:
        raise HTTPException(400, "Unknown failure type.")
    thread_id = body.thread_id or f"web-{uuid.uuid4().hex[:10]}"
    if not app_db.execute("SELECT 1 FROM conversations WHERE id = ?", (thread_id,)).fetchone():
        app_db.execute("INSERT INTO conversations VALUES (?, ?, ?, ?)",
                       (thread_id, body.question[:80], body.user_id, datetime.now().isoformat(timespec="seconds")))
    team = team_for(body.monitor_on, body.user_id)
    config = {"configurable": {"thread_id": thread_id}}

    def stream():
        state = team.get_state(config).values
        turn = {"thread_id": thread_id, "question": body.question, "fault": body.fault,
                "monitor_on": body.monitor_on, "user_id": body.user_id, "started": time.time(),
                "seen": len(state.get("messages", [])), "steps": [], "queries": [], "results": []}
        yield sse("start", thread_id=thread_id)
        set_fault(body.fault)
        yield from run(team, team_turn(body.question), config, turn)
        if "approval" in turn:
            pending[thread_id] = (team, turn)
            return
        yield finish(team, config, turn)

    return StreamingResponse(guarded(stream()), media_type="text/event-stream")


@app.post("/api/resume")
def resume(body: ResumeBody):
    if body.thread_id not in pending or body.decision not in ("approve", "reject"):
        raise HTTPException(400, "Nothing is waiting for approval in this conversation.")
    team, turn = pending.pop(body.thread_id)
    config = {"configurable": {"thread_id": body.thread_id}}

    def stream():
        yield from run(team, Command(resume=body.decision), config, turn)
        yield finish(team, config, turn)

    return StreamingResponse(guarded(stream()), media_type="text/event-stream")


@app.get("/api/conversations")
def conversations():
    rows = app_db.execute("""
        SELECT c.id, c.title, c.user_id, c.created_at, COUNT(t.position)
        FROM conversations c LEFT JOIN turns t ON t.thread_id = c.id
        GROUP BY c.id ORDER BY c.created_at DESC LIMIT 50""").fetchall()
    return [{"id": r[0], "title": r[1], "user_id": r[2], "created_at": r[3], "turns": r[4]} for r in rows if r[4]]


@app.get("/api/conversations/{thread_id}")
def conversation(thread_id: str):
    rows = app_db.execute("SELECT payload FROM turns WHERE thread_id = ? ORDER BY position", (thread_id,)).fetchall()
    return [json.loads(r[0]) for r in rows]


@app.get("/api/failures")
def failures():
    columns = FailureLog.COLUMNS
    rows = [dict(zip(columns, r)) for r in failure_log.rows()]
    rows.reverse()
    return {"counts": [{"type": t, "count": n} for t, n in failure_log.counts_by_type()], "rows": rows[:300]}


@app.get("/api/evaluation")
def evaluation():
    """The saved results of ta2/evaluate.py, summed up per case."""
    if not EVAL_RESULTS.exists():
        raise HTTPException(404, "Run python ta2/evaluate.py first.")
    data = json.loads(EVAL_RESULTS.read_text(encoding="utf-8"))

    def summary(rows, key):
        groups = defaultdict(list)
        for row in rows:
            groups[key(row)].append(row)
        return groups

    ev = []
    for (system, case), rows in summary(data["ev"], lambda r: (r["system"], r["case"])).items():
        ev.append({"system": system, "case": case, "runs": len(rows),
                   "outcomes": {o: sum(r["outcome"] == o for r in rows) for o in {r["outcome"] for r in rows}},
                   "model_calls": round(sum(r["model_calls"] for r in rows) / len(rows), 1),
                   "seconds": round(sum(r["seconds"] for r in rows) / len(rows), 1)})
    injections = []
    for kind, rows in summary(data["injections"], lambda r: r["kind"]).items():
        injections.append({"kind": kind, "runs": len(rows), "detected": sum(r["detected"] for r in rows),
                           "outcomes": {o: sum(r["outcome"] == o for r in rows) for o in {r["outcome"] for r in rows}},
                           "retries": round(sum(r["retries"] for r in rows) / len(rows), 1)})
    containment = []
    for monitor_on, rows in summary(data["containment"], lambda r: r["monitor_on"]).items():
        containment.append({"monitor_on": monitor_on, "runs": len(rows),
                            "in_history": sum(r["in_history"] for r in rows),
                            "turn2_wrong": sum(r["turn2_wrong"] for r in rows),
                            "turn1_correct": sum(r["turn1_correct"] for r in rows),
                            "model_calls": round(sum(r["model_calls"] for r in rows) / len(rows), 1),
                            "seconds": round(sum(r["seconds"] for r in rows) / len(rows), 1)})
    return {"repeats": data["repeats"], "ev": ev, "injections": injections, "containment": containment}


@app.get("/api/glossary/{user_id}")
def glossary(user_id: str):
    if user_id not in USERS:
        raise HTTPException(404, "Unknown store.")
    return [{"term": t, "meaning": m} for t, m in sorted(load_terms(store, user_id).items())]


@app.post("/api/glossary/{user_id}")
def add_term(user_id: str, body: TermBody):
    if user_id not in USERS or not body.term.strip() or not body.meaning.strip():
        raise HTTPException(400, "A store, a word and its meaning are all needed.")
    save_term(store, user_id, body.term, body.meaning)
    return glossary(user_id)


# ------------------------------------------------------------------ the built frontend

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def page(path: str):
        file = FRONTEND_DIST / path
        return FileResponse(file if path and file.is_file() else FRONTEND_DIST / "index.html")


if __name__ == "__main__":
    import uvicorn

    print("QueryMend is running at http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
