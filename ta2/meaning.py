"""What the columns of an uploaded shop mean: which one is the date, the pieces, the price of one
piece, the total of a line, the money still to be paid, the bill number.

Worked out from the numbers first (if a x b = c on nearly every row, then c is the total and a or b
is the number of pieces) and from the names only where the numbers do not decide. The result is used
three times: in the prompt, in the worked examples, and by the Monitor's rules (checks.py).
"""
import re
import sqlite3
from itertools import permutations

WORD = lambda words: rf"(^|_)({words})(_|$)"
QUANTITY = re.compile(WORD("qty|quantity|pcs|pieces?|units?|nos|stock|bags?|boxes?|packets?|pkts?|bottles?|cartons?|dozens?|kgs?|kilos?|"
                           "litres?|liters?|ltrs?|metres?|meters?|strips?|plates?|nag") + "|मात्रा|नग")
STOCK = re.compile(WORD("stock|in_hand|on_hand|closing|available|left"))
MONEY = re.compile(WORD("amount|amt|total|value|net|sale|sales|revenue|bill_amount|grand_total") + "|राशी|रक्कम|राशि")
PRICE = re.compile(WORD("price|rate|mrp|cost") + "|दर|किंमत")
COST = re.compile(WORD("cost|purchase|buy|buying|landing"))
SELL = re.compile(WORD("sell|selling|sale|mrp"))
DUE = re.compile(WORD("due|dues|balance|bal|pending|outstanding|unpaid|credit|udhar|udhari|udhaar|baki|bakaya|receivable") + "|बाकी|उधार")
PAID = re.compile(WORD("paid|received|advance|jama|deposit") + "|जमा")
ID_LIKE = re.compile(WORD("id|no|number|num|code|year|month|pin|hsn|sr"))
BILL = re.compile(WORD("invoice|inv|bill|order|voucher|receipt|visit|token|kot"))
ITEM = re.compile(WORD("item|product|medicine|particulars?|description|desc|model|service|dish") + "|वस्तू|वस्तु")
WHO = re.compile(WORD("party|customer|cust|client|farmer|member|patient|student|buyer") + "|ग्राहक")
NAMELIKE = re.compile(WORD("name|party|customer|category|brand|group|supplier|vendor|company") + "|नाव|नाम|" + ITEM.pattern + "|" + WHO.pattern)
DATE_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}")
MONTH_NAMES = "January February March April May June July August September October November December".split()
MOSTLY = 0.8
SAMPLE_ROWS = 2000   # enough rows to see that pieces x price = total


def q(name):
    return f'"{name}"' if not re.fullmatch(r"[a-z_][a-z0-9_]*", name) else name


def plain(name):
    """A column name in words: tbl_sale -> sale, itm_desc -> item, invoice_no -> invoice."""
    words = name.split(".")[-1].strip('"')
    words = re.sub(r"^(tbl|table|tb)_", "", words)
    words = re.sub(r"^(itm|prod)(_|$)", r"item\2", words)
    words = re.sub(r"^(cust)(_|$)", r"customer\2", words)
    words = re.sub(r"^inv(_|$)", r"invoice\1", words)
    words = re.sub(r"^(item|product|medicine|party|customer|supplier|bill|invoice|order|voucher|receipt|visit|group|token)"
                   r"_(name|desc|description|no|number|id)$", r"\1", words)
    return re.sub(r"\bno\b", "number", words.replace("_", " ")) if words != "no" else words


def month_name(text, short=False):
    name = MONTH_NAMES[int(text[5:7]) - 1]
    return f"{name[:3] if short else name} {text[:4]}"


def numeric(table):
    """Columns that can be added up: numbers that are not ids or links."""
    links = {j[0] for j in table["joins"]}
    return [c for c in table["columns"] if c["type"] != "TEXT" and not ID_LIKE.search(c["name"]) and c["name"] not in links]


# ------------------------------------------------------------------ finding the meaning

