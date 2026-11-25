"""Which shop a question is about: the built-in sample shop, or one a shopkeeper uploaded.

Each uploaded shop is a folder in ta2/state/shops/ with two files:
    shop.db      the data, built by importer.py
    about.json   its name, tables, links, limits and example questions, worked out from the data

Everything the agents need to know about a shop (prompt, Monitor limits, error guide) comes
from here, so no agent has a shop written into it.
"""
import json
import re
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import importer
import read_apart
from config import GENERAL_GUIDE_FILE, CORPUS_FILE, SHOP_DB, STATE_DIR
from meaning import (DATE_TEXT, ID_LIKE, column_note, example_queries, example_questions, facts, find_meaning, Parts, plain, q,
                     work_out_limits)
from personal import personal_columns
from visitors import PUBLIC

SHOPS_DIR = STATE_DIR / "shops"
WAITING_DIR = STATE_DIR / "shops_waiting"   # uploaded and previewed, not accepted yet
WAITING_HOURS = 1
MAX_VALUES = 8          # a text column with this few different values is listed in the prompt

SAMPLE_USERS = {"owner_pune": "Pune store", "owner_mumbai": "Mumbai store"}


@dataclass
class Shop:
    id: str
    name: str
    db: Path
    about: dict | None = None   # None for the sample shop, which keeps its own prompt and limits

    @property
    def is_sample(self):
        return self.about is None

    @property
    def guide(self):
        return CORPUS_FILE if self.is_sample else GENERAL_GUIDE_FILE


SAMPLE = Shop("sample", "Sahyadri Stores", SHOP_DB)


def read_about(folder, session):
    """A shop's description. None if it is gone, or belongs to another visitor."""
    try:
        about = json.loads((folder / "about.json").read_text(encoding="utf-8"))
    except OSError:   # removed in this very moment, or never there
        return None
    return about if about.get("session", "") == session else None


def shop_for(user_id, session=""):
    """The shop behind a store in the picker. None if there is no such store, or it is another visitor's."""
    if user_id in SAMPLE_USERS:
        return SAMPLE
    if not re.fullmatch(r"shop-[0-9a-f]{10}", user_id or ""):
        return None
    folder = SHOPS_DIR / user_id
    about = read_about(folder, session)
    if about is None:
        return None
    if "meaning" not in about:   # added before this version
        about = understand(about, folder / "shop.db")
        (folder / "about.json").write_text(json.dumps(about, ensure_ascii=False), encoding="utf-8")
    return Shop(user_id, about["name"], folder / "shop.db", about)


def stores(session=""):
    """Every store the picker can show to this visitor."""
    found = [{"id": k, "name": v, "uploaded": False} for k, v in SAMPLE_USERS.items()]
    uploaded = [(folder, read_about(folder, session)) for folder in SHOPS_DIR.glob("shop-*")]
    for folder, about in sorted(((f, a) for f, a in uploaded if a), key=lambda pair: pair[1].get("created_at", "")):
        found.append({"id": folder.name, "name": about["name"], "uploaded": True})
    return found


def sessions_with_stores():
    """Visitors who have an uploaded shop on the disk."""
    found = set()
    for path in SHOPS_DIR.glob("shop-*/about.json"):
        try:
            found.add(json.loads(path.read_text(encoding="utf-8")).get("session", ""))
        except OSError:
            pass
    return found - {""}


# ------------------------------------------------------------------ upload, preview, accept

def prepare(files, session=""):
    """Import the files into a waiting folder and describe what was found."""
    clear_old_waiting()
    token = uuid.uuid4().hex
    folder = WAITING_DIR / token
    folder.mkdir(parents=True)
    try:
        if PUBLIC:
            about = read_apart.read(files, folder)
        else:
            about = describe(importer.import_files(files, folder / "shop.db"), folder / "shop.db")
        about["files"] = [name[:80] for name, _ in files]
        about["session"] = session
        (folder / "about.json").write_text(json.dumps(about, ensure_ascii=False), encoding="utf-8")
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return token, about


