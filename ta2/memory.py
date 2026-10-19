"""Memory for PBA 2. Three separate SQLite files, three separate jobs.

checkpoints.db   short-term memory: the conversation of one thread_id, saved
                 after every node. Also what lets a crashed run resume.
store.db         long-term memory: facts about a user that every thread can
                 read, here a business glossary ("revenue means ...").
failure_log.db   a plain table of every failure, for the evidence tables.

All three survive a restart because they are files, not RAM.
"""
import sqlite3
from datetime import datetime

from langchain_core.tools import tool
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore

from config import CHECKPOINT_DB, FAILURE_LOG_DB, STORE_DB


def open_db(path):
    # check_same_thread=False because LangGraph may touch the connection from worker threads.
    return sqlite3.connect(path, check_same_thread=False, isolation_level=None)


def make_checkpointer():
    return SqliteSaver(open_db(CHECKPOINT_DB))


def make_store():
    store = SqliteStore(open_db(STORE_DB))
    store.setup()
    return store


# ---------------------------------------------------------------- glossary

def glossary_namespace(user_id):
    return (user_id, "glossary")


def save_term(store, user_id, term, meaning):
    store.put(glossary_namespace(user_id), term.strip().lower(), {"meaning": meaning.strip()})


def load_terms(store, user_id):
    """All saved terms for a user, as {term: meaning}."""
    return {item.key: item.value["meaning"] for item in store.search(glossary_namespace(user_id), limit=100)}


def glossary_text(store, user_id):
    terms = load_terms(store, user_id)
    if not terms:
        return ""
    lines = "\n".join(f"- {term}: {meaning}" for term, meaning in sorted(terms.items()))
    return f"\nThis user's own definitions (follow them):\n{lines}"


def make_glossary_tool(store, user_id):
    """A tool the agent can call when the user defines a business word."""

    @tool
    def remember_term(term: str, meaning: str) -> str:
        """Save what the user means by a business word (for example 'revenue' or 'big order')
        so it is remembered in every future conversation. Use it only when the user defines a word."""
        save_term(store, user_id, term, meaning)
        return f"Saved: '{term}' means {meaning}"

    return remember_term


# ------------------------------------------------------------- failure log

class FailureLog:
    """One row per failure: who saw it, what kind, what was tried, what was left."""

    COLUMNS = ("logged_at", "thread_id", "agent", "failure_type", "sql", "error", "action", "retries_left")

    def __init__(self, path=FAILURE_LOG_DB):
        self.conn = open_db(path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS failure_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                logged_at    TEXT NOT NULL,
                thread_id    TEXT,
                agent        TEXT NOT NULL,
                failure_type TEXT NOT NULL,
                sql          TEXT,
                error        TEXT,
                action       TEXT,
                retries_left INTEGER
            )""")

    def add(self, thread_id, agent, failure_type, sql, error, action, retries_left):
        self.conn.execute(
            f"INSERT INTO failure_log ({', '.join(self.COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), thread_id, agent, failure_type,
             sql, error, action, retries_left))

    def rows(self, thread_id=None):
        query = f"SELECT {', '.join(self.COLUMNS)} FROM failure_log"
        args = ()
        if thread_id:
            query += " WHERE thread_id = ?"
            args = (thread_id,)
        return self.conn.execute(query + " ORDER BY id", args).fetchall()

    def counts_by_type(self):
        return self.conn.execute(
            "SELECT failure_type, COUNT(*) FROM failure_log GROUP BY failure_type ORDER BY 2 DESC").fetchall()

    def clear(self):
        self.conn.execute("DELETE FROM failure_log")
