"""The QueryMend web app: a FastAPI backend around the agent team in graph.py.

    python ta2/server.py            then open http://127.0.0.1:8000

The agents are exactly the ones in graph.py. This file only:
- streams every step of a run to the browser as Server-Sent Events,
- pauses and resumes the run for the privacy approval,
- takes a shopkeeper's own files, shows what was found, and adds them as a new store,
- serves the failure log, the evaluation results and the glossary,
- serves the built frontend (frontend/dist) so one command runs everything,
- keeps visitors apart when it runs online (QM_PUBLIC=1, see visitors.py).

Build the frontend once first: cd frontend && npm install && npm run build
"""
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
import urllib.request
import uuid
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime
from pathlib import Path

import anyio
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from pydantic import BaseModel, Field

import agents
import importer
import shops
import visitors
from config import CHAT_MODEL, EMBED_MODEL, REPO_DIR, RETRY_BUDGET, SHOP_DB, STATE_DIR
from graph import build_team, team_turn
from meaning import month_name
from memory import FailureLog, forget_terms, load_terms, make_checkpointer, make_store, save_term, SharedDb
from sql_tool import reset_faults
from visitors import PUBLIC, scoped

MAX_STEPS = 60   # the model can keep calling the tool without ever answering; stop it instead of blocking everyone
FRONTEND_DIST = REPO_DIR / "frontend" / "dist"
EVAL_RESULTS = REPO_DIR / "traces" / "eval" / "results.json"
APP_DB = STATE_DIR / "app.db"   # conversation list and finished turns, for the history sidebar
OLLAMA_URL = os.environ.get("OLLAMA_HOST_URL", "http://127.0.0.1:11434")

# large uploads are kept on disk while they arrive; keep them in the project folder, not the system temp folder
(STATE_DIR / "tmp").mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(STATE_DIR / "tmp")

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
teams = OrderedDict()         # the most recently used ones, see team_for
TEAMS_KEPT = 40
teams_lock = threading.Lock()
limits = visitors.Limits()
slots = visitors.Slots()
upload_lock = threading.Lock()   # one file is read at a time, because reading needs memory
log = logging.getLogger("querymend")

app_db = SharedDb(APP_DB)
app_db.conn.executescript("""
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, user_id TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS turns (
        thread_id TEXT NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY (thread_id, position));
    CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, last_seen REAL NOT NULL);
""")
if "session" not in [r[1] for r in app_db.execute("PRAGMA table_info(conversations)")]:
    app_db.execute("ALTER TABLE conversations ADD COLUMN session TEXT NOT NULL DEFAULT ''")

pending = {}   # (session, thread_id) -> the turn being built while a run is paused for approval


def team_for(monitor_on, user_id, session):
    key = (monitor_on, scoped(session, user_id))
    shop = store_or_404(user_id, session)
    with teams_lock:
        if key not in teams:
            teams[key] = build_team(checkpointer=checkpointer, store=store, user_id=scoped(session, user_id), monitor_on=monitor_on,
                                    failure_log=failure_log, shop=shop)
        teams.move_to_end(key)
        while len(teams) > TEAMS_KEPT:
            teams.popitem(last=False)
        return teams[key]


def store_or_404(user_id, session):
    shop = shops.shop_for(user_id, session)
    if shop is None:
        raise HTTPException(404, "Unknown store. It may have been removed.")
    return shop


# ------------------------------------------------------------------ visitors

def who(request):
    """The session of a request. Empty on your own computer, where there is only one person."""
    return request.state.session


def seen(session):
    if session:
        app_db.execute("INSERT INTO sessions VALUES (?, ?) ON CONFLICT (id) DO UPDATE SET last_seen = excluded.last_seen", (session, time.time()))


def allow(kind, request):
    """Counts one question, upload or saved word against the limits, and keeps the session alive."""
    if not PUBLIC:
        return
    message = limits.allow(kind, who(request), request.state.address)
    if message:
        raise HTTPException(429, message)
    seen(who(request))


def threads_of(session, user_id=None):
    rows = app_db.execute("SELECT id, user_id FROM conversations WHERE session = ?", (session,))
    return [r[0] for r in rows if user_id in (None, r[1])]