def preview_tables(token, about):
    """The tables with their first rows, personal columns hidden, for the person to check before accepting."""
    conn = sqlite3.connect(f"file:{(WAITING_DIR / token / 'shop.db').as_posix()}?mode=ro", uri=True)
    try:
        out = []
        for t in about["tables"]:
            rows = conn.execute(f"SELECT * FROM {q(t['name'])} LIMIT 4").fetchall()
            hidden = {i for i, c in enumerate(t["columns"]) if c["personal"]}
            out.append({"name": t["name"], "source": t["source"], "rows": t["rows"], "notes": t["notes"],
                        "columns": [{"name": c["name"], "type": kind(c), "personal": c["personal"]} for c in t["columns"]],
                        "sample": [["(hidden)" if i in hidden else v for i, v in enumerate(r)] for r in rows],
                        "joins": [f"{a} matches {c} in {plain(b)}" for a, b, c in t["joins"]]})
        return out
    finally:
        conn.close()


def kind(column):
    if column.get("date"):
        return "date"
    return "text" if column["type"] == "TEXT" else "number"


def accept(token, name, session=""):
    """Turn a previewed upload into a shop. Returns its store id."""
    folder = WAITING_DIR / token
    about = read_about(folder, session) if re.fullmatch(r"[0-9a-f]{32}", token) else None
    if about is None:
        raise importer.ImportProblem("This upload has expired. Please choose the file again.")
    about["name"] = re.sub(r"\s+", " ", name).strip()[:40] or "My shop"
    about["created_at"] = datetime.now().isoformat(timespec="seconds")
    (folder / "about.json").write_text(json.dumps(about, ensure_ascii=False), encoding="utf-8")
    SHOPS_DIR.mkdir(parents=True, exist_ok=True)
    store_id = f"shop-{uuid.uuid4().hex[:10]}"
    shutil.move(str(folder), str(SHOPS_DIR / store_id))
    return store_id


def remove(store_id, session=""):
    shop = shop_for(store_id, session)
    if shop is None or shop.is_sample:
        return False
    shutil.rmtree(shop.db.parent, ignore_errors=True)
    return True


def discard_all(session):
    """Uploads a visitor previewed but never accepted."""
    for folder in WAITING_DIR.glob("*"):
        if read_about(folder, session):
            shutil.rmtree(folder, ignore_errors=True)


def discard(token, session=""):
    if re.fullmatch(r"[0-9a-f]{32}", token) and read_about(WAITING_DIR / token, session):
        shutil.rmtree(WAITING_DIR / token, ignore_errors=True)


def disk_used():
    """Bytes used by all uploaded shops, accepted or waiting."""
    total = 0
    for base in (SHOPS_DIR, WAITING_DIR):
        for file in base.rglob("*"):
            try:
                total += file.stat().st_size
            except OSError:   # removed in this very moment
                pass
    return total


def clear_old_waiting():
    if WAITING_DIR.exists():
        for folder in WAITING_DIR.iterdir():
            try:
                old = time.time() - folder.stat().st_mtime > WAITING_HOURS * 3600
            except OSError:   # accepted or removed in this very moment
                continue
            if old:
                shutil.rmtree(folder, ignore_errors=True)


# ------------------------------------------------------------------ describing a database

