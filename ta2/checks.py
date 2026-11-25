"""The Monitor's rules for uploaded shops. Plain Python, no model.

A wrong number from an unknown shop usually sits inside every limit, so checking the number
alone is not enough. These rules look at the query itself, next to what the shop's data is
like (which column repeats, which price is per piece, which total already includes the
quantity) and what was asked.

Two strengths:
    wrong   the query cannot give a right total (counts lines twice, adds up prices of one
            piece). The result is quarantined until the query is fixed or the team stops.
    doubt   the query does not seem to fit the question (a filter nobody asked for, a month
            that was left out). Wording can mislead a rule, so the Executor is asked about it
            once. If the same point comes back, the result is kept but marked for the person
            to verify.
"""
import difflib
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from meaning import COST, DATE_TEXT, ID_LIKE, MONTH_NAMES, Parts, plain, q

MAX_LISTED = 60      # a text column with more different values than this is not searched for in questions


@dataclass
class Finding:
    key: str         # the same point raised twice has the same key
    strength: str    # "wrong" or "doubt"
    detail: str      # for the log and the page
    advice: str      # for the Executor


# ------------------------------------------------------------------ what the data is like

class Profile:
    """Facts about one uploaded shop that the rules need. Worked out from its data."""

    def __init__(self, about, db):
        self.tables = {t["name"]: t for t in about["tables"]}
        self.columns = {(t["name"], c["name"]): c for t in about["tables"] for c in t["columns"]}
        self.meaning = about["meaning"]
        self.parts = Parts(about["tables"], self.meaning)
        pairs = lambda key: {(t, c) for t, m in self.meaning.items() for c in m[key]}
        self.quantities = pairs("quantities") | {(t, m["stock"]) for t, m in self.meaning.items() if m["stock"]}
        self.unit_prices = pairs("unit_prices")
        self.line_totals = {(t, m["money"]): m["total_of"] for t, m in self.meaning.items() if m["total_of"]}
        self.bills = {t: m["bill"] for t, m in self.meaning.items() if m["bill"]}
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            self.values = self.find_values(conn)
        finally:
            conn.close()
        words = {w for t, c in self.columns for w in re.split(r"[^\w]+|_", c.lower())} | {
            w for t in self.tables for w in t.lower().split("_")}
        self.own_words = words | {w + "s" for w in words}

    def unique(self, table, column):
        c = self.columns.get((table, column))
        return c is not None and c["distinct"] == c["filled"] == self.tables[table]["rows"]

    def find_values(self, conn):
        """Text values a question may name, such as a customer, an item or a payment type."""
        out = []
        for (t, name), c in self.columns.items():
            if c["type"] == "TEXT" and not c["personal"] and not c.get("date") and c["distinct"] <= MAX_LISTED and not ID_LIKE.search(name):
                for (v,) in conn.execute(f"SELECT DISTINCT {q(name)} FROM {q(t)} WHERE {q(name)} IS NOT NULL"):
                    out.append((t, name, str(v)))
        return out


# ------------------------------------------------------------------ reading a query

KEYWORDS = set("select from where group order by having limit join inner left right outer cross on using as and or not in is null "
               "like between distinct case when then else end asc desc union all with sum avg count total min max round cast real "
               "integer text strftime date coalesce ifnull abs substr length lower upper over partition offset exists".split())
NAME = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[^\W\d]\w*)'


def bare(name):
    return name.strip('"`[]').lower()


def without_text(sql):
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


