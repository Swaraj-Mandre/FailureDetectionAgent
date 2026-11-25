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
from config import GENERAL_GUIDE_FILE, CORPUS_FILE, SHOP_DB, STATE_DIR
from personal import personal_columns

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


def shop_for(user_id):
    """The shop behind a store in the picker. None if there is no such store."""
    if user_id in SAMPLE_USERS:
        return SAMPLE
    if not re.fullmatch(r"shop-[0-9a-f]{10}", user_id or ""):
        return None
    folder = SHOPS_DIR / user_id
    if not (folder / "about.json").exists():
        return None
    about = json.loads((folder / "about.json").read_text(encoding="utf-8"))
    return Shop(user_id, about["name"], folder / "shop.db", about)


def stores():
    """Every store the picker can show."""
    found = [{"id": k, "name": v, "uploaded": False} for k, v in SAMPLE_USERS.items()]
    folders = sorted(SHOPS_DIR.glob("shop-*/about.json"), key=lambda p: p.stat().st_mtime) if SHOPS_DIR.exists() else []
    for path in folders:
        about = json.loads(path.read_text(encoding="utf-8"))
        found.append({"id": path.parent.name, "name": about["name"], "uploaded": True})
    return found


# ------------------------------------------------------------------ upload, preview, accept

def prepare(files):
    """Import the files into a waiting folder and describe what was found."""
    clear_old_waiting()
    token = uuid.uuid4().hex
    folder = WAITING_DIR / token
    folder.mkdir(parents=True)
    try:
        tables = importer.import_files(files, folder / "shop.db")
        about = describe(tables, folder / "shop.db")
        about["files"] = [name for name, _ in files]
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


def accept(token, name):
    """Turn a previewed upload into a shop. Returns its store id."""
    if not re.fullmatch(r"[0-9a-f]{32}", token) or not (WAITING_DIR / token / "about.json").exists():
        raise importer.ImportProblem("This upload has expired. Please choose the file again.")
    folder = WAITING_DIR / token
    about = json.loads((folder / "about.json").read_text(encoding="utf-8"))
    about["name"] = re.sub(r"\s+", " ", name).strip()[:40] or "My shop"
    about["created_at"] = datetime.now().isoformat(timespec="seconds")
    (folder / "about.json").write_text(json.dumps(about, ensure_ascii=False), encoding="utf-8")
    SHOPS_DIR.mkdir(parents=True, exist_ok=True)
    store_id = f"shop-{uuid.uuid4().hex[:10]}"
    shutil.move(str(folder), str(SHOPS_DIR / store_id))
    return store_id


def remove(store_id):
    shop = shop_for(store_id)
    if shop is None or shop.is_sample:
        return False
    shutil.rmtree(shop.db.parent, ignore_errors=True)
    return True


def discard(token):
    if re.fullmatch(r"[0-9a-f]{32}", token):
        shutil.rmtree(WAITING_DIR / token, ignore_errors=True)


def clear_old_waiting():
    if WAITING_DIR.exists():
        for folder in WAITING_DIR.iterdir():
            if time.time() - folder.stat().st_mtime > WAITING_HOURS * 3600:
                shutil.rmtree(folder, ignore_errors=True)


# ------------------------------------------------------------------ describing a database

QUANTITY = re.compile(r"(^|_)(qty|quantity|pcs|pieces|units?|nos|stock)(_|$)|मात्रा|नग")
MONEY = re.compile(r"(^|_)(amount|amt|total|value|net|sale|sales|revenue|bill_amount|grand_total)(_|$)|राशी|रक्कम|राशि")
PRICE = re.compile(r"(^|_)(price|rate|mrp|cost)(_|$)|दर|किंमत")
COST = re.compile(r"(^|_)(cost|purchase|buy|buying|landing)(_|$)")
ID_LIKE = re.compile(r"(^|_)(id|no|number|num|code|year|month|pin|hsn|sr)(_|$)")
BILL = re.compile(r"(^|_)(invoice|bill|order|voucher|receipt)(_|$)")
NAMELIKE = re.compile(r"(^|_)(item|product|medicine|particulars|description|desc|name|party|customer|category|brand)(_|$)"
                      r"|वस्तू|नाव|वस्तु|नाम")
DATE_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}")


def describe(tables, db):
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
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
        roles = find_roles(out)
        return {"name": "", "tables": out, "limits": work_out_limits(conn, out),
                "examples": example_queries(conn, roles, out), "facts": facts(out, roles),
                "questions": example_questions(conn, roles, out)}
    finally:
        conn.close()