def describe(tables, db):
    """Everything the agents, the Monitor and the page need to know about the imported tables."""
    out = []
    for t in tables:
        personal = set(personal_columns(t.columns, t.rows))
        columns = []
        for i, name in enumerate(t.columns):
            values = [r[i] for r in t.rows if r[i] is not None]
            column = {"name": name, "original": t.original[i], "type": t.types[i], "personal": name in personal,
                      "distinct": len(set(values)), "filled": len(values)}
            if values:
                text = t.types[i] == "TEXT"
                column["date"] = text and all(DATE_TEXT.fullmatch(str(v)) for v in values)
                if not text or column["date"]:
                    column["min"], column["max"] = min(values), max(values)
                elif name not in personal and column["distinct"] <= MAX_VALUES:
                    column["values"] = sorted(set(map(str, values)))
            columns.append(column)
        out.append({"name": t.name, "source": t.source, "rows": len(t.rows), "columns": columns, "notes": t.notes,
                    "joins": [list(j) for j in t.joins]})
    return understand({"name": "", "tables": out}, db)


def understand(about, db):
    """Adds what the columns mean, and what follows from it: limits, examples, facts and questions."""
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        tables, meaning = about["tables"], find_meaning(conn, about["tables"])
        return {**about, "meaning": meaning, "limits": work_out_limits(conn, tables, meaning),
                "examples": example_queries(conn, tables, meaning), "facts": facts(tables, meaning),
                "questions": example_questions(tables, meaning)}
    finally:
        conn.close()


# ------------------------------------------------------------------ what the Executor is told

def schema_text(about):
    lines = []
    for t in about["tables"]:
        lines.append(f"{q(t['name'])} ({t['rows']} rows)")
        for c in t["columns"]:
            kind = {"INTEGER": "whole number", "REAL": "number"}.get(c["type"], "text")
            detail = ""
            if c.get("date"):
                kind, detail = "date text YYYY-MM-DD", f", from {c['min']} to {c['max']}"
            elif c.get("values"):
                detail = ", one of: " + ", ".join("'" + v[:30].replace("'", "''") + "'" for v in c["values"])
            elif "min" in c and not ID_LIKE.search(c["name"]):
                detail = f", from {c['min']:g} to {c['max']:g}"
            note = column_note(about["meaning"][t["name"]], c["name"])
            lines.append(f"  {q(c['name'])}: {kind}{', ' + note if note else ''}{detail}")
    joins = [f"{q(t['name'])}.{q(a)} = {q(b)}.{q(c)}" for t in about["tables"] for a, b, c in t["joins"]]
    if joins:
        lines.append("Links between tables (join on these):")
        lines += [f"  {j}" for j in joins]
    return "\n".join(lines)


def system_prompt(shop):
    about = shop.about
    examples = "\n\n".join(f"Example. Question: {question}\n{sql}" for question, sql in about["examples"])
    unpaid, table = Parts(about["tables"], about["meaning"]).unpaid()
    unpaid = f"\n- Money still unpaid is {unpaid} in {q(table)}." if unpaid and " - " in unpaid else ""
    return f"""You answer questions about the data of the shop "{shop.name}".
Always use the run_sql tool to get the data. Never make up numbers.

Tables (SQLite):
{schema_text(about)}

Rules:
- Use only the tables and columns above, spelled exactly the same.
- A column belongs only to the table it is listed under. If you need columns from two tables, join them on the link above.
- A question about one total needs one SUM and no GROUP BY.
- Use GROUP BY only for "which", "each", "per" or "top" questions, with ORDER BY and LIMIT for "which" and "top".
- For "per bill" or "per invoice" averages, first add up each bill in a subquery, then take AVG.
- If a column already holds the amount or total, add that up. Otherwise multiply the number of pieces by the price of one piece.
- Put brackets around sums of differences: SUM(quantity * (sell - cost)).{unpaid}
- "Which month" needs GROUP BY strftime('%m', date column).
- The examples show the pattern only. Use a filter on a date, name or type only when the question asks for it.
- Dates are text. One month: LIKE '2026-09%'. Before a date: column < '2027-01-01'.
- Money is in Indian rupees (Rs).
- If the user tells you what a business word means, save it with remember_term.
- If these tables cannot answer the question, say so plainly and do not guess.
- When you have the result, answer in one or two short sentences.

{examples}"""
