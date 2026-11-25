"""Reads a shopkeeper's own file and turns it into a clean SQLite database.

Supported: SQL dumps (MySQL, SQLite, SQL Server style), SQLite files, Excel (.xlsx, .xls),
CSV, and the HTML tables some billing software saves with an .xls name. The type is decided
by what is inside the file, not by its extension.

Two safety rules:
- An uploaded SQL file is never run. We only pick out CREATE TABLE, INSERT and foreign keys,
  and insert the values ourselves with parameters. Everything else in it is ignored.
- Nothing is changed silently. Every guess (dates, skipped total rows, blanks) becomes a note
  that the shopkeeper sees before they accept the data.
"""
import csv
import io
import re
import sqlite3
import tempfile
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time
from html.parser import HTMLParser
from pathlib import Path

MAX_FILES = 8
MAX_FILE_MB = 10
MAX_UNZIPPED_MB = 120   # an .xlsx is a zip, so a tiny file can unpack into a huge one
MAX_TABLES = 25
MAX_COLUMNS = 60
MAX_ROWS = 200_000

EMPTY_WORDS = {"", "-", "--", "n/a", "#n/a", "null"}
TOTAL_ROW = re.compile(r"(grand |sub ?|net )?totals?( amount)?\s*:?", re.IGNORECASE)
# These hold digits but are labels, so they stay text (a phone number is never added up).
LABEL_WORDS = {"phone", "mobile", "mob", "contact", "whatsapp", "gst", "gstin", "aadhaar", "aadhar", "pan",
               "pin", "pincode", "zip", "barcode", "hsn", "sac", "account", "batch", "imei", "sku"}
SQL_WORDS = set("""add all alter and as asc between by case cast check collate column commit constraint create cross
    default delete desc distinct drop else end except exists from full group having if in index inner insert intersect
    into is isnull join key left like limit natural not notnull null offset on or order outer primary references replace
    right rollback select set table then to transaction trigger union unique update using vacuum values view when where
    with attach detach pragma reindex truncate""".split())


class ImportProblem(ValueError):
    """Something the shopkeeper can act on. The message is shown to them as it is."""


@dataclass
class Table:
    name: str
    columns: list
    rows: list
    source: str = ""                             # which file or sheet it came from
    joins: list = field(default_factory=list)    # (column, other table, other column)
    original: list = field(default_factory=list)  # headings as they were in the file
    notes: list = field(default_factory=list)


# ------------------------------------------------------------------ which kind of file is it

def read_file(filename, data):
    """One uploaded file to a list of raw Tables."""
    if not data:
        raise ImportProblem(f"{filename} is empty.")
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise ImportProblem(f"{filename} is larger than {MAX_FILE_MB} MB.")
    stem = Path(filename).stem

    if data.startswith(b"SQLite format 3\x00"):
        return read_sqlite(filename, data)
    if data.startswith(b"PK\x03\x04"):
        return read_zip(filename, stem, data)
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        return read_old_excel(filename, stem, data)
    if data.startswith(b"%PDF"):
        raise ImportProblem(f"{filename} is a PDF. Please export the same report as Excel or CSV instead.")

    text = decode(filename, data)
    head = text.lstrip()[:4000].lower()
    if head.startswith("<") and "<table" in text.lower():
        return read_html(filename, stem, text)
    if filename.lower().endswith(".sql") or re.search(r"\b(create\s+table|insert\s+into)\b", head):
        return read_sql(filename, text)
    return read_csv(filename, stem, text)