def q(name):
    return f'"{name}"' if not re.fullmatch(r"[a-z_][a-z0-9_]*", name) else name


def numeric(table):
    """Columns that can be added up: numbers that are not ids or links."""
    links = {j[0] for j in table["joins"]}
    return [c for c in table["columns"] if c["type"] != "TEXT" and not ID_LIKE.search(c["name"]) and c["name"] not in links]


def work_out_limits(conn, tables):
    """The largest total this shop's data can produce, and the largest count. The Monitor uses both."""
    one = lambda sql: abs(conn.execute(sql).fetchone()[0] or 0)
    biggest, negative = 0, False
    for t in tables:
        cols = numeric(t)
        for c in cols:
            biggest = max(biggest, one(f"SELECT SUM(ABS({q(c['name'])})) FROM {q(t['name'])}"))
            negative = negative or c.get("min", 0) < 0
        for c in t["columns"]:
            if c["type"] != "TEXT" and c["filled"]:
                biggest = max(biggest, abs(c["max"]), abs(c["min"]))
        quantities = [c for c in cols if QUANTITY.search(c["name"])]
        for a in quantities:
            for b in cols:
                if PRICE.search(b["name"]):
                    biggest = max(biggest, one(f"SELECT SUM(ABS({q(a['name'])} * {q(b['name'])})) FROM {q(t['name'])}"))
            for mine, other, theirs in t["joins"]:
                parent = next((x for x in tables if x["name"] == other), None)
                for b in numeric(parent) if parent else []:
                    if PRICE.search(b["name"]):
                        biggest = max(biggest, one(
                            f"SELECT SUM(ABS(a.{q(a['name'])} * b.{q(b['name'])})) FROM {q(t['name'])} a "
                            f"JOIN {q(other)} b ON a.{q(mine)} = b.{q(theirs)}"))
    return {"gross": biggest, "max_count": max(t["rows"] for t in tables), "negative_ok": negative,
            "columns": sorted({c["name"] for t in tables for c in t["columns"]}),
            # text columns can hold digits too, such as bar codes: those are not amounts
            "labels": sorted({c["name"] for t in tables for c in t["columns"] if c["type"] == "TEXT"}
                             - {c["name"] for t in tables for c in t["columns"] if c["type"] != "TEXT"})}


def find_roles(tables):
    """The main sales-like table and what its columns mean, guessed from names. None where unsure."""
    def score(t):
        dated = any(c.get("date") for c in t["columns"])
        return (dated and bool(numeric(t)), any(MONEY.search(c["name"]) for c in numeric(t)), t["rows"])

    main = max(tables, key=score)
    pick = lambda pattern, cols: next((c for c in cols if pattern.search(c["name"])), None)
    cols = numeric(main)
    money, quantity = pick(MONEY, cols), pick(QUANTITY, cols)
    texts = [c for c in main["columns"] if c["type"] == "TEXT" and not c["personal"] and not c.get("date")
             and c["distinct"] > 1 and not ID_LIKE.search(c["name"])]
    label = pick(NAMELIKE, [c for c in texts if re.search(r"item|product|medicine|particular|desc|वस्तू|वस्तु", c["name"])])
    label = label or pick(NAMELIKE, texts)
    bill = next((c for c in main["columns"] if BILL.search(c["name"]) and not c.get("date") and not c["personal"]
                 and (c["type"] == "INTEGER" or ID_LIKE.search(c["name"])) and c["distinct"] < c["filled"]), None)
    roles = {"table": main["name"], "date": next((c["name"] for c in main["columns"] if c.get("date")), None),
             "last": next((c["max"] for c in main["columns"] if c.get("date")), None),
             "first": next((c["min"] for c in main["columns"] if c.get("date")), None),
             "money": money["name"] if money else None, "quantity": quantity["name"] if quantity else None,
             "label": label["name"] if label else None, "bill": bill["name"] if bill else None, "join": None}
    # quantity here, price and name in a linked table (a normal billing database)
    if not money and quantity:
        for mine, other, theirs in main["joins"]:
            parent = next(t for t in tables if t["name"] == other)
            price = pick(re.compile(r"(^|_)(sell|selling|sale|mrp|unit)(_|$)"), [c for c in numeric(parent) if PRICE.search(c["name"])])
            price = price or pick(PRICE, numeric(parent))
            name = pick(NAMELIKE, [c for c in parent["columns"] if c["type"] == "TEXT" and not c["personal"]])
            if price:
                roles["join"] = {"mine": mine, "table": other, "theirs": theirs, "price": price["name"],
                                 "label": name["name"] if name else None}
                break
    return roles