class Query:
    """The parts of one SELECT that the rules look at. Not a full parser: where it is unsure it finds nothing."""

    def __init__(self, sql, profile):
        self.sql, self.code, self.profile = sql, without_text(sql), profile
        self.nested = len(re.findall(r"\bselect\b", self.code, re.I)) > 1
        known = {t.lower(): t for t in profile.tables}
        self.aliases = {}
        # the second name is an alias only when it is not a keyword, as in "FROM sales ORDER BY"
        not_keyword = rf"(?!(?:{'|'.join(KEYWORDS)})\b)"
        for m in re.finditer(rf"(?:\bfrom|\bjoin|,)\s+({NAME})(?:\s+(?:as\s+)?{not_keyword}({NAME}))?", self.code, re.I):
            table = known.get(bare(m[1]))
            if table:
                self.aliases[table.lower()] = table
                if m[2]:
                    self.aliases[bare(m[2])] = table
        self.tables = list(dict.fromkeys(self.aliases.values()))
        self.aggregates = list(self.find_aggregates())
        self.grouped = bool(re.search(r"\bgroup\s+by\b", self.code, re.I))
        self.limited = bool(re.search(r"\blimit\b", self.code, re.I))
        self.texts = [m.replace("''", "'") for m in re.findall(r"'((?:[^']|'')*)'", sql)]
        self.links = self.find_links()

    def find_aggregates(self):
        for m in re.finditer(r"\b(sum|avg|count|total|min|max)\s*\(", self.code, re.I):
            depth, i = 1, m.end()
            while i < len(self.code) and depth:
                depth += {"(": 1, ")": -1}.get(self.code[i], 0)
                i += 1
            inner = self.code[m.end():i - 1].strip()
            yield m[1].lower(), inner, inner.lower().startswith("distinct")

    def columns_in(self, text):
        """The known columns in a piece of the query, as (table, column)."""
        found = []
        for m in re.finditer(rf"(?:({NAME})\s*\.\s*)?({NAME})", text):
            alias, name = bare(m[1]) if m[1] else None, bare(m[2])
            for table in ([self.aliases[alias]] if alias in self.aliases else [] if alias else self.tables):
                column = next((c for t, c in self.profile.columns if t == table and c.lower() == name), None)
                if column:
                    found.append((table, column))
                    break
        return found

    def find_links(self):
        """Pairs of columns the query joins on."""
        out = []
        for a, b in re.findall(rf"({NAME}\s*\.\s*{NAME})\s*=\s*({NAME}\s*\.\s*{NAME})", self.code):
            x, y = self.columns_in(a), self.columns_in(b)
            if x and y and x[0][0] != y[0][0]:
                out.append((x[0], y[0]))
        for column in re.findall(rf"\busing\s*\(\s*({NAME})\s*\)", self.code, re.I):
            holders = [(t, c) for t, c in self.profile.columns if t in self.tables and c.lower() == bare(column)]
            if len(holders) == 2:
                out.append((holders[0], holders[1]))
        return out

    def select_list(self):
        m = re.search(r"\bselect\b(.*?)\bfrom\b", self.code, re.I | re.S)
        return m[1] if m else ""


# ------------------------------------------------------------------ rules on the query alone

def terms(expression):
    """The parts of an expression between + and -, outside brackets."""
    out, depth = [""], 0
    for ch in expression:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch in "+-" and depth == 0:
            out.append("")
        else:
            out[-1] += ch
    return out


def price_without_quantity(query, question):
    p = query.profile
    for kind, inner, _ in query.aggregates:
        if kind not in ("sum", "total"):
            continue
        price = None
        for term in terms(inner):
            cols = query.columns_in(term)
            if any(c in p.unit_prices for c in cols) and not any(c in p.quantities or c in p.line_totals for c in cols):
                price = price or next(c for c in cols if c in p.unit_prices)
        if not price:
            continue
        if any(c in p.quantities for c in query.columns_in(inner)):   # SUM(qty * price - cost)
            yield Finding(f"price:{price}", "wrong", f"{kind.upper()}({inner}) multiplies only a part by the quantity",
                          f"{q(price[1])} is the price of one piece and must be multiplied by the quantity too. "
                          f"Put brackets around the difference: SUM(quantity * (price - {q(price[1])})).")
            continue
        options = [c for c in p.quantities if c[0] in query.tables]
        options += [c for c in p.quantities if c[0] not in query.tables and any(j[1] == price[0] for j in p.tables[c[0]]["joins"])]
        stock = [c for c in options if c[1] == p.meaning[c[0]]["stock"]]
        sold = [c for c in options if c not in stock]
        pick = (stock or sold) if re.search(r"\bstock\b|in hand|inventory", question, re.I) else (sold or stock)
        if not pick:
            continue
        table, column = pick[0]   # the pieces the question is about
        extra = ""
        if table not in query.tables:
            link = next(j for j in p.tables[table]["joins"] if j[1] == price[0])
            extra = f" Join {q(table)} on {q(table)}.{q(link[0])} = {q(price[0])}.{q(link[2])}."
        total = next((t for (tt, t), (_, pr) in p.line_totals.items() if tt == price[0] and pr == price[1]), None)
        instead = f" or use SUM({q(total)}), which already includes it" if total else ""
        yield Finding(f"price:{price}", "wrong", f"{kind.upper()}({inner}) adds up the price of one piece without the quantity",
                      f"{q(price[1])} is the price of one piece. Multiply by the quantity: SUM({q(table)}.{q(column)} * ({inner})){instead}.{extra}")