def forget_threads(session, user_id=None):
    for thread_id in threads_of(session, user_id):
        pending.pop((session, thread_id), None)
        checkpointer.delete_thread(thread_id)
        failure_log.forget(thread_id)
        app_db.execute("DELETE FROM turns WHERE thread_id = ?", (thread_id,))
        app_db.execute("DELETE FROM conversations WHERE id = ?", (thread_id,))


def forget_store(session, user_id):
    """An uploaded shop, what was asked about it and the words saved for it."""
    forget_threads(session, user_id)
    forget_terms(store, scoped(session, user_id))
    with teams_lock:
        for key in [k for k in teams if k[1] == scoped(session, user_id)]:
            del teams[key]
    shops.remove(user_id, session)


def forget_session(session):
    """Everything one visitor left behind, also for the sample stores."""
    for s in shops.stores(session):
        forget_store(session, s["id"])
    shops.discard_all(session)
    forget_threads(session)
    app_db.execute("DELETE FROM sessions WHERE id = ?", (session,))


def forget_old_sessions():
    """Runs in the background online: deletes the data of visitors who have not come back."""
    while True:
        time.sleep(visitors.SWEEP_SECONDS)
        try:
            shops.clear_old_waiting()
            # a visitor who deleted everything while another request was still running is picked up here
            left = {r[0] for r in app_db.execute("SELECT DISTINCT session FROM conversations WHERE session != ''")}
            for session in left | shops.sessions_with_stores():
                app_db.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?)", (session, time.time()))
            old = app_db.execute("SELECT id FROM sessions WHERE last_seen < ?", (time.time() - visitors.SESSION_HOURS * 3600,))
            for (session,) in old:
                if slots.enter(session):
                    try:
                        forget_session(session)
                    finally:
                        slots.leave(session)
        except Exception:
            log.exception("could not delete old sessions")


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
                personal = agents.sensitive_columns(columns, rows)
                hidden = [i for i, c in enumerate(columns) if c in personal]
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
    with app_db.lock:
        position = app_db.execute("SELECT COUNT(*) FROM turns WHERE thread_id = ?", (turn["thread_id"],))[0][0]
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
        personal = agents.sensitive_columns(columns, rows)
        hidden = [i for i, c in enumerate(columns) if c in personal]
        rows = [["(hidden)" if i in hidden else v for i, v in enumerate(r)] for r in rows]
    return {"columns": columns, "rows": rows[:20], "total": len(rows), "hidden": 0}


def outcome_of(steps):
    """One word for the turn, for the badge on the answer."""
    if any(s["role"] == "stop" for s in steps):
        return "stopped"
    results = [s["text"] for s in steps if s["role"] == "monitor" and s["text"].startswith(("result", "personal"))]
    if results and "please verify" in results[-1]:
        return "verify"
    if any(s["role"] == "recovery" for s in steps):
        return "recovered"
    if any(s["role"] == "person" for s in steps):
        return "approved" if any("APPROVED" in s["text"] for s in steps) else "masked"
    if any("OFF" in s["text"] for s in steps if s["role"] == "monitor"):
        return "unchecked"
    return "checked"


class EventStream(StreamingResponse):
    """Server-Sent Events. When the visitor leaves in the middle, the run is closed at once, so its place is free again."""
    media_type = "text/event-stream"

    def __init__(self, events):
        super().__init__(events)
        self.events = events

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self.events.close)


def guarded(session, run_key, stream):
    """Runs one stream when there is a place for it, and reports errors to the page instead of dropping the connection."""
    if not slots.enter(session):
        yield sse("error", message="Another question is still running. Wait for it to finish.")
        return
    try:
        if not slots.places.acquire(blocking=False):
            yield sse("queued")
            if not slots.wait():
                yield sse("error", message="Many people are asking right now. Please try again in a minute.")
                return
        try:
            yield from in_place(run_key, stream)
        finally:
            slots.places.release()
    finally:
        slots.leave(session)