def find_meaning(conn, tables):
    """{table: meaning}. Every value is a column name, a list of them, or None."""
    parents = {j[1] for t in tables for j in t["joins"]}
    return {t["name"]: table_meaning(conn, t, t["name"] in parents) for t in tables}


def table_meaning(conn, t, is_parent):
    names = [c["name"] for c in numeric(t)]
    pick = lambda pattern, among: next((n for n in among if pattern.search(n)), None)
    due, paid = pick(DUE, names), pick(PAID, [n for n in names if not DUE.search(n)])
    total, qty, price = find_product(conn, t, [n for n in names if n not in (due, paid)])

    stock = pick(STOCK, [n for n in names if not PRICE.search(n) and not MONEY.search(n) or re.search("stock", n) and QUANTITY.search(n)])
    quantities = [n for n in names if QUANTITY.search(n) and not PRICE.search(n) and not MONEY.search(n)]
    if qty and qty not in quantities:
        quantities.insert(0, qty)
    prices = [n for n in names if (PRICE.search(n) or n == price) and n not in quantities and n != total
              and (n == price or not re.search("amount|total|value", n))]
    unit = [p for p in prices if p == price or (not quantities and is_parent) or (quantities and per_piece(conn, t, p, quantities[0]))]
    sold = [n for n in quantities if n != stock]

    money = total if total and total != stock else None
    if not money:
        named = [n for n in names if MONEY.search(n) and n not in quantities + prices and n not in (due, paid, stock)]
        # the total of a bill first (grand total, net amount), then any amount
        money = pick(re.compile("grand|net"), named) or pick(re.compile("total|amount|amt"), named) or (named[0] if named else None)
    date = next((c["name"] for c in t["columns"] if c.get("date")), None)
    if not money and not sold and date:
        rest = [c for c in numeric(t) if c["name"] not in prices + quantities + [due] and c.get("min", 0) >= 0]
        sums = [(conn.execute(f"SELECT SUM({q(c['name'])}) FROM {q(t['name'])}").fetchone()[0] or 0, c["name"]) for c in rest]
        money = max(sums)[1] if sums else (paid or None)
    cost = pick(COST, prices)
    sell = pick(SELL, [p for p in prices if p != cost]) or next((p for p in prices if p != cost), None)
    if paid and (paid == money or not money):
        money, paid = paid, None       # a list of payments: the amount paid is its money

    if not due and paid and money:     # unpaid money can be worked out when paid never exceeds the total
        low, of = conn.execute(f"SELECT SUM({q(paid)} <= {q(money)}), COUNT(*) FROM {q(t['name'])}").fetchone()
        if not of or low < MOSTLY * of:
            paid = None
    return {"date": date, "money": money, "total_of": [qty, price] if money and money == total else None,
            "quantities": sold, "stock": stock, "unit_prices": unit, "sell": sell, "cost": cost, "paid": paid, "due": due,
            "bill": find_bill(t), "label": find_label(t)}


def find_product(conn, t, names):
    """(total, pieces, price) when total = pieces x price on nearly every row."""
    if not 3 <= len(names) <= 14:
        return None, None, None
    best = None
    for total, a, b in permutations(names, 3):
        if a > b:
            continue   # a x b and b x a are the same
        hits, of, mean_a, mean_b = conn.execute(
            f"SELECT SUM(ABS({q(total)} - {q(a)} * {q(b)}) <= 0.5 + 0.1 * ABS({q(total)})), COUNT(*), AVG({q(a)}), AVG({q(b)}) "
            f"FROM (SELECT * FROM {q(t['name'])} LIMIT {SAMPLE_ROWS}) WHERE {q(a)} > 1 AND {q(b)} > 1 AND {q(total)} > 0").fetchone()
        if not of or of < 0.3 * min(t["rows"], SAMPLE_ROWS) or hits < MOSTLY * of:
            continue
        pieces, price = (a, b) if mean_a <= mean_b else (b, a)
        if (QUANTITY.search(price) and not QUANTITY.search(pieces)) or (PRICE.search(pieces) and not PRICE.search(price)):
            pieces, price = price, pieces
        score = (hits / of, bool(MONEY.search(total)), bool(QUANTITY.search(pieces)), bool(PRICE.search(price)))
        if best is None or score > best[0]:
            best = (score, total, pieces, price)
    return best[1:] if best else (None, None, None)