def total_times_quantity(query, question):
    p = query.profile
    for kind, inner, _ in query.aggregates:
        cols = query.columns_in(inner)
        for c in cols:
            if c in p.line_totals and (c[0], p.line_totals[c][0]) in cols and "*" in inner:
                yield Finding(f"twice:{c}", "wrong", f"{q(c[1])} already includes the quantity and is multiplied by it again",
                              f"{q(c[1])} already includes the quantity. Use {kind.upper()}({q(c[1])}) without multiplying.")


def counted_twice(query, question):
    """Joins that repeat rows, so a total counts the same thing several times."""
    p = query.profile
    if query.nested:
        return
    used = re.findall(rf"(?:\bfrom|\bjoin)\s+({NAME})", query.code, re.I)
    if len(used) > len({bare(u) for u in used}) and query.aggregates:
        yield Finding("self", "wrong", f"{bare(used[0])} is joined to itself, which counts rows many times", "Use each table once. Remove the join to the same table.")
        return
    if len(query.tables) < 2:
        return
    if not query.links:
        yield Finding("cross", "wrong", f"{' and '.join(query.tables)} are used together without a link",
                      "Join the tables on the link listed in the schema, or use only the table the question needs.")
        return
    adding = [a for a in query.aggregates if a[0] in ("sum", "total", "count", "avg") and not a[2]]
    for a, b in query.links:
        if adding and not p.unique(*a) and not p.unique(*b):
            yield Finding(f"many:{a}{b}", "wrong", f"joining {q(a[0])} and {q(b[0])} on {q(a[1])} pairs every row with many rows",
                          f"{q(a[1])} repeats in both tables, so this join counts rows many times. Use only the table the question needs.")
            return
    one_side = {}
    for a, b in query.links:
        if p.unique(*a) and not p.unique(*b):
            one_side[a[0]] = (a[1], b[0])
        elif p.unique(*b) and not p.unique(*a):
            one_side[b[0]] = (b[1], a[0])
    for kind, inner, _ in adding:
        tables = {t for t, _ in query.columns_in(inner)}
        table = next(iter(tables)) if len(tables) == 1 else None
        if table not in one_side or inner == "*":
            continue
        key, many = one_side[table]
        if kind == "count":
            advice = f"Use COUNT(DISTINCT {q(table)}.{q(key)})."
        else:
            advice = (f"One row of {q(table)} is repeated for every matching row of {q(many)}. Leave {q(many)} out of the query if the "
                      f"question does not need it, or use the amount columns of {q(many)}.")
        yield Finding(f"repeat:{table}", "wrong", f"{kind.upper()}({inner}) counts rows of {q(table)} again for every row of {q(many)}", advice)


def lines_not_bills(query, question):
    """COUNT(bill number) counts lines when one bill has many rows."""
    p = query.profile
    if query.nested:
        return
    for kind, inner, distinct in query.aggregates:
        if kind != "count" or distinct:
            continue
        for t, c in query.columns_in(inner):
            if p.bills.get(t) == c and not query.grouped:
                yield Finding(f"bills:{t}", "wrong", f"COUNT({inner}) counts lines, because one {plain(c)} has several rows",
                              f"Use COUNT(DISTINCT {q(c)}).")
    table = query.tables[0] if len(query.tables) == 1 else None
    if table in p.bills and re.search(r"how many\s+(\w+\s+)?(bill|invoice|receipt|order|voucher)s?\b", question, re.I) \
            and any(k == "count" and i == "*" for k, i, _ in query.aggregates):
        yield Finding(f"bills:{table}", "doubt", "COUNT(*) counts lines, because one bill has several rows",
                      f"To count bills use COUNT(DISTINCT {q(p.bills[table])}).")


# ------------------------------------------------------------------ rules that compare with the question