def in_place(run_key, stream):
    try:
        yield from stream
    except GraphRecursionError:
        yield sse("error", message="This question took too many steps, so it was stopped. Please ask it in a simpler way.")
    except Exception as exc:   # the page shows it; the server keeps running
        log.exception("a question failed")
        # online, the details stay in the server's log
        yield sse("error", message="Something went wrong while answering. Please try again." if PUBLIC else f"{type(exc).__name__}: {exc}")
    finally:
        reset_faults(run_key)


def settings_for(thread_id, run_key, fault=None):
    """The config of one run. The planted failure travels with the run, so it cannot reach anyone else's."""
    return {"configurable": {"thread_id": thread_id, "fault": fault, "run": run_key}, "recursion_limit": MAX_STEPS}


# ------------------------------------------------------------------ API

app = FastAPI(title="QueryMend", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(visitors.Guard)
if PUBLIC:
    threading.Thread(target=forget_old_sessions, daemon=True).start()


class AskBody(BaseModel):
    thread_id: str | None = Field(None, max_length=60)
    question: str = Field(max_length=visitors.QUESTION_LENGTH)
    user_id: str = Field("owner_pune", max_length=60)
    monitor_on: bool = True
    fault: str | None = Field(None, max_length=30)


class ResumeBody(BaseModel):
    thread_id: str = Field(max_length=60)
    decision: str = Field(max_length=10)   # "approve" or "reject"


class AcceptBody(BaseModel):
    token: str = Field(max_length=60)
    name: str = Field(max_length=200)


class TermBody(BaseModel):
    term: str = Field(max_length=60)
    meaning: str = Field(max_length=300)


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
def setup(request: Request):
    return {
        "users": shops.stores(who(request)),
        "public": PUBLIC, "keep_hours": visitors.SESSION_HOURS,
        "max_files": visitors.MAX_FILES, "max_file_mb": visitors.MAX_FILE_MB, "question_length": visitors.QUESTION_LENGTH,
        "faults": [{"id": k, "name": v} for k, v in FAULTS.items()],
        "retry_budget": RETRY_BUDGET,
    }


@app.get("/api/shop")
def shop(request: Request, user_id: str = "owner_pune"):
    """A few facts about the store, and questions to start with, for the welcome screen."""
    chosen = store_or_404(user_id, who(request))
    if not chosen.is_sample:
        about = chosen.about
        return {"name": chosen.name, "uploaded": True, "facts": about["facts"], "examples": about["questions"],
                "files": about["files"], "tables": [{"name": t["name"], "rows": t["rows"]} for t in about["tables"]]}
    with sqlite3.connect(f"file:{SHOP_DB}?mode=ro", uri=True) as conn:
        count = lambda table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        first, last = conn.execute("SELECT MIN(order_date), MAX(order_date) FROM orders").fetchone()
        facts = [{"label": t.capitalize(), "value": f"{count(t):,}"} for t in ("customers", "products", "orders")]
    facts.append({"label": "Period", "value": f"{month_name(first, short=True)} to {month_name(last, short=True)}"})
    return {"name": chosen.name, "uploaded": False, "facts": facts, "examples": EXAMPLES, "files": [], "tables": []}


@app.post("/api/shops/preview")
def preview_upload(request: Request, files: list[UploadFile] = File(...)):
    """Read the chosen files and show what was found. Nothing is added until the person accepts it."""
    allow("upload", request)
    session = who(request)
    if PUBLIC:
        if sum(s["uploaded"] for s in shops.stores(session)) >= visitors.STORES_EACH:
            raise HTTPException(400, f"You can keep {visitors.STORES_EACH} shops at a time. Remove one first.")
        if shops.disk_used() > visitors.DISK_MB * 1024 * 1024:
            raise HTTPException(503, "The online demo has no room for new files right now. Please try again later.")
    if len(files) > importer.MAX_FILES:
        raise HTTPException(400, f"Choose at most {importer.MAX_FILES} files at a time.")
    if not upload_lock.acquire(timeout=visitors.IMPORT_SECONDS + 10):
        raise HTTPException(503, "Another file is being read right now. Please try again in a minute.")
    try:
        # one byte over the limit is enough for the importer to refuse a file that is too big
        token, about = shops.prepare([(f.filename or "file", f.file.read(importer.MAX_FILE_MB * 1024 * 1024 + 1)) for f in files], session)
    except importer.ImportProblem as problem:
        raise HTTPException(400, str(problem))
    except Exception:
        log.exception("a file could not be read")
        raise HTTPException(400, "This file could not be read. Please export it again as Excel or CSV and try once more.")
    finally:
        upload_lock.release()
    return {"token": token, "name": suggested_name(files[0].filename or ""), "files": about["files"],
            "questions": about["questions"], "tables": shops.preview_tables(token, about)}


def suggested_name(filename):
    words = re.sub(r"[_\-.]+", " ", Path(filename).stem).strip()
    return (words[:1].upper() + words[1:])[:40] or "My shop"


@app.post("/api/shops")
def accept_upload(body: AcceptBody, request: Request):
    session = who(request)
    if PUBLIC and sum(s["uploaded"] for s in shops.stores(session)) >= visitors.STORES_EACH:
        raise HTTPException(400, f"You can keep {visitors.STORES_EACH} shops at a time. Remove one first.")
    try:
        store_id = shops.accept(body.token, body.name, session)
    except importer.ImportProblem as problem:
        raise HTTPException(400, str(problem))
    seen(session)
    return {"id": store_id, "users": shops.stores(session)}


@app.delete("/api/shops/preview/{token}")
def discard_upload(token: str, request: Request):
    shops.discard(token, who(request))
    return {"ok": True}


@app.delete("/api/shops/{store_id}")
def remove_shop(store_id: str, request: Request):
    """Remove an uploaded shop and everything that was said about it. The sample stores cannot be removed."""
    session = who(request)
    if store_or_404(store_id, session).is_sample:
        raise HTTPException(400, "The sample stores cannot be removed.")
    if not slots.enter(session):
        raise HTTPException(409, "A question is still running. Wait for it to finish.")
    try:
        forget_store(session, store_id)
    finally:
        slots.leave(session)
    return {"users": shops.stores(session)}


@app.delete("/api/session")
def delete_my_data(request: Request):
    """Online only: a visitor deletes everything they left here, without waiting for it to expire."""
    session = who(request)
    if not session:
        raise HTTPException(404, "Not found.")
    if not slots.enter(session):
        raise HTTPException(409, "A question is still running. Wait for it to finish.")
    try:
        forget_session(session)
    finally:
        slots.leave(session)
    return {"users": shops.stores(session)}


@app.post("/api/ask")
def ask(body: AskBody, request: Request):
    if body.fault and body.fault not in FAULTS:
        raise HTTPException(400, "Unknown failure type.")
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Please type a question.")
    session = who(request)
    # a conversation stays with the store it was started in, so two shops never mix in one thread.
    # An id that is not one of this visitor's own starts a new conversation.
    started_in = app_db.execute("SELECT user_id FROM conversations WHERE id = ? AND session = ?", (body.thread_id, session))
    thread_id = body.thread_id if started_in else f"web-{uuid.uuid4().hex}"
    user_id = started_in[0][0] if started_in else body.user_id
    team = team_for(body.monitor_on, user_id, session)
    allow("ask", request)
    if not started_in:
        app_db.execute("INSERT INTO conversations (id, title, user_id, created_at, session) VALUES (?, ?, ?, ?, ?)",
                       (thread_id, question[:80], user_id, datetime.now().isoformat(timespec="seconds"), session))
    run_key = uuid.uuid4().hex
    turn = {"thread_id": thread_id, "question": question, "fault": body.fault,
            "monitor_on": body.monitor_on, "user_id": user_id, "steps": [], "queries": [], "results": []}
    config = settings_for(thread_id, run_key, body.fault)

    def stream():
        pending.pop((session, thread_id), None)   # asking again instead of answering the approval
        turn.update(started=time.time(), seen=len(team.get_state(config).values.get("messages", [])))
        yield sse("start", thread_id=thread_id)
        yield from run(team, team_turn(question), config, turn)
        if "approval" in turn:
            pending[session, thread_id] = (team, turn)
            return
        yield finish(team, config, turn)

    return EventStream(guarded(session, run_key, stream()))


@app.post("/api/resume")
def resume(body: ResumeBody, request: Request):
    session = who(request)
    if (session, body.thread_id) not in pending or body.decision not in ("approve", "reject"):
        raise HTTPException(400, "Nothing is waiting for approval in this conversation.")
    run_key = uuid.uuid4().hex
    config = settings_for(body.thread_id, run_key)

    def stream():
        team, turn = pending.pop((session, body.thread_id))
        yield from run(team, Command(resume=body.decision), config, turn)
        yield finish(team, config, turn)

    return EventStream(guarded(session, run_key, stream()))


@app.get("/api/conversations")
def conversations(request: Request):
    rows = app_db.execute("""
        SELECT c.id, c.title, c.user_id, c.created_at, COUNT(t.position)
        FROM conversations c LEFT JOIN turns t ON t.thread_id = c.id WHERE c.session = ?
        GROUP BY c.id ORDER BY c.created_at DESC LIMIT 50""", (who(request),))
    return [{"id": r[0], "title": r[1], "user_id": r[2], "created_at": r[3], "turns": r[4]} for r in rows if r[4]]


@app.get("/api/conversations/{thread_id}")
def conversation(thread_id: str, request: Request):
    if not app_db.execute("SELECT 1 FROM conversations WHERE id = ? AND session = ?", (thread_id, who(request))):
        raise HTTPException(404, "This conversation does not exist any more.")
    rows = app_db.execute("SELECT payload FROM turns WHERE thread_id = ? ORDER BY position", (thread_id,))
    return [json.loads(r[0]) for r in rows]


@app.get("/api/failures")
def failures(request: Request):
    columns = FailureLog.COLUMNS
    rows = [dict(zip(columns, r)) for r in failure_log.rows()]
    if PUBLIC:   # each visitor sees only the failures of their own questions
        mine = set(threads_of(who(request)))
        rows = [r for r in rows if r["thread_id"] in mine]
    rows.reverse()
    counts = Counter(r["failure_type"] for r in rows).most_common()
    return {"counts": [{"type": t, "count": n} for t, n in counts], "rows": rows[:300]}


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
def glossary(user_id: str, request: Request):
    store_or_404(user_id, who(request))
    return [{"term": t, "meaning": m} for t, m in sorted(load_terms(store, scoped(who(request), user_id)).items())]


@app.post("/api/glossary/{user_id}")
def add_term(user_id: str, body: TermBody, request: Request):
    store_or_404(user_id, who(request))
    allow("word", request)
    if PUBLIC and len(load_terms(store, scoped(who(request), user_id))) >= visitors.TERMS_EACH:
        raise HTTPException(400, f"You can save {visitors.TERMS_EACH} words for a store.")
    if not body.term.strip() or not body.meaning.strip():
        raise HTTPException(400, "A word and its meaning are both needed.")
    save_term(store, scoped(who(request), user_id), body.term, body.meaning)
    return glossary(user_id, request)


# ------------------------------------------------------------------ the built frontend

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    # the page is checked for a newer build on every visit, otherwise the browser keeps showing an old one
    FRESH = {"Cache-Control": "no-cache"}

    @app.get("/{path:path}")
    def page(path: str):
        try:
            file = (FRONTEND_DIST / path).resolve()
        except (ValueError, OSError):   # not a possible file name
            return FileResponse(FRONTEND_DIST / "index.html", headers=FRESH)
        # serve only files inside the built frontend; a path with .. in it must not reach the rest of the disk
        inside = file.is_relative_to(FRONTEND_DIST.resolve())
        return FileResponse(file if inside and file.is_file() else FRONTEND_DIST / "index.html", headers=FRESH)


if __name__ == "__main__":
    import uvicorn

    host, port = ("0.0.0.0" if PUBLIC else "127.0.0.1"), int(os.environ.get("PORT", 8000))
    print(f"QueryMend is running at http://127.0.0.1:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning", limit_concurrency=100 if PUBLIC else None)