def per_piece(conn, t, price, quantity):
    """A price that stays the same for an item whatever the quantity is the price of one piece."""
    for c in t["columns"]:
        if (c["type"] != "TEXT" and not any(j[0] == c["name"] for j in t["joins"])) or c.get("date") or not c["filled"]:
            continue
        if c["distinct"] == t["rows"]:
            if c["type"] == "TEXT" and not BILL.search(c["name"]):
                return True      # one row per item: a price list
            continue
        steady, of = conn.execute(
            f"SELECT SUM(p <= 2), COUNT(*) FROM (SELECT COUNT(DISTINCT {q(price)}) AS p FROM {q(t['name'])} "
            f"GROUP BY {q(c['name'])} HAVING COUNT(DISTINCT {q(quantity)}) >= 3)").fetchone()
        if of and steady >= MOSTLY * of:
            return True
    return False


def find_bill(t):
    """The bill number column, when one bill has several rows."""
    links = {j[0] for j in t["joins"]}
    return next((c["name"] for c in t["columns"] if BILL.search(c["name"]) and not c.get("date") and not c["personal"]
                 and (ID_LIKE.search(c["name"]) or (c["type"] == "INTEGER" and not MONEY.search(c["name"]) and not QUANTITY.search(c["name"])))
                 and c["distinct"] < c["filled"] and c["name"] not in links), None)


def texts(t, with_links=False):
    links = set() if with_links else {j[0] for j in t["joins"]}
    return [c["name"] for c in t["columns"] if c["type"] == "TEXT" and not c["personal"] and not c.get("date")
            and c["distinct"] > 1 and not ID_LIKE.search(c["name"]) and c["name"] not in links]


def find_label(t):
    names = texts(t, with_links=True)
    return next((n for n in names if ITEM.search(n)), None) or next((n for n in names if NAMELIKE.search(n)), None)


# ------------------------------------------------------------------ the parts of a shop