MOST = re.compile(r"\b(most|highest|top|maximum|max|best|biggest|largest|costliest)\b", re.I)
LEAST = re.compile(r"\b(least|lowest|fewest|minimum|min|worst|smallest|cheapest)\b", re.I)
PICK_ONE = re.compile(r"\b(which|who|whose|whom|top|what (?:item|product|day|month|customer))\b", re.I)
EACH = re.compile(r"\b(each|every|per|by|wise|list|show|which|who|top|all the|names?|compare|between)\b", re.I)
DATE_WORDS = re.compile(r"\b(today|yesterday|day|days|week|weeks|month|months|monthly|year|years|yearly|daily|date|dates|when|since|until|"
                        r"before|after|last|this|recent|latest|quarter|season|festival|diwali|expir\w*|old|20\d\d|\d{1,2}[/-]\d{1,2})\b", re.I)
RUPEES = re.compile(r"\b(rupees|rs|amount|revenue|earn\w*|money|collection|income|turnover|value|business|billing)\b|₹", re.I)
PIECES = re.compile(r"\b(units?|pieces|pcs|quantity|qty|plates|bags|boxes|bottles|packets|cups|kg)\b", re.I)


def months_in(text):
    """Month numbers named in the text. 'may' only counts as a month when written May and used like one."""
    found = []
    for i, name in enumerate(MONTH_NAMES, 1):
        if name == "May":
            hit = re.search(r"\b(?:in|of|for|during|since|and|to|from|till|until|before|after)\s+May\b|\bMay\s+20\d\d", text)
        else:
            short = name[:4] + "t?" if name == "September" else name[:3]
            hit = re.search(rf"\b({name}|{short})\b", text, re.I) and not (name == "March" and re.search(r"\bmar\b", text) and not re.search(r"\bmarch\b", text, re.I))
        if hit:
            found.append(f"{i:02}")
    return found


def date_filters(query):
    return [t for t in query.texts if re.search(r"20\d\d-\d\d|^20\d\d[%-]?|^%?-\d\d-", t)]


def empty_result(query, question, columns, rows):
    if rows is not None and not (rows and all(cell == "None" for row in rows for cell in row)):
        return
    if not re.search(r"\bwhere\b|\bhaving\b", query.code, re.I):
        return
    values = []
    for t, c, v in query.profile.values:
        if t in query.tables and any(text.lower() != v.lower() and text.lower().strip("%") and
                                     (text.lower().strip("%") in v.lower() or v.lower() in text.lower()) for text in query.texts):
            values.append(f"{q(c)} has the value '{v}'")
    hint = f" {'; '.join(values[:3])}." if values else ""
    yield Finding("empty", "doubt", "the query matched no rows", "The filters matched no rows. Keep only the filters the question asks for, "
                  f"check the spelling of values and the dates against the table list, and run it again.{hint}")


# words a shopkeeper uses for a way of paying, and the values the data may hold for it
SAME_MEANING = [({"online", "digital", "upi", "gpay", "phonepe", "paytm", "google", "qr"}, {"upi", "gpay", "phonepe", "paytm", "google", "online", "qr", "neft", "imps", "bank", "card"}),
                ({"udhar", "udhari", "udhaar", "credit", "owe", "owes", "unpaid", "baki", "pending", "due"}, {"credit", "udhar", "udhari", "unpaid", "pending", "due", "baki"}),
                ({"cash", "rokh", "nakad"}, {"cash", "rokh", "nakad"})]


def filter_not_asked(query, question, context):
    said = context.lower()
    spoken = set(re.findall(r"[^\W_]+", said))
    for mine, theirs in SAME_MEANING:
        if spoken & mine:
            said += " " + " ".join(theirs)
    for text in query.texts:
        words = [w for w in re.findall(r"[^\W_]{2,}", text.lower()) if not w.isdigit()]
        if not words or DATE_TEXT.match(text) or date_filters_only(text):
            continue
        # 'Cash Sale' is not asked for just because the question has the word sale
        words = [w for w in words if w not in query.profile.own_words | {"sale", "sales", "shop", "store"}] or words
        # the first letters are enough, so 'Card' fits cards and 'Drinks' fits drink
        if not any(w[:max(3, len(w) - 2)] in said for w in words):
            yield Finding(f"asked:{text.lower()}", "doubt", f"the query filters on '{text}', which the question does not mention",
                          f"The question does not mention '{text}'. Remove that filter only if the question does not mean it in other words, and run the query again.")