def value_sql(r):
    """(expression, FROM part, label expression) for 'how much was sold'."""
    if r["join"]:
        j = r["join"]
        source = f"{q(r['table'])} a JOIN {q(j['table'])} b ON b.{q(j['theirs'])} = a.{q(j['mine'])}"
        label = f"b.{q(j['label'])}" if j["label"] else (f"a.{q(r['label'])}" if r["label"] else None)
        return f"SUM(a.{q(r['quantity'])} * b.{q(j['price'])})", source, label
    measure = r["money"] or r["quantity"]
    if not measure:
        return None, None, None
    return f"SUM({q(measure)})", q(r["table"]), q(r["label"]) if r["label"] else None


def date_column(r):
    return ("a." if r["join"] else "") + q(r["date"])


def example_queries(conn, r, tables):
    """Worked examples on the shop's own columns. Small models copy a pattern better than they follow a rule.
    Each one is run first, so a wrong guess never reaches the prompt."""
    made = stock_examples(tables)
    value, source, label = value_sql(r)
    if not value:
        return run_examples(conn, made)
    if r["date"]:
        month = r["last"][:7]
        made.append((f"total in {month_name(month)}?",
                     f"SELECT {value} AS total FROM {source} WHERE {date_column(r)} LIKE '{month}%'"))
    if label:
        made.append((f"top 3 {plain(label)} by total?",
                     f"SELECT {label}, {value} AS total FROM {source} GROUP BY {label} ORDER BY total DESC LIMIT 3"))
    made += profit_examples(r, tables) + linked_examples(r, tables)
    due, who = owed(r, tables)
    if due and who:
        made.append(("who owes the most?", f"SELECT {q(who)}, SUM({q(due)}) AS total FROM {q(r['table'])} "
                                           f"GROUP BY {q(who)} ORDER BY total DESC LIMIT 1"))
    if r["bill"] and not r["join"]:
        made.append(("average bill value?",
                     f"SELECT AVG(t) AS average FROM (SELECT {value} AS t FROM {source} GROUP BY {q(r['bill'])})"))
        made.append((f"how many {plain(r['bill'])}s?", f"SELECT COUNT(DISTINCT {q(r['bill'])}) AS how_many FROM {source}"))
    return run_examples(conn, made)


def owed(r, tables):
    """The unpaid amount column and the customer column of the main table, or None."""
    main = next(t for t in tables if t["name"] == r["table"])
    due = next((c["name"] for c in numeric(main) if re.search(r"due|balance|pending|credit|udhar", c["name"])), None)
    who = next((c["name"] for c in main["columns"] if re.search(r"party|customer|client", c["name"])
                and c["type"] == "TEXT" and not c["personal"]), None)
    return due, who


def sell_and_cost(table):
    """The selling price and the cost price column of a table, None where there is none."""
    prices = [c["name"] for c in numeric(table) if PRICE.search(c["name"])]
    cost = next((n for n in prices if COST.search(n)), None)
    return next((n for n in prices if n != cost), None), cost


def stock_examples(tables):
    for t in tables:
        stock = next((c["name"] for c in numeric(t) if "stock" in c["name"] and QUANTITY.search(c["name"])), None)
        sell, _ = sell_and_cost(t)
        if stock and sell:
            return [("value of the stock in hand?", f"SELECT SUM({q(stock)} * {q(sell)}) AS total FROM {q(t['name'])}")]
    return []


def profit_examples(r, tables):
    """Profit is per piece, so it must be multiplied by the pieces sold."""
    if not r["quantity"]:
        return []
    main = next(t for t in tables if t["name"] == r["table"])
    sell, cost = sell_and_cost(main)
    if sell and cost:
        return [("total profit?", f"SELECT SUM({q(r['quantity'])} * ({q(sell)} - {q(cost)})) AS profit FROM {q(main['name'])}")]
    for mine, other, theirs in main["joins"]:
        sell, cost = sell_and_cost(next(t for t in tables if t["name"] == other))
        if sell and cost:
            return [("total profit?", f"SELECT SUM(a.{q(r['quantity'])} * (b.{q(sell)} - b.{q(cost)})) AS profit "
                                      f"FROM {q(main['name'])} a JOIN {q(other)} b ON b.{q(theirs)} = a.{q(mine)}")]
    return []