class Parts:
    """The main table, the table with the sold items, and the stock list, picked by what they hold."""

    def __init__(self, tables, meaning):
        self.tables, self.meaning = {t["name"]: t for t in tables}, meaning
        self.main = max(tables, key=lambda t: (bool(meaning[t["name"]]["date"]),
                                               bool(meaning[t["name"]]["money"] or meaning[t["name"]]["quantities"]),
                                               bool(meaning[t["name"]]["money"]), t["rows"]))["name"]
        self.m = meaning[self.main]
        linked = [t["name"] for t in tables if meaning[t["name"]]["quantities"] and t["name"] != self.main
                  and any(j[1] == self.main for j in t["joins"])]
        self.items = self.main if self.m["quantities"] else (linked[0] if linked else None)
        self.stock = next((n for n, m in meaning.items() if m["stock"]), None)

    def parents(self, name):
        return [(j[0], j[1], j[2]) for j in self.tables[name]["joins"]]

    def sale(self):
        """(sum expression, FROM part, alias) for 'how much was sold' in the main table."""
        if self.m["money"]:
            return f"SUM({q(self.m['money'])})", q(self.main), ""
        return self.item_sale(self.main) if self.items == self.main else (None, None, "")

    def item_sale(self, name):
        m = self.meaning[name]
        if m["money"] and m["total_of"]:
            return f"SUM(a.{q(m['money'])})", f"{q(name)} a", "a."
        if not m["quantities"]:
            return None, None, ""
        qty = m["quantities"][0]
        own = next((p for p in m["unit_prices"] if p != m["cost"]), None)
        if own:
            return f"SUM(a.{q(qty)} * a.{q(own)})", f"{q(name)} a", "a."
        for mine, other, theirs in self.parents(name):
            if self.meaning[other]["sell"]:
                return (f"SUM(a.{q(qty)} * b.{q(self.meaning[other]['sell'])})",
                        f"{q(name)} a JOIN {q(other)} b ON b.{q(theirs)} = a.{q(mine)}", "a.")
        return None, None, ""

    def item_label(self, name):
        """(label expression, FROM part) for the name of the sold item, with the table as a."""
        for mine, other, theirs in self.parents(name):
            label = next((n for n in texts(self.tables[other]) if ITEM.search(n)), None) or self.meaning[other]["label"]
            if label and label != theirs and (self.meaning[other]["sell"] or ITEM.search(label)):
                return f"b.{q(label)}", f"{q(name)} a JOIN {q(other)} b ON b.{q(theirs)} = a.{q(mine)}"
        own = self.meaning[name]["label"]   # also covers a link by name, such as item = item
        return (f"a.{q(own)}", f"{q(name)} a") if own else (None, None)

    def profit(self):
        """(expression, FROM part). Profit is per piece, so it is multiplied by the pieces sold."""
        if not self.items:
            return None, None
        m = self.meaning[self.items]
        qty = m["quantities"][0]
        own = next((p for p in m["unit_prices"] if p != m["cost"]), None)
        if own and m["cost"] and m["cost"] in m["unit_prices"]:
            return f"SUM({q(qty)} * ({q(own)} - {q(m['cost'])}))", q(self.items)
        for mine, other, theirs in self.parents(self.items):
            p = self.meaning[other]
            if p["cost"] and (own or p["sell"]):
                sell = f"a.{q(own)}" if own else f"b.{q(p['sell'])}"
                return (f"SUM(a.{q(qty)} * ({sell} - b.{q(p['cost'])}))",
                        f"{q(self.items)} a JOIN {q(other)} b ON b.{q(theirs)} = a.{q(mine)}")
        return None, None

    def unpaid(self):
        """(expression without SUM, table) for money still to be paid."""
        for name in [self.main] + [n for n in self.meaning if n != self.main]:
            m = self.meaning[name]
            if m["due"]:
                return q(m["due"]), name
            if m["paid"] and m["money"]:
                return f"{q(m['money'])} - {q(m['paid'])}", name
        return None, None

    def who(self, name):
        """(label expression, FROM part) for the customer of a table."""
        own = next((n for n in texts(self.tables[name], with_links=True) if WHO.search(n)), None)
        if own:
            return f"a.{q(own)}", f"{q(name)} a"
        for mine, other, theirs in self.parents(name):
            label = next((n for n in texts(self.tables[other]) if WHO.search(n)), None)
            if label:
                return f"b.{q(label)}", f"{q(name)} a JOIN {q(other)} b ON b.{q(theirs)} = a.{q(mine)}"
        return None, None


# ------------------------------------------------------------------ worked examples and questions