def date_filters_only(text):
    return bool(re.fullmatch(r"[%\d\-/: ]*|%[YmdHMSjwW](?:[-/]?%[YmdHMSjwW])*|(?:start of|end of|now|[+-]\d+) ?\w*", text))


def dates_not_asked(query, question, context):
    dates = date_filters(query)
    if dates and not DATE_WORDS.search(context) and not months_in(context):
        yield Finding("dates", "doubt", f"the query filters on the date '{dates[0]}', but the question has no date",
                      "The question does not mention a date. Remove the date filter and run the query again.")


def month_left_out(query, question):
    if not any(c.get("date") for (t, _), c in query.profile.columns.items() if t in query.tables):
        return
    code = " ".join(query.texts)
    for m in months_in(question):
        if not re.search(rf"-{m}\b|-{m}-|-{m}%|^{m}$|\b{m}\b", code):
            yield Finding(f"month:{m}", "doubt", f"the question asks about {MONTH_NAMES[int(m) - 1]}, but the query has no filter for that month",
                          f"The question asks about {MONTH_NAMES[int(m) - 1]}. Add a filter on the date column with LIKE '20YY-{m}%', using the year of the question.")


def value_left_out(query, question):
    """A customer, item or type that exists in the data is named in the question but not in the query."""
    texts = " ".join(query.texts).lower()
    asked = question.lower()
    tops = {}
    for t, c, v in query.profile.values:
        low = v.lower().strip()
        if len(low) < 3 or low in query.profile.own_words or not re.search(rf"(?<!\w){re.escape(low)}(?:e?s)?(?!\w)", asked):
            continue
        tops.setdefault(low, (t, c, v))
    for low, (t, c, v) in tops.items():
        if any(low != other and low in other for other in tops):
            continue   # part of a longer value that is also named
        if not any(w in texts for w in re.findall(r"[^\W_]{3,}", low)):
            yield Finding(f"value:{low}", "doubt", f"the question names '{v}', but the query does not filter on it",
                          f"The question is about '{v}', which is a value of {q(t)}.{q(c)}. Add WHERE {q(c)} = '{v.replace(chr(39), chr(39) * 2)}' and run the query again.")


def one_row_not_total(query, question):
    """'Which customer bought the most' needs a total per customer, not the largest single row."""
    if query.nested or not PICK_ONE.search(question) or not (MOST.search(question) or LEAST.search(question)):
        return
    if any(k in ("sum", "total", "count", "avg") for k, _, _ in query.aggregates) or re.search(r"\bsingle\b|\bone (bill|sale|row)\b|at once", question, re.I):
        return
    extremes = [a for a in query.aggregates if a[0] in ("min", "max")]
    order = re.search(r"\border\s+by\s+(.*?)(?:\blimit\b|$)", query.code, re.I | re.S)
    if not extremes and not (order and query.limited):
        return
    labels = [(t, c) for t, c in query.columns_in(query.select_list())
              if query.profile.columns[t, c]["type"] == "TEXT" and not query.profile.columns[t, c]["personal"]]
    if not labels:
        return
    t, c = labels[0]
    column = query.profile.columns[t, c]
    if column["distinct"] >= column["filled"] and len(query.tables) < 2:
        return
    measure = extremes[0][1] if extremes else re.sub(r"\b(asc|desc)\b.*", "", order[1], flags=re.I | re.S).strip()
    if not query.columns_in(measure) or all(query.profile.columns[x]["type"] == "TEXT" for x in query.columns_in(measure)):
        return
    way = "ASC" if LEAST.search(question) and not MOST.search(question) else "DESC"
    yield Finding(f"total:{c}", "doubt", f"the query looks at single rows, but {plain(c)} has many rows",
                  f"Add up the rows of each {plain(c)}: SELECT {q(c)}, SUM({measure}) AS total ... GROUP BY {q(c)} ORDER BY total {way} LIMIT 1.")


def wrong_direction(query, question):
    order = re.search(r"\border\s+by\b(?!.*\border\s+by\b)(.*?)\blimit\b", query.code, re.I | re.S)
    if not order or bool(MOST.search(question)) == bool(LEAST.search(question)):
        return
    down = bool(re.search(r"\bdesc\b", order[1], re.I))
    if down and LEAST.search(question):
        yield Finding("direction", "doubt", "the question asks for the lowest, but the query sorts from the highest", "Sort with ASC instead of DESC.")
    if not down and MOST.search(question):
        yield Finding("direction", "doubt", "the question asks for the highest, but the query sorts from the lowest", "Sort with DESC.")