def linked_examples(r, tables):
    """A total of the main table, split by a text column that sits in a linked table."""
    if r["join"] or not r["money"]:
        return []
    main = next(t for t in tables if t["name"] == r["table"])
    for mine, other, theirs in main["joins"]:
        parent = next(t for t in tables if t["name"] == other)
        column = next((c["name"] for c in parent["columns"] if c["type"] == "TEXT" and not c["personal"]
                       and not c.get("date") and c["name"] != theirs and not ID_LIKE.search(c["name"])), None)
        if column:
            return [(f"which {plain(column)} has the highest total?",
                     f"SELECT b.{q(column)}, SUM(a.{q(r['money'])}) AS total FROM {q(main['name'])} a "
                     f"JOIN {q(other)} b ON b.{q(theirs)} = a.{q(mine)} GROUP BY b.{q(column)} ORDER BY total DESC LIMIT 1")]
    return []


def run_examples(conn, made):
    good = []
    for question, sql in made:
        try:
            if conn.execute(sql).fetchone():
                good.append([question, sql])
        except sqlite3.Error:
            pass
    return good


def example_questions(conn, r, tables):
    """Questions for the start screen, in plain words, that this data can answer."""
    value, _, label = value_sql(r)
    asked = []
    thing = "sale" if (r["money"] or r["join"]) else plain(r["quantity"] or "")
    if value and r["date"]:
        asked.append(f"What was the total {thing} in {month_name(r['last'][:7])}?")
    if value and label:
        asked.append(f"Which 3 {plain(label)}s have the highest {thing}?")
    if r["bill"]:
        asked.append(f"How many {plain(r['bill'])}s are there?")
    due, who = owed(r, tables)
    if due and who:
        asked.append("Who owes me the most money?")
    if profit_examples(r, tables):
        asked.append("What is the total profit?")
    for t in tables:
        stock = next((c["name"] for c in numeric(t) if "stock" in c["name"]), None)
        name = next((c["name"] for c in t["columns"] if NAMELIKE.search(c["name"]) and c["type"] == "TEXT" and not c["personal"]), None)
        if stock and name:
            asked.append(f"Which {plain(name)} has the lowest {plain(stock)}?")
            break
    asked += [f"How many rows are in {plain(t['name'])}?" for t in tables[:2]] if len(asked) < 2 else []
    return asked[:5]


def facts(tables, r):
    """A few numbers for the start screen."""
    out = [{"label": "Rows" if len(tables) == 1 else plain(t["name"]).capitalize(), "value": f"{t['rows']:,}"} for t in
           sorted(tables, key=lambda t: -t["rows"])[:3]]
    if r["date"]:
        out.append({"label": "Period", "value": f"{month_name(r['first'][:7], short=True)} to {month_name(r['last'][:7], short=True)}"})
    return out


def plain(name):
    """A column name in words: tbl_sale -> sale, itm_desc -> item, invoice_no -> invoice."""
    words = name.split(".")[-1].strip('"')
    words = re.sub(r"^(tbl|table|tb)_", "", words)
    words = re.sub(r"^(itm|prod)(_|$)", r"item\2", words)
    words = re.sub(r"^(item|product|medicine|party|customer|supplier|bill|invoice|order|voucher|receipt)"
                   r"_(name|desc|description|no|number|id)$", r"\1", words)
    return re.sub(r"\bno\b", "number", words.replace("_", " ")) if words != "no" else words


MONTH_NAMES = "January February March April May June July August September October November December".split()


def month_name(text, short=False):
    name = MONTH_NAMES[int(text[5:7]) - 1]
    return f"{name[:3] if short else name} {text[:4]}"


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
            if BILL.search(c["name"]) and ID_LIKE.search(c["name"]) and c["distinct"] < c["filled"]:
                detail = ", the same number is on several rows, count with COUNT(DISTINCT)"
            lines.append(f"  {q(c['name'])}: {kind}{detail}")
    joins = [f"{q(t['name'])}.{q(a)} = {q(b)}.{q(c)}" for t in about["tables"] for a, b, c in t["joins"]]
    if joins:
        lines.append("Links between tables (join on these):")
        lines += [f"  {j}" for j in joins]
    return "\n".join(lines)


def system_prompt(shop):
    about = shop.about
    examples = "\n\n".join(f"Example. Question: {question}\n{sql}" for question, sql in about["examples"])
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
- If a column already holds the amount or total, add that up. Otherwise multiply quantity by price.
- Put brackets around sums of differences: SUM(quantity * (sell - cost)).
- Dates are text. One month: LIKE '2026-09%'. Before a date: column < '2027-01-01'.
- Money is in Indian rupees (Rs).
- If the user tells you what a business word means, save it with remember_term.
- If these tables cannot answer the question, say so plainly and do not guess.
- When you have the result, answer in one or two short sentences.

{examples}"""