def example_queries(conn, tables, meaning):
    """Worked examples on the shop's own columns. Small models copy a pattern better than they follow a rule.
    Each one is run first, so a wrong guess never reaches the prompt."""
    p, made = Parts(tables, meaning), []
    m = p.m
    value, source, alias = p.sale()
    if value:
        if m["date"]:
            date = alias + q(m["date"])
            month = next(c["max"] for c in p.tables[p.main]["columns"] if c["name"] == m["date"])[:7]
            made.append((f"total in {month_name(month)}?", f"SELECT {value} AS total FROM {source} WHERE {date} LIKE '{month}%'"))
            made.append(("which month had the highest total?", f"SELECT strftime('%m', {date}) AS month, {value} AS total FROM {source} "
                                                               "GROUP BY month ORDER BY total DESC LIMIT 1"))
        for column in ([m["label"]] if m["label"] else [])[:1]:
            made.append((f"which {plain(column)} has the highest total?",
                         f"SELECT {alias}{q(column)}, {value} AS total FROM {source} GROUP BY {alias}{q(column)} ORDER BY total DESC LIMIT 1"))
        if m["bill"] and not alias:
            bill = q(m["bill"])
            made += [(f"average value of one {plain(m['bill'])}?", f"SELECT AVG(t) AS average FROM (SELECT {value} AS t FROM {source} GROUP BY {bill})"),
                     (f"how many {plain(m['bill'])}s?", f"SELECT COUNT(DISTINCT {bill}) AS how_many FROM {source}"),
                     (f"biggest {plain(m['bill'])}?", f"SELECT {bill}, {value} AS total FROM {source} GROUP BY {bill} ORDER BY total DESC LIMIT 1")]
        if m["money"]:
            for mine, other, theirs in p.parents(p.main)[:2]:
                column = next((n for n in texts(p.tables[other]) if n != theirs), None)
                if column:
                    made.append((f"which {plain(column)} has the highest total?",
                                 f"SELECT b.{q(column)}, SUM(a.{q(m['money'])}) AS total FROM {q(p.main)} a JOIN {q(other)} b "
                                 f"ON b.{q(theirs)} = a.{q(mine)} GROUP BY b.{q(column)} ORDER BY total DESC LIMIT 1"))
    if p.items:
        label, joined = p.item_label(p.items)
        qty = q(meaning[p.items]["quantities"][0])
        if label:
            made.append(("which item sold the most pieces?", f"SELECT {label}, SUM(a.{qty}) AS pieces FROM {joined} GROUP BY {label} ORDER BY pieces DESC LIMIT 1"))
            item_value, item_source, _ = p.item_sale(p.items)
            if item_value and p.items != p.main:
                from_part = joined if " JOIN " in joined else item_source
                made.append(("which item sold the most in rupees?", f"SELECT {label}, {item_value} AS total FROM {from_part} GROUP BY {label} ORDER BY total DESC LIMIT 1"))
    profit, source = p.profit()
    if profit:
        made.append(("total profit?", f"SELECT {profit} AS profit FROM {source}"))
    unpaid, table = p.unpaid()
    if unpaid:
        made.append(("how much money is still unpaid?", f"SELECT SUM({unpaid}) AS unpaid FROM {q(table)}"))
        label, source = p.who(table)
        if label:
            expression = re.sub(r"([\w\"]+)", r"a.\1", unpaid) if '"' not in unpaid else unpaid
            made.append(("who owes the most?", f"SELECT {label}, SUM({expression}) AS unpaid FROM {source} GROUP BY {label} ORDER BY unpaid DESC LIMIT 1"))
    if p.stock:
        s = meaning[p.stock]
        price = s["sell"] or s["cost"]
        if price:
            made.append(("value of the stock?", f"SELECT SUM({q(s['stock'])} * {q(price)}) AS total FROM {q(p.stock)}"))
        if s["label"]:
            made.append(("which item has the lowest stock?", f"SELECT {q(s['label'])}, {q(s['stock'])} FROM {q(p.stock)} ORDER BY {q(s['stock'])} ASC LIMIT 1"))
    good = []
    for question, sql in made:
        try:
            if conn.execute(sql).fetchone() and sql not in [g[1] for g in good]:
                good.append([question, sql])
        except sqlite3.Error:
            pass
    return good