def too_many_totals(query, question, columns, rows):
    one = r"\s*(what (?:is|was|were|are) (?:my |the |our )?(?:total|average|(?:\w+ )?(?:value|profit|sale|sales|revenue|income|amount))|how (?:much|many)|total\b)"
    if rows and len(rows) > 1 and re.match(one, question, re.I) and not EACH.search(question) \
            and (query.aggregates or re.search(r"\bvalue|profit|total|average\b", question, re.I)):
        yield Finding("onetotal", "doubt", f"the question asks for one number, but the query returned {len(rows)} rows",
                      "The question asks for one number. Remove GROUP BY, use SUM(...) and give one total.")


def average_of_lines(query, question):
    p = query.profile
    if query.nested or not re.search(r"\b(average|avg|mean)\b", question, re.I) \
            or not re.search(r"\b(bill|invoice|order|receipt|voucher|basket)s?\b", question, re.I):
        return
    for kind, inner, _ in query.aggregates:
        if kind != "avg":
            continue
        for t, c in query.columns_in(inner):
            if t in p.bills:
                yield Finding(f"average:{t}", "doubt", f"AVG({inner}) is the average of the lines, and one bill has several lines",
                              f"Add up each bill first: SELECT AVG(t) AS average FROM (SELECT SUM({inner}) AS t FROM {q(t)} GROUP BY {q(p.bills[t])}).")
                return


def wrong_measure(query, question):
    """Pieces asked and rupees added up, or the other way round, or rows counted instead of pieces."""
    p = query.profile
    sums = [query.columns_in(i) for k, i, _ in query.aggregates if k in ("sum", "total")]
    pieces_only = sums and all(cols and all(c in p.quantities for c in cols) for cols in sums)
    table = query.tables[0] if query.tables else None
    amount = next((p.meaning[t]["money"] for t in query.tables if p.meaning[t]["money"]), None)
    qty = next((c for t in query.tables for c in p.meaning[t]["quantities"]), None)
    if RUPEES.search(question) and not PIECES.search(question) and pieces_only and amount:
        yield Finding("rupees", "doubt", "the question asks about money, but the query adds up pieces", f"Use SUM({q(amount)}) instead.")
    sold = re.search(r"how many\b.*\b(sold|sell|bought|purchased)\b", question, re.I)
    if RUPEES.search(question) and not PIECES.search(question) and not re.search(r"how many", question, re.I) and not sums and amount \
            and any(k == "count" for k, _, _ in query.aggregates):
        yield Finding("rupees", "doubt", "the question asks about money, but the query counts rows", f"Use SUM({q(amount)}) instead of COUNT.")
    ranked = PICK_ONE.search(question) and re.search(r"\b(bought|buys?|sold|sells?|spends?|spent)\b", question, re.I)
    if ranked and not sums and any(k == "count" and not d for k, _, d in query.aggregates) and (amount or qty) \
            and not re.search(r"\b(often|times|visits?|bills?|orders?|invoices?|how many)\b", question, re.I):
        wanted = qty if qty and (PIECES.search(question) or not amount) else amount
        yield Finding("ranked", "doubt", "the query counts rows instead of adding up what was sold", f"Use SUM({q(wanted)}) instead of COUNT.")
    if qty and sold \
            and not re.search(r"how many\s+(\w+\s+)?(bill|invoice|order|sale|customer|time|day|item|product|kind|type)s?\b", question, re.I) \
            and any(k == "count" and not d for k, _, d in query.aggregates) and not sums:
        yield Finding("pieces", "doubt", "the question asks how many pieces, but the query counts rows", f"Use SUM({q(qty)}) instead of COUNT.")
    if re.match(r"\s*how many\b", question, re.I) and not RUPEES.search(question) and not qty and len(sums) == 1 \
            and any(p.meaning[t]["money"] == c for t, c in sums[0]):
        yield Finding("howmany", "doubt", "the question asks how many, but the query adds up money", "Count the rows with COUNT(*) instead of adding up money.")
    if (PIECES.search(question) or sold) and not RUPEES.search(question) and sums and qty and table and not pieces_only \
            and not any(c in p.quantities for cols in sums for c in cols):
        yield Finding("quantity", "doubt", "the question asks about pieces, but the query adds up money", f"Use SUM({q(qty)}) instead.")