def decode(filename, data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")   # SQL Server saves its scripts like this
    if b"\x00" in data[:4000]:
        raise ImportProblem(f"{filename} is not a file type QueryMend can read. Use Excel, CSV, SQL or SQLite.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


# ------------------------------------------------------------------ spreadsheets

def read_zip(filename, stem, data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            size = sum(i.file_size for i in z.infolist())
    except zipfile.BadZipFile:
        raise ImportProblem(f"{filename} is damaged and could not be opened.")
    if size > MAX_UNZIPPED_MB * 1024 * 1024:
        raise ImportProblem(f"{filename} unpacks to more than {MAX_UNZIPPED_MB} MB.")
    if "xl/workbook.xml" not in names:
        kind = "a Word document" if any(n.startswith("word/") for n in names) else "a zip file QueryMend cannot read yet"
        raise ImportProblem(f"{filename} is {kind}. Please export the data as Excel or CSV instead.")

    import openpyxl
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sheets = []
        for sheet in book.worksheets:
            if sheet.sheet_state != "visible":
                continue
            sheet.reset_dimensions()   # some exports store a wrong sheet size
            rows = []
            for row in sheet.iter_rows(values_only=True):
                rows.append(list(row))
                if len(rows) > MAX_ROWS:
                    raise ImportProblem(f"{filename} has more than {MAX_ROWS:,} rows.")
            sheets.append((sheet.title, rows))
    except ImportProblem:
        raise
    except Exception:
        raise ImportProblem(f"{filename} could not be opened as an Excel file. It may be damaged or password protected.")
    return sheets_to_tables(filename, stem, sheets)


def read_old_excel(filename, stem, data):
    import xlrd
    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception:
        raise ImportProblem(f"{filename} could not be opened as an old Excel file. It may be password protected. "
                            "Please save it as .xlsx or CSV.")
    sheets = []
    for sheet in book.sheets():
        if sheet.visibility:
            continue
        rows = []
        for r in range(sheet.nrows):
            row = []
            for c in range(sheet.ncols):
                cell = sheet.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    try:
                        row.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
                    except (xlrd.XLDateError, ValueError, OverflowError):
                        row.append(cell.value)
                elif cell.ctype in (xlrd.XL_CELL_ERROR, xlrd.XL_CELL_EMPTY):
                    row.append(None)
                else:
                    row.append(cell.value)
            rows.append(row)
        sheets.append((sheet.name, rows))
    return sheets_to_tables(filename, stem, sheets)


def sheets_to_tables(filename, stem, sheets):
    tables = []
    for title, rows in sheets:
        # a lone sheet with a default name says nothing, so the file name is used instead
        name = stem if len(sheets) == 1 or re.fullmatch(r"sheet\s*\d*", title, re.IGNORECASE) else title
        table = grid_to_table(name, rows, f"{filename}, sheet {title}")
        if table:
            tables.append(table)
    if not tables:
        raise ImportProblem(f"No table was found in {filename}.")
    return tables


def read_csv(filename, stem, text):
    try:
        dialect = csv.Sniffer().sniff(text[:8000], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = max(",;\t|", key=text[:8000].count)
    csv.field_size_limit(1_000_000)
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    except csv.Error as exc:
        raise ImportProblem(f"{filename} could not be read as a CSV file ({exc}).")
    table = grid_to_table(stem, rows, filename)
    if not table:
        raise ImportProblem(f"No table was found in {filename}. Use Excel, CSV, SQL or SQLite.")
    return [table]


class _HtmlTables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = ""

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(self.cell.strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.tables[-1].append(self.row)
            self.row = None


def read_html(filename, stem, text):
    parser = _HtmlTables()
    parser.feed(text)
    found = [grid_to_table(stem, rows, filename) for rows in parser.tables]
    found = [t for t in found if t]
    if not found:
        raise ImportProblem(f"No table was found in {filename}.")
    return found


def is_blank(value):
    return value is None or (isinstance(value, str) and value.strip().lower() in EMPTY_WORDS)


def looks_like_label(value):
    return isinstance(value, str) and parse_number(value) is None and parse_date(value) is None


def grid_to_table(name, grid, source):
    """Find the heading row in a sheet that may start with a shop name, dates and blank lines."""
    grid = [row for row in grid if any(not is_blank(v) for v in row)]
    if not grid:
        return None
    filled = [sum(not is_blank(v) for v in row) for row in grid]
    widest = max(filled[:60])
    notes = []

    start = None
    for i, row in enumerate(grid[:30]):
        if filled[i] >= max(1, 0.6 * widest) and all(looks_like_label(v) for v in row if not is_blank(v)):
            start = i
            break
    if start is None:
        start = next(i for i, n in enumerate(filled) if n >= max(1, 0.6 * widest))
        headings = [f"column {i + 1}" for i in range(max(len(r) for r in grid))]
        body = grid[start:]
        notes.append("No heading row was found, so the columns are numbered. Adding headings gives better answers.")
    else:
        headings, body = list(grid[start]), grid[start + 1:]
    if start:
        notes.append(f"Skipped {start} line{'s' if start > 1 else ''} above the headings.")
    if not body:
        return None

    width = max(len(headings), max(len(r) for r in body))
    headings += [None] * (width - len(headings))
    body = [list(r) + [None] * (width - len(r)) for r in body]
    keep = [i for i in range(width) if any(not is_blank(r[i]) for r in body)]
    headings = [headings[i] if not is_blank(headings[i]) else f"column {i + 1}" for i in keep]
    body = [[r[i] for i in keep] for r in body]

    body, dropped = drop_totals(body)
    if dropped:
        notes.append(f"Left out {dropped} total or footer row{'s' if dropped > 1 else ''}, so nothing is counted twice.")
    if not body:
        return None
    return Table(name, [str(h) for h in headings], body, source, notes=notes)


def drop_totals(body):
    """Report totals would double every sum, so they must not become data rows."""
    kept = [r for r in body if not any(isinstance(v, str) and TOTAL_ROW.fullmatch(v.strip()) for v in r)]
    while len(kept) > 2:
        last = kept[-1]
        values = [(i, v) for i, v in enumerate(last) if not is_blank(v)]
        lone_footer = len(last) >= 3 and len(values) == 1 and isinstance(values[0][1], str)
        if not (lone_footer or is_unlabelled_total(kept, values)):
            break
        kept.pop()
    return kept, len(body) - len(kept)


def is_unlabelled_total(rows, values):
    """A last row holding only numbers, each the sum of the column above it."""
    if not values or len(values) >= len(rows[0]):
        return False
    for i, v in values:
        total = as_number(v)
        above = [as_number(r[i]) for r in rows[:-1] if not is_blank(r[i])]
        if total is None or None in above or not above or abs(sum(above) - total) > max(0.05, abs(total) * 1e-6):
            return False
    return True


# ------------------------------------------------------------------ SQLite files

def read_sqlite(filename, data):
    with tempfile.TemporaryDirectory(dir=_temp_dir()) as folder:
        path = Path(folder) / "upload.db"
        path.write_bytes(data)
        try:
            conn = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
        except sqlite3.Error:
            raise ImportProblem(f"{filename} could not be opened as a SQLite database.")
        try:
            conn.execute("PRAGMA trusted_schema = OFF")
            conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
            tables, total = [], 0
            listed = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' "
                                  "AND name NOT LIKE 'sqlite_%'").fetchall()
            for name, sql in listed:
                if re.match(r"\s*create\s+virtual", sql or "", re.IGNORECASE):
                    continue
                quoted = '"' + name.replace('"', '""') + '"'
                cursor = conn.execute(f"SELECT * FROM {quoted} LIMIT {MAX_ROWS + 1}")
                columns = [c[0] for c in cursor.description]
                rows = [[None if isinstance(v, bytes) else v for v in row] for row in cursor]
                total += len(rows)
                if total > MAX_ROWS:
                    raise ImportProblem(f"{filename} has more than {MAX_ROWS:,} rows.")
                joins = [(r[3], r[2], r[4] or r[3]) for r in conn.execute(f"PRAGMA foreign_key_list({quoted})")]
                if rows:
                    tables.append(Table(name, columns, rows, filename, joins))
        except sqlite3.Error:
            raise ImportProblem(f"{filename} is damaged or locked with a password, so it could not be read.")
        finally:
            conn.close()
    if not tables:
        raise ImportProblem(f"{filename} has no tables with data.")
    return tables


def _temp_dir():
    from config import STATE_DIR
    folder = STATE_DIR / "tmp"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# ------------------------------------------------------------------ SQL dumps

def tokens_of(text):
    """Cut SQL text into (kind, value) pairs: str, name, num, word, and single characters."""
    mysql = "`" in text or "engine=" in text.lower()
    inside = r"(?:[^'\\]|''|\\.)*" if mysql else r"(?:[^']|'')*"
    pattern = re.compile(rf"""
        (?P<comment>--[^\n]*|/\*.*?\*/{"|^[#][^\n]*" if mysql else ""})
      | [Nn]?'(?P<str>{inside})'
      | `(?P<tick>[^`]*)` | "(?P<dq>[^"]*)" | \[(?P<bracket>[^\]\n]*)\]
      | (?P<hex>0x[0-9a-fA-F]+)
      | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+)
      | (?P<word>[^\W\d]\w*)
      | (?P<char>\S)""", re.VERBOSE | re.DOTALL | re.MULTILINE)
    escapes = {"n": "\n", "t": "\t", "r": "\r", "0": ""}
    for m in pattern.finditer(text):
        kind = m.lastgroup
        value = m.group(kind)
        if kind == "comment":
            continue
        if kind == "str":
            value = value.replace("''", "'")
            if mysql and "\\" in value:
                value = re.sub(r"\\(.)", lambda e: escapes.get(e.group(1), e.group(1)), value, flags=re.DOTALL)
        yield ("name" if kind in ("tick", "dq", "bracket") else kind), value


class _Reader:
    """Walks over the tokens of one statement."""

    def __init__(self, tokens):
        self.t, self.i = tokens, 0

    def peek(self, ahead=0):
        return self.t[self.i + ahead] if self.i + ahead < len(self.t) else ("end", "")

    def next(self):
        token = self.peek()
        self.i += 1
        return token

    def word(self, *options):
        kind, value = self.peek()
        if kind == "word" and value.lower() in options:
            self.i += 1
            return True
        return False

    def char(self, value):
        if self.peek() == ("char", value):
            self.i += 1
            return True
        return False

    def table_name(self):
        name = self.next()[1]
        while self.char("."):   # database.schema.table
            name = self.next()[1]
        return name

    def name_list(self):
        names = []
        if self.char("("):
            while self.peek()[0] != "end" and not self.char(")"):
                kind, value = self.next()
                if kind in ("name", "word"):
                    names.append(value)
        return names

    def group(self):
        """Items between brackets, each a list of tokens."""
        items, current, depth = [], [], 1
        while self.peek()[0] != "end":
            token = self.next()
            if token == ("char", "("):
                depth += 1
            elif token == ("char", ")"):
                depth -= 1
                if depth == 0:
                    break
            elif token == ("char", ",") and depth == 1:
                items.append(current)
                current = []
                continue
            current.append(token)
        return items + ([current] if current else [])


def literal(tokens):
    """(value, understood) for one value of an INSERT. Calculations are not run, so they are left empty."""
    if len(tokens) == 1:
        kind, value = tokens[0]
        if kind in ("str", "name"):
            return value, True
        if kind == "num":
            return (float(value) if any(c in value for c in ".eE") else int(value)), True
        if kind == "word" and value.lower() in ("null", "true", "false", "default"):
            return {"true": 1, "false": 0}.get(value.lower()), True
        return None, False
    if len(tokens) == 2 and tokens[0][1] in "+-" and tokens[1][0] == "num":
        value, _ = literal(tokens[1:])
        return (-value if tokens[0][1] == "-" else value), True
    if tokens and tokens[0][1].lower() in ("cast", "convert", "date", "timestamp"):   # CAST('2026-09-01' AS date)
        inner = [t for t in tokens if t[0] in ("str", "num")]
        if inner:
            return literal(inner[:1])
    return None, False


def read_sql(filename, text):
    if re.search(r"^copy\s.+from\s+stdin", text, re.IGNORECASE | re.MULTILINE):
        raise ImportProblem(f"{filename} is a PostgreSQL dump in COPY format. Please export it with INSERT statements.")
    tables, ignored, unread = {}, Counter(), 0

    def table(name):
        return tables.setdefault(name.lower(), Table(name, [], [], filename))

    def statement(tokens):
        nonlocal unread
        r = _Reader(tokens)
        while r.peek()[0] != "end":
            first = r.peek()[1].lower()
            if first == "create" and _starts_table(r):
                read_create(r)
                return
            if first == "insert" or first == "replace":
                if not read_insert(r):
                    ignored["INSERT ... SELECT"] += 1
                    return
                if r.peek()[0] == "end" or r.peek()[1].lower() not in ("insert", "create"):
                    return
                continue
            if first == "alter":
                read_alter(r)
                return
            if first not in ("set", "use", "lock", "unlock", "start", "begin", "commit", "drop", "pragma", "go", "if", "print"):
                ignored[first.upper() or "?"] += 1
            return

    def _starts_table(r):
        words = [t[1].lower() for t in r.t[r.i:r.i + 4]]
        return "table" in words

    def read_create(r):
        while not r.word("table"):
            r.next()
        if r.word("if"):
            r.word("not"); r.word("exists")
        target = table(r.table_name())
        if not r.char("("):
            ignored["CREATE TABLE AS"] += 1
            return
        for item in r.group():
            part = _Reader(item)
            kind, value = part.peek()
            if kind == "word" and value.lower() in ("primary", "key", "unique", "constraint", "foreign", "index",
                                                    "check", "fulltext", "spatial"):
                add_foreign_keys(part, target)
                continue
            target.columns.append(part.next()[1])
            while part.peek()[0] != "end":
                if part.word("references"):
                    other = part.table_name()
                    cols = part.name_list()
                    target.joins.append((target.columns[-1], other, cols[0] if cols else target.columns[-1]))
                else:
                    part.next()

    def add_foreign_keys(r, target):
        while r.peek()[0] != "end":
            if r.word("foreign"):
                r.word("key")
                if r.peek()[0] in ("name", "word"):   # MySQL allows an index name here
                    r.next()
                mine = r.name_list()
                if r.word("references"):
                    other = r.table_name()
                    theirs = r.name_list()
                    target.joins += [(a, other, b) for a, b in zip(mine, theirs or mine)]
            else:
                r.next()

    def read_alter(r):
        r.next()
        if r.word("table"):
            r.word("only"); r.word("if"); r.word("exists")
            name = r.table_name()
            if name.lower() in tables:
                add_foreign_keys(r, tables[name.lower()])

    def read_insert(r):
        nonlocal unread
        r.next()
        while r.word("ignore", "or", "replace", "abort", "rollback", "into", "low_priority", "delayed"):
            pass
        target = table(r.table_name())
        columns = r.name_list()
        if not r.word("values", "value"):
            return False
        while r.char("("):
            row = []
            for item in r.group():
                value, understood = literal(item)
                unread += not understood
                row.append(value)
            names = columns or target.columns or [f"column {i + 1}" for i in range(len(row))]
            for name in names:
                if name.lower() not in (c.lower() for c in target.columns):
                    target.columns.append(name)
            place = {c.lower(): i for i, c in enumerate(target.columns)}
            full = [None] * len(target.columns)
            for name, value in zip(names, row):
                full[place[name.lower()]] = value
            target.rows.append(full)
            if len(target.rows) > MAX_ROWS:
                raise ImportProblem(f"{filename} has more than {MAX_ROWS:,} rows.")
            r.char(",")
        return True

    current = []
    for token in tokens_of(text):
        if token == ("char", ";") or (token[0] == "word" and token[1] == "GO"):
            if current:
                statement(current)
            current = []
        else:
            current.append(token)
    if current:
        statement(current)

    found = [t for t in tables.values() if t.rows]
    if not found:
        raise ImportProblem(f"{filename} has no table with rows. An SQL file needs CREATE TABLE and INSERT statements "
                            "(in phpMyAdmin choose Export, then SQL, with data).")
    for t in found:
        t.rows = [row + [None] * (len(t.columns) - len(row)) for row in t.rows]
    if ignored["UPDATE"] or ignored["DELETE"]:
        found[0].notes.append("The SQL file also has UPDATE or DELETE commands. They were not applied, so some rows "
                              "may be out of date. A fresh export is safer.")
    if ignored:
        found[0].notes.append("Ignored in the SQL file: " + ", ".join(f"{n} {k}" for k, n in ignored.most_common(5))
                              + ". Only tables and their rows are read.")
    if unread:
        found[0].notes.append(f"{unread} value{'s were' if unread > 1 else ' was'} a calculation, so left empty.")
    return found


# ------------------------------------------------------------------ cleaning

def clean_name(text, used, fallback):
    """A heading like 'Invoice No.' or 'राशी' becomes a safe, unique SQL name."""
    out = "".join(ch if ch.isalnum() or unicodedata.category(ch).startswith("M") else "_" for ch in str(text).lower())
    out = re.sub(r"_+", "_", out).strip("_")[:48] or fallback
    if out[0].isdigit():
        out = f"{fallback}_{out}"
    if out in SQL_WORDS:
        out += "_info"
    base, n = out, 2
    while out in used:
        out, n = f"{base}_{n}", n + 1
    used.add(out)
    return out


def parse_number(text):
    """'₹ 1,25,000.50', 'Rs. 40/-', '(250)' or '12%' to a number. None if it is not one."""
    t = text.strip()
    t = re.sub(r"^(₹|rs\.?|inr)\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"(/-|%)$", "", t).strip()
    negative = t.startswith("(") and t.endswith(")")
    if negative:
        t = t[1:-1].strip()
    if not re.fullmatch(r"[+-]?(\d+|\d{1,3}(,\d{2,3})+)(\.\d+)?", t):
        return None
    t = t.replace(",", "")
    value = float(t) if "." in t else int(t)
    return -value if negative else value


def as_number(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value) if isinstance(value, float) and value.is_integer() and abs(value) < 1e15 else value
    return parse_number(value) if isinstance(value, str) else None


MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_TIME = r"(?:[T\s,]+\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\s*(?:[ap]\.?m\.?)?)?(?:Z|[+-]\d{2}:?\d{2})?"
YMD = re.compile(rf"(\d{{4}})[-/.](\d{{1,2}})[-/.](\d{{1,2}}){_TIME}", re.IGNORECASE)
DMY = re.compile(rf"(\d{{1,2}})[-/.](\d{{1,2}})[-/.](\d{{4}}|\d{{2}}){_TIME}", re.IGNORECASE)
DMONY = re.compile(rf"(\d{{1,2}})[-/.\s]*([A-Za-z]{{3,9}})[-/.,\s]*(\d{{4}}|\d{{2}}){_TIME}", re.IGNORECASE)


def parse_date(text, day_first=True):
    """Date text to 'YYYY-MM-DD'. Indian order, day first, unless told otherwise."""
    t = text.strip()
    if m := YMD.fullmatch(t):
        y, mo, d = map(int, m.groups())
    elif m := DMY.fullmatch(t):
        a, b, y = map(int, m.groups())
        d, mo = (a, b) if day_first else (b, a)
    elif m := DMONY.fullmatch(t):
        mo = MONTHS.get(m.group(2)[:3].lower())
        if not mo:
            return None
        d, y = int(m.group(1)), int(m.group(3))
    else:
        return None
    y += 2000 if y < 100 else 0
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def name_words(name):
    return set(re.split(r"[_\s]+", name.lower()))


def clean_column(name, values):
    """(cleaned values, SQL type, note or None)."""
    present = [v for v in values if not is_blank(v)]
    if not present:
        return [None] * len(values), "TEXT", None

    stamps = [isinstance(v, (datetime, date)) for v in present]
    if any(stamps):
        texts = [v for v in present if isinstance(v, str)]
        if all(isinstance(v, (datetime, date, str)) for v in present) and all(parse_date(v) for v in texts):
            note = (f"Dates in '{name}' are stored in two different ways in the file. Please check a few of them."
                    if texts else None)
            return [None if is_blank(v) else parse_date(v) if isinstance(v, str) else v.strftime("%Y-%m-%d")
                    for v in values], "TEXT", note

    if all(isinstance(v, str) for v in present) and any(parse_date(v) or parse_date(v, False) for v in present[:5]):
        for day_first in (True, False):
            if all(parse_date(v, day_first) for v in present):
                note = None
                if any(re.search(r"[T\s](?!00:00)\d{1,2}:\d{2}", v) for v in present):
                    note = f"Only the date part of '{name}' is kept, not the time."
                elif any(DMY.fullmatch(v.strip()) for v in present):
                    both = all(parse_date(v, not day_first) for v in present)
                    if not day_first:
                        note = f"Dates in '{name}' were read as month/day/year."
                    elif both:
                        note = f"Dates in '{name}' were read as day/month/year."
                return [None if is_blank(v) else parse_date(v, day_first) for v in values], "TEXT", note

    numbers = [as_number(v) for v in present]
    label = bool(name_words(name) & LABEL_WORDS)
    if None not in numbers and not label:
        padded = any(isinstance(v, str) and re.fullmatch(r"0\d+", v.strip()) for v in present)
        long_ids = all(isinstance(n, int) and abs(n) >= 10 ** 9 for n in numbers)
        too_big = any(isinstance(n, int) and abs(n) >= 2 ** 63 for n in numbers)   # SQLite cannot hold it
        if not (padded or long_ids or too_big):
            kind = "INTEGER" if all(isinstance(n, int) for n in numbers) else "REAL"
            return [None if is_blank(v) else as_number(v) for v in values], kind, None

    note = None
    bad = sum(n is None for n in numbers)
    if not label and 0 < bad <= 0.2 * len(present) and len(present) >= 5:
        example = next(str(v) for v, n in zip(present, numbers) if n is None)
        note = (f"'{name}' looks like numbers, but {bad} of {len(present)} values are not (for example '{example[:30]}'). "
                "It is kept as text, so it cannot be added up.")
    return [None if is_blank(v) else as_text(v) for v in values], "TEXT", note


def as_text(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, time):
        return value.strftime("%H:%M")
    return re.sub(r"\s+", " ", str(value)).strip()


def clean_tables(raw):
    """Safe names, real numbers and dates, and the links between tables."""
    if len(raw) > MAX_TABLES:
        raise ImportProblem(f"That is {len(raw)} tables. QueryMend can take {MAX_TABLES} at a time.")
    if sum(len(t.rows) for t in raw) > MAX_ROWS:
        raise ImportProblem(f"That is more than {MAX_ROWS:,} rows in total.")

    used, cleaned, renamed = set(), [], {}
    for t in raw:
        if len(t.columns) > MAX_COLUMNS:
            raise ImportProblem(f"'{t.name}' has {len(t.columns)} columns. The limit is {MAX_COLUMNS}.")
        name = clean_name(t.name, used, "table")
        taken = set()
        columns = [clean_name(c, taken, "column") for c in t.columns]
        notes, data, types = list(t.notes), [], []
        for i, column in enumerate(columns):
            values, kind, note = clean_column(str(t.columns[i]).strip(), [row[i] for row in t.rows])
            data.append(values)
            types.append(kind)
            if note:
                notes.append(note)
            blanks = sum(v is None for v in values)
            if blanks > 0.3 * len(values):
                notes.append(f"'{str(t.columns[i]).strip()}' is blank in {blanks} of {len(values)} rows.")
        rows = list(zip(*data))
        copies = len(rows) - len(set(rows))
        if copies:
            notes.append(f"{copies} row{'s are' if copies > 1 else ' is'} an exact copy of another row. They were kept.")
        table = Table(name, columns, rows, t.source, original=[str(c).strip() for c in t.columns], notes=notes)
        table.types = types
        table.raw_joins = t.joins
        renamed.setdefault(str(t.name).lower(), (name, {str(c).lower(): n for c, n in zip(t.columns, columns)}))
        cleaned.append(table)

    for table in cleaned:
        own = {str(o).lower(): c for o, c in zip(table.original, table.columns)}
        for mine, other, theirs in table.raw_joins:
            target, names = renamed.get(str(other).lower(), (None, {}))
            if target and str(mine).lower() in own and str(theirs).lower() in names:
                table.joins.append((own[str(mine).lower()], target, names[str(theirs).lower()]))
    guess_joins(cleaned)
    return cleaned


def guess_joins(tables):
    """Excel and CSV have no keys. A same-named column that is unique in one table and whose values
    nearly all appear there is treated as the link, and shown to the shopkeeper."""
    for child in tables:
        for parent in tables:
            if child is parent or any(j[1] == parent.name for j in child.joins):
                continue
            if any(j[1] == child.name for j in parent.joins):
                continue
            for column in set(child.columns) & set(parent.columns):
                if column in ("date", "remarks", "remark", "notes", "type", "status"):
                    continue
                there = [r[parent.columns.index(column)] for r in parent.rows]
                here = {r[child.columns.index(column)] for r in child.rows} - {None}
                keys = set(there)
                if len(keys) < len(there) or None in keys or len(keys) < 2 or not here:
                    continue
                if len(here & keys) >= 0.9 * len(here) and len(child.rows) >= len(parent.rows):
                    child.joins.append((column, parent.name, column))
                    child.notes.append(f"Linked to '{parent.name}' by '{column}'.")


# ------------------------------------------------------------------ writing the database

def import_files(files, path):
    """files: list of (filename, bytes). Writes a fresh SQLite file and returns the cleaned tables."""
    if not files:
        raise ImportProblem("Choose at least one file.")
    if len(files) > MAX_FILES:
        raise ImportProblem(f"Choose up to {MAX_FILES} files at a time.")
    raw = [table for filename, data in files for table in read_file(filename, data)]
    tables = clean_tables(raw)
    conn = sqlite3.connect(path)
    try:
        with conn:
            for t in tables:
                columns = [f'"{c}" {k}' for c, k in zip(t.columns, t.types)]
                columns += [f'FOREIGN KEY ("{a}") REFERENCES "{b}" ("{c}")' for a, b, c in t.joins]
                conn.execute(f'CREATE TABLE "{t.name}" ({", ".join(columns)})')
                marks = ", ".join("?" * len(t.columns))
                conn.executemany(f'INSERT INTO "{t.name}" VALUES ({marks})', t.rows)
    finally:
        conn.close()
    return tables