def example_questions(tables, meaning):
    """Questions for the start screen, in plain words, that this data can answer."""
    p, asked = Parts(tables, meaning), []
    value, _, _ = p.sale()
    thing = "sale" if value and (p.m["money"] or p.items) else None
    if thing and p.m["money"] and not MONEY.search(p.m["money"]):
        thing = plain(p.m["money"])
    if thing and p.m["date"]:
        last = next(c["max"] for c in p.tables[p.main]["columns"] if c["name"] == p.m["date"])
        asked.append(f"What was the total {thing} in {month_name(last[:7])}?")
    label = p.m["label"] or (p.item_label(p.items)[0] if p.items else None)
    if thing and label:
        asked.append(f"Which 3 {plain(label)}s have the highest {thing}?")
    if p.m["bill"]:
        asked.append(f"How many {plain(p.m['bill'])}s are there?")
    unpaid, table = p.unpaid()
    if unpaid:
        asked.append("Who owes me the most money?" if p.who(table)[0] else "How much money is still unpaid?")
    if p.profit()[0]:
        asked.append("What is the total profit?")
    if p.stock and meaning[p.stock]["label"]:
        asked.append(f"Which {plain(meaning[p.stock]['label'])} has the lowest stock?")
    if len(asked) < 2:
        asked += [f"How many rows are in {plain(t['name'])}?" for t in tables[:2]]
    return asked[:5]


def facts(tables, meaning):
    """A few numbers for the start screen."""
    p = Parts(tables, meaning)
    out = [{"label": "Rows" if len(tables) == 1 else plain(t["name"]).capitalize(), "value": f"{t['rows']:,}"}
           for t in sorted(tables, key=lambda t: -t["rows"])[:3]]
    if p.m["date"]:
        c = next(c for c in p.tables[p.main]["columns"] if c["name"] == p.m["date"])
        out.append({"label": "Period", "value": f"{month_name(c['min'][:7], short=True)} to {month_name(c['max'][:7], short=True)}"})
    return out


def column_note(m, name):
    """What a column means, in a few words, for the table list in the prompt. Empty when it is plain from the name."""
    if m["total_of"] and name == m["money"]:
        return f"already {m['total_of'][0]} x {m['total_of'][1]}, do not multiply again"
    if name == m["stock"]:
        return "pieces in stock"
    if name in m["unit_prices"]:
        return "cost of one piece" if name == m["cost"] else "price of one piece"
    if name in m["quantities"]:
        return "number of pieces"
    if name == m["due"]:
        return "money still unpaid"
    if name == m["paid"]:
        return "money paid so far"
    if name == m["bill"]:
        return "the same number is on several rows, count with COUNT(DISTINCT)"
    return ""


# ------------------------------------------------------------------ limits

def work_out_limits(conn, tables, meaning):
    """The largest total this shop's data can produce, and the largest count. The Monitor uses both."""
    one = lambda sql: abs(conn.execute(sql).fetchone()[0] or 0)
    biggest, negative = 0, False
    for t in tables:
        m = meaning[t["name"]]
        for c in numeric(t):
            biggest = max(biggest, one(f"SELECT SUM(ABS({q(c['name'])})) FROM {q(t['name'])}"))
            negative = negative or c.get("min", 0) < 0
        for c in t["columns"]:
            if c["type"] != "TEXT" and c["filled"]:
                biggest = max(biggest, abs(c["max"]), abs(c["min"]))
        for a in m["quantities"] + ([m["stock"]] if m["stock"] else []):
            for b in [p for p in (m["sell"], m["cost"], *m["unit_prices"]) if p]:
                biggest = max(biggest, one(f"SELECT SUM(ABS({q(a)} * {q(b)})) FROM {q(t['name'])}"))
            for mine, other, theirs in t["joins"]:
                for b in [p for p in (meaning[other]["sell"], meaning[other]["cost"]) if p]:
                    biggest = max(biggest, one(f"SELECT SUM(ABS(a.{q(a)} * b.{q(b)})) FROM {q(t['name'])} a "
                                               f"JOIN {q(other)} b ON a.{q(mine)} = b.{q(theirs)}"))
    names = lambda text: {c["name"] for t in tables for c in t["columns"] if (c["type"] == "TEXT") == text}
    return {"gross": biggest, "max_count": max(t["rows"] for t in tables), "negative_ok": negative,
            "columns": sorted(names(True) | names(False)),
            "labels": sorted(names(True) - names(False))}   # text columns can hold digits too, such as bar codes