OWED = re.compile(r"\b(owes?|owing|owed|pending|dues?|unpaid|outstanding|remaining to|udhar|udhari|udhaar|baki|bakaya|"
                  r"(?:still |yet )?(?:have|has) to (?:pay|give)|(?:still |yet )?to be (?:paid|received|collected)|yet to pay|balance)\b", re.I)
PAID_SO_FAR = re.compile(r"\b(paid|jama)\b(?!\s+(?:by|through|via|in|with|using))", re.I)


def owed_column(query, question):
    """Money still to be paid is asked, the data has it, and the query does not use it."""
    p = query.profile
    used = query.columns_in(query.code)
    if OWED.search(question):
        expression, table = p.parts.unpaid()
        if not expression:
            return
        m = p.meaning[table]
        needed = [(table, m["due"])] if m["due"] else [(table, m["money"]), (table, m["paid"])]
        if not all(c in used for c in needed):
            yield Finding("owed", "doubt", "the question is about unpaid money, but the query does not work it out",
                          f"Money still unpaid is SUM({expression}) from {q(table)}. Use that.")
    elif PAID_SO_FAR.search(question):
        for t in query.tables:
            m = p.meaning[t]
            if m["paid"] and (t, m["paid"]) not in used:
                yield Finding("paid", "doubt", f"the question is about money paid, but the query does not use {q(m['paid'])}",
                              f"The money paid is in {q(t)}.{q(m['paid'])}. Use SUM({q(m['paid'])}).")
                return


def unpaid_not_asked(query, question):
    """The unpaid or paid column is added up, but the question is about sales."""
    if OWED.search(question) or re.search(r"\b(paid|pay|pays|payment|received|receive|collected|jama|advance|credit)\b", question, re.I):
        return
    for kind, inner, _ in query.aggregates:
        cols = query.columns_in(inner)
        for t, c in cols:
            m = query.profile.meaning[t]
            if kind == "sum" and c in (m["due"], m["paid"]) and m["money"] and (t, m["money"]) not in cols and c != m["money"] \
                    and not set(c.split("_")) & set(re.findall(r"\w+", question.lower())):
                yield Finding(f"not-owed:{c}", "doubt", f"{q(c)} is only the {'unpaid' if c == m['due'] else 'paid'} part, and the question is about the whole amount",
                              f"Use SUM({q(m['money'])}) instead of SUM({q(c)}).")


def profit_without_cost(query, question):
    if not re.search(r"\b(profit|margin)\b", question, re.I):
        return
    expression, source = query.profile.parts.profit()
    if expression and not any(COST.search(c) for _, c in query.columns_in(query.code)):
        yield Finding("profit", "doubt", "the question is about profit, but the query does not use the cost",
                      f"Profit is the sale minus the cost: SELECT {expression} AS profit FROM {source}.")


def stock_left(query, question):
    """What is left in the shop is in the stock column, not in the sales."""
    p = query.profile
    if not p.parts.stock or not re.search(r"\b(in stock|stock|left|in hand|inventory)\b", question, re.I):
        return
    column = p.meaning[p.parts.stock]["stock"]
    if (p.parts.stock, column) not in query.columns_in(query.code):
        yield Finding("stock", "doubt", f"the question is about the stock, but the query does not use {q(column)}",
                      f"The pieces in stock are in {q(p.parts.stock)}.{q(column)}. Use that table.")


def stock_at_cost(query, question):
    """The value of the stock at cost, worked out with the selling price."""
    p = query.profile
    m = p.meaning[p.parts.stock] if p.parts.stock else None
    if m and m["cost"] and re.search(r"\bstock\b|in hand|inventory", question, re.I) and re.search(r"\bcost|purchase (price|rate|value)|buying\b", question, re.I) \
            and (p.parts.stock, m["cost"]) not in query.columns_in(query.code):
        yield Finding("atcost", "doubt", "the question asks for the cost, but the query uses another price",
                      f"Use the cost: SUM({q(m['stock'])} * {q(m['cost'])}) from {q(p.parts.stock)}.")


def month_not_day(query, question):
    if not re.search(r"\b(which|what|each|every|per|by) month|month[- ]?wise|monthly\b", question, re.I) or not query.grouped \
            or re.search(r"strftime|substr", query.code, re.I):
        return
    grouped = re.search(r"\bgroup\s+by\s+(.*?)(?:\border\b|\bhaving\b|\blimit\b|$)", query.code, re.I | re.S)[1]
    dates = [c for t, c in query.columns_in(grouped) if query.profile.columns[t, c].get("date")]
    if dates:
        yield Finding("month", "doubt", "the question asks about months, but the query groups by day",
                      f"Group by month: SELECT strftime('%m', {q(dates[0])}) AS month, SUM(...) AS total ... GROUP BY month.")


def biggest_line_not_bill(query, question):
    p = query.profile
    if query.nested or not (MOST.search(question) or LEAST.search(question)) or not re.search(r"\b(bill|invoice|receipt|order|visit)\b", question, re.I):
        return
    if any(k in ("sum", "total", "count", "avg") for k, _, _ in query.aggregates):
        return
    for t in query.tables:
        if t in p.bills and p.meaning[t]["money"]:
            bill, way = q(p.bills[t]), "ASC" if LEAST.search(question) and not MOST.search(question) else "DESC"
            yield Finding(f"bill:{t}", "doubt", "the query looks at single lines, and one bill has several lines",
                          f"Add up each bill: SELECT {bill}, SUM({q(p.meaning[t]['money'])}) AS total FROM {q(t)} GROUP BY {bill} ORDER BY total {way} LIMIT 1.")
            return


def closest_column(profile, error):
    """For 'no such column: bills_amount', the columns with the closest names and their tables."""
    m = re.search(r"no such column: (?:\w+\.)?([^\s.]+)", error)
    if not m:
        return ""
    names = {c for _, c in profile.columns}
    near = difflib.get_close_matches(m[1].lower(), names, n=2, cutoff=0.6)
    if not near:
        return ""
    where = "; ".join(f"{q(c)} in {', '.join(q(t) for t, x in profile.columns if x == c)}" for c in near)
    return f"There is no column {m[1]}. The closest names are: {where}."


def numbers_not_in_rows(answer, rows, question, sql):
    """Numbers the answer gives that are in no row, not in the question and not in the query. Dates and small list numbers are left out."""
    number = r"(?<!\w)(?<!\d\.)-?\d[\d,]*(?:\.\d+)?"
    as_floats = lambda text: [float(n.replace(",", "")) for n in re.findall(number, text)]
    given = as_floats(" ".join(str(c) for r in rows for c in r))
    given += as_floats(re.sub(r"[-/:]", " ", " ".join(str(c) for r in rows for c in r) + " " + question + " " + sql))
    given.append(float(len(rows)))
    text = re.sub(r"(?m)^\s*\d+[.)]\s", " ", answer)                  # 1. 2. 3.
    text = re.sub(r"\b\d{1,4}[-/]\d{1,2}(?:[-/]\d{1,4})?\b|\b\d{1,2}(?:st|nd|rd|th)\b", " ", text)
    return [n for n in as_floats(text) if not any(abs(abs(n) - abs(g)) <= max(0.5, abs(g) * 0.001) for g in given)]


# ------------------------------------------------------------------ all of them

def removed_filters(raised, sql):
    """Filters the Monitor asked about that are gone now. The question may have meant them in other words."""
    texts = " ".join(re.findall(r"'((?:[^']|'')*)'", sql)).lower()
    return [k.split(":", 1)[1] for k in raised if k.startswith("asked:") and k.split(":", 1)[1] not in texts]


def find(profile, sql, question, context, columns=(), rows=None):
    """Every finding for one query. context is everything the person said in this conversation."""
    query = Query(sql, profile)
    if not query.tables:
        return []
    found = []
    for rule in (total_times_quantity, price_without_quantity, counted_twice, lines_not_bills):
        found += rule(query, question)
    for rule in (average_of_lines, one_row_not_total, wrong_direction, wrong_measure, owed_column, unpaid_not_asked, profit_without_cost, stock_left, stock_at_cost,
                 month_not_day,
                 biggest_line_not_bill, value_left_out, month_left_out):
        found += rule(query, question)
    found += filter_not_asked(query, question, context)
    found += dates_not_asked(query, question, context)
    found += empty_result(query, question, columns, rows)
    found += too_many_totals(query, question, columns, rows)
    by_key = defaultdict(list)
    for f in found:
        by_key[f.key].append(f)
    return [group[0] for group in by_key.values()]
