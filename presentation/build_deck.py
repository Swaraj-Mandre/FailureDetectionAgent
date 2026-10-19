"""Build the jury deck as an editable PowerPoint file.

    python presentation/build_deck.py          writes presentation/QueryMend_slides.pptx
    python presentation/build_deck.py --pdf    also exports a PDF through PowerPoint

Needs python-pptx (pip install python-pptx), only for building the slides; the
agents do not use it. Fonts are Bahnschrift and Segoe UI, which ship with
Windows 10 and 11, so the deck looks the same on any college PC.

Diagrams come from report/diagrams/drawio/ (exported from draw.io). A missing
diagram leaves a dashed frame with its file name, so the deck always builds.
"""
import subprocess
import sys
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

REPO_DIR = Path(__file__).resolve().parent.parent
DIAGRAMS = REPO_DIR / "report" / "diagrams" / "drawio"
OUT = REPO_DIR / "presentation" / "QueryMend_slides.pptx"

# one colour per role, the same in the diagrams, the slides and the report
NAVY = "14213D"
INK = "14213D"
SLATE = "64748B"
MUTED = "CBD5E1"
LINE = "E2E8F0"
PAPER = "F8FAFC"
WHITE = "FFFFFF"
BLUE, BLUE_L = "2563EB", "DBEAFE"
AMBER, AMBER_L, AMBER_D = "D97706", "FEF3C7", "FBBF24"
GREEN, GREEN_L, GREEN_D = "059669", "D1FAE5", "34D399"
PURPLE, PURPLE_L = "7C3AED", "EDE9FE"
RED, RED_L, RED_D = "E11D48", "FFE4E6", "FB7185"

HEAD = "Bahnschrift SemiBold"
BODY = "Segoe UI"
BODY_BOLD = "Segoe UI Semibold"
MONO = "Consolas"

W, H = 13.333, 7.5
MARGIN = 0.7


def rgb(hex_colour):
    return RGBColor.from_string(hex_colour)


# ------------------------------------------------------------------ drawing helpers

def background(slide, colour):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = rgb(colour)


def box(slide, x, y, w, h, fill=None, line=None, line_width=1.0, rounded=True, dash=False):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
                                   Inches(x), Inches(y), Inches(w), Inches(h))
    if rounded:
        shape.adjustments[0] = min(0.12, 0.12 / max(h, 0.1))
    shape.shadow.inherit = False
    if fill:
        shape.fill.solid()
        shape.fill.fore_color.rgb = rgb(fill)
    else:
        shape.fill.background()
    if line:
        shape.line.color.rgb = rgb(line)
        shape.line.width = Pt(line_width)
        if dash:
            shape.line.dash_style = MSO_LINE_DASH_STYLE.DASH
    else:
        shape.line.fill.background()
    return shape


def text(slide, x, y, w, h, parts, size=18, font=BODY, colour=INK, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, spacing=1.1, spc=None):
    """parts: a string, or a list of lines; each line is a string or a list of
    (text, {overrides}) runs. Overrides: size, font, colour, strike."""
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = shape.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
    lines = parts if isinstance(parts, list) else [parts]
    for i, line in enumerate(lines):
        paragraph = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
        paragraph.alignment = align
        paragraph.line_spacing = spacing
        runs = line if isinstance(line, list) else [(line, {})]
        for run_text, style in runs:
            run = paragraph.add_run()
            run.text = run_text
            run.font.size = Pt(style.get("size", size))
            run.font.name = style.get("font", font)
            run.font.color.rgb = rgb(style.get("colour", colour))
            if style.get("strike"):
                run.font._rPr.set("strike", "sngStrike")
            if style.get("spc", spc):
                run.font._rPr.set("spc", str(style.get("spc", spc)))
    return shape


def brand(slide, dark=False):
    """Four short role-colour bars, top left of every slide."""
    for i, colour in enumerate((BLUE, AMBER, GREEN, PURPLE)):
        box(slide, MARGIN + i * 0.34, 0.42, 0.28, 0.07, fill=colour, rounded=False)


def footer(slide, number, dark=False):
    colour = MUTED if dark else SLATE
    text(slide, MARGIN, H - 0.45, 6, 0.3, "QueryMend  ·  Group 17  ·  AIA_05", size=11, colour=colour)
    text(slide, W - MARGIN - 1, H - 0.45, 1, 0.3, str(number), size=11, colour=colour, align=PP_ALIGN.RIGHT)


def kicker(slide, words, colour=BLUE, y=0.68):
    text(slide, MARGIN, y, 11, 0.35, words.upper(), size=13, font=BODY_BOLD, colour=colour, spc=150)


def headline(slide, words, colour=INK, y=1.02, size=34, w=11.9):
    text(slide, MARGIN, y, w, 1.0, words, size=size, font=HEAD, colour=colour, spacing=1.0)


def diagram(slide, name, x, y, w, h):
    """Place a draw.io export inside the frame, keeping its shape. Dashed frame if missing."""
    path = DIAGRAMS / name
    if not path.exists():
        box(slide, x, y, w, h, fill=PAPER, line=SLATE, dash=True)
        text(slide, x, y, w, h, [f"Diagram goes here", [(f"report/diagrams/drawio/{name}", {"font": MONO, "size": 13})]],
             size=16, colour=SLATE, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        return
    with Image.open(path) as image:
        ratio = image.width / image.height
    if w / h > ratio:
        draw_h, draw_w = h, h * ratio
    else:
        draw_w, draw_h = w, w / ratio
    slide.shapes.add_picture(str(path), Inches(x + (w - draw_w) / 2), Inches(y + (h - draw_h) / 2),
                             Inches(draw_w), Inches(draw_h))


def card(slide, x, y, w, h, accent, label, title, body, fill=WHITE, title_size=20, body_size=15):
    box(slide, x, y, w, h, fill=fill, line=LINE)
    box(slide, x, y, 0.09, h, fill=accent, rounded=False)
    text(slide, x + 0.32, y + 0.24, w - 0.5, 0.3, label.upper(), size=11, font=BODY_BOLD, colour=accent, spc=120)
    text(slide, x + 0.32, y + 0.55, w - 0.5, 0.8, title, size=title_size, font=BODY_BOLD, colour=INK)
    text(slide, x + 0.32, y + 0.55 + title_size / 72 * 1.5 * max(1, title.count("\n") + 1), w - 0.5, h - 1.0,
         body, size=body_size, colour="374151", spacing=1.15)


def stat(slide, x, y, w, h, value, caption, accent, fill, value_size=54):
    box(slide, x, y, w, h, fill=fill, line=None)
    text(slide, x + 0.3, y + 0.22, w - 0.6, value_size / 72 * 1.3, value, size=value_size, font=HEAD, colour=accent)
    text(slide, x + 0.3, y + 0.32 + value_size / 72 * 1.3, w - 0.6, h - 1.5, caption, size=15, colour=INK, spacing=1.15)


def notes(slide, words):
    slide.notes_slide.notes_text_frame.text = words


# ------------------------------------------------------------------ the slides

def slide_hook(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    background(s, NAVY)
    brand(s)
    kicker(s, "September revenue  ·  Sahyadri Stores", colour=AMBER_D, y=1.2)
    text(s, MARGIN - 0.05, 1.75, 12, 1.9, [[("Rs 24,03,00,20", {"strike": True})]], size=110, font=HEAD, colour=RED_D)
    text(s, MARGIN, 3.85, 11.5, 0.6, "Our AI agent said this, with full confidence.", size=30, font="Segoe UI Light", colour=WHITE)
    text(s, MARGIN, 4.5, 11.5, 0.7, [[("The real answer is ", {}), ("Rs 2,40,300", {"font": HEAD, "colour": GREEN_D, "size": 34})]],
         size=30, font="Segoe UI Light", colour=WHITE)
    text(s, MARGIN, 5.75, 11.5, 0.5, "No error. No warning. Just a wrong number, a hundred times too big.", size=20, colour=MUTED)
    footer(s, 1, dark=True)
    notes(s, "Pause for two seconds on the number. Ask the room: would you have caught that? "
             "The tool returned prices in paise instead of rupees. Nothing crashed, so a normal agent just reports it. "
             "QueryMend is about catching exactly this kind of failure.")


def slide_problem(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    background(s, WHITE)
    brand(s)
    kicker(s, "The problem")
    headline(s, "AI agents fail in three ways. Only one makes noise.")
    y, w, h, gap = 2.45, 3.75, 3.2, 0.33
    items = [
        (SLATE, "Loud", "The tool throws an error", "no such column: amount", "Easy to catch, easy to retry."),
        (RED, "Quiet", "The number is just wrong", "prices read in paise, a missing GROUP BY", "The query runs fine. Nobody notices."),
        (RED, "Spreading", "The next answer builds on it", "turn 2 reuses turn 1's number", "One mistake becomes many."),
    ]
    for i, (accent, label, title, example, verdict) in enumerate(items):
        x = MARGIN + i * (w + gap)
        box(s, x, y, w, h, fill=WHITE, line=LINE)
        box(s, x, y, w, 0.1, fill=accent, rounded=False)
        text(s, x + 0.35, y + 0.35, w - 0.7, 0.6, label, size=30, font=HEAD, colour=accent)
        text(s, x + 0.35, y + 1.0, w - 0.7, 0.7, title, size=18, font=BODY_BOLD, spacing=1.0)
        text(s, x + 0.35, y + 1.78, w - 0.7, 0.6, example, size=13, font=MONO, colour=SLATE)
        text(s, x + 0.35, y + 2.42, w - 0.7, 0.6, verdict, size=15, colour=accent if accent != SLATE else INK)
    text(s, MARGIN, 6.15, 11.9, 0.5, [[("PBA 1 ", {"font": HEAD, "colour": BLUE}), ("catches the loud ones.   ", {}),
                                      ("PBA 2 ", {"font": HEAD, "colour": GREEN}), ("catches all three.", {})]], size=22)
    footer(s, 2)
    notes(s, "Most demos only show loud failures, where the tool throws an error. "
             "The dangerous ones are quiet: the SQL runs, the number is wrong. "
             "And in a multi-turn chat, a wrong number gets reused, so the error spreads. PBA 2 targets all three.")


def slide_intro(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    background(s, NAVY)
    brand(s)
    text(s, MARGIN, 1.15, 11, 1.3, "QueryMend", size=80, font=HEAD, colour=WHITE)
    text(s, MARGIN, 2.55, 11.2, 1.2, "Ask your shop database a question in plain English. If the answer is wrong, "
                                      "it notices, fixes it, or tells you it can't.", size=24, font="Segoe UI Light", colour=MUTED, spacing=1.15)
    chips = [(BLUE, "Executor", "writes the SQL"), (AMBER, "Monitor", "checks everything"),
             (GREEN, "Recovery", "fixes or stops"), (PURPLE, "A person", "approves personal data")]
    x, w, gap = MARGIN, 2.8, 0.2
    for i, (colour, name, role) in enumerate(chips):
        cx = x + i * (w + gap)
        box(s, cx, 4.15, w, 1.0, fill=colour)
        text(s, cx + 0.25, 4.27, w - 0.4, 0.4, name, size=20, font=HEAD, colour=WHITE)
        text(s, cx + 0.25, 4.68, w - 0.4, 0.4, role, size=14, colour=WHITE)
    text(s, MARGIN, 5.6, 12, 0.4, "qwen2.5:3b  ·  LangGraph  ·  SQLite  ·  Chroma  ·  fully offline  ·  free",
         size=16, font=BODY_BOLD, colour=AMBER_D)
    text(s, MARGIN, 6.05, 12, 0.4, "Swaraj Mandre  ·  Saumya Joshi  ·  Siddhant Pawar      Topic 17: an agent that detects and recovers from its own tool failures",
         size=13, colour=MUTED)
    footer(s, 3, dark=True)
    notes(s, "One line: ask in plain English, and if the answer is wrong, it notices, fixes it, or tells you it can't. "
             "Three agents plus one human checkpoint. Everything runs on a laptop GPU with a 3B model, no paid API.")


def slide_pba1(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    background(s, WHITE)
    brand(s)
    kicker(s, "PBA 1  ·  Units 1 and 2")
    headline(s, "One agent that notices when its tool fails")
    diagram(s, "01_pba1_agent.png", MARGIN, 1.85, 5.6, 4.95)
    card(s, 6.75, 1.95, 5.88, 1.7, RED, "The slide version", "One generic message for every error",
         "Sent the same broken query 7 times. Told a user to run a DELETE by hand.", fill=RED_L)
    card(s, 6.75, 3.85, 5.88, 1.7, GREEN, "Our refinement", "Sort the error, then act on it",
         "A specific hint, at most 3 tries, and the tool is locked when retrying can't help.", fill=GREEN_L)
    text(s, 6.75, 5.85, 5.88, 0.8, [[("Blind spot: ", {"font": BODY_BOLD, "colour": RED}),
                                     ("wrong data that raises no error goes straight to the user.", {})]], size=16)
    footer(s, 4)
    notes(s, "route_after_tools checks every tool result for our Error prefix or an error status, and sends failures to handle_failure. "
             "The slide version treated every error the same, so the agent looped and even told a user to run DELETE. "
             "Our version classifies the error, caps retries at 3 and locks the tool for unsafe or unreachable cases. "
             "What it still cannot see: a wrong number with no error. That is why PBA 2 exists.")


def slide_team(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    background(s, WHITE)
    brand(s)
    kicker(s, "PBA 2  ·  Units 3 and 4")
    headline(s, "Three agents, one job each")
    diagram(s, "02_pba2_team.png", MARGIN, 1.75, 11.93, 3.6)
    roles = [(BLUE, "Executor", "Writes and runs the SQL. The only agent that touches the data."),
             (AMBER, "Monitor", "Plain rules, no LLM. Checks every result and every answer."),
             (GREEN, "Recovery", "Advice from our error guide, or a safe stop after 3 retries.")]
    w, gap = 3.84, 0.2
    for i, (colour, name, line) in enumerate(roles):
        x = MARGIN + i * (w + gap)
        box(s, x, 5.55, w, 1.25, fill=PAPER, line=None)
        box(s, x, 5.55, 0.09, 1.25, fill=colour, rounded=False)
        text(s, x + 0.3, 5.66, w - 0.45, 0.4, name, size=19, font=HEAD, colour=colour)
        text(s, x + 0.3, 6.04, w - 0.45, 0.7, line, size=14, spacing=1.1)
    footer(s, 5)
    notes(s, "The Executor is the only agent with database access. The Monitor has no model on purpose: "
             "a 3B model checking another 3B model shares its blind spots, so it uses rules from the shop's real limits. "
             "Recovery decides: retry with advice, or stop. The retry budget of 3 is the hop limit.")


def slide_real_run(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    background(s, WHITE)
    brand(s)
    kicker(s, "A real run")
    headline(s, "One question, two silent mistakes, both caught")
    diagram(s, "05_one_recovery.png", MARGIN, 1.8, 7.4, 5.0)
    steps = [(AMBER, AMBER_L, "1", "Ambiguous column", "The tool errors. Recovery pulls advice from the guide."),
             (RED, RED_L, "2", "Missing GROUP BY", "No error at all. The Monitor reads the SQL and quarantines it."),
             (GREEN, GREEN_L, "3", "Corrected query", "5 rows. The answer matches the data.")]
    for i, (colour, fill, n, title, line) in enumerate(steps):
        y = 1.95 + i * 1.5
        box(s, 8.45, y, 4.18, 1.3, fill=fill)
        text(s, 8.7, y + 0.18, 0.6, 0.9, n, size=40, font=HEAD, colour=colour)
        text(s, 9.35, y + 0.2, 3.15, 0.4, title, size=18, font=BODY_BOLD)
        text(s, 9.35, y + 0.58, 3.15, 0.7, line, size=13.5, spacing=1.1)
    text(s, 8.45, 6.5, 4.18, 0.3, "traces/ta2/failures.txt", size=12, font=MONO, colour=SLATE)
    footer(s, 6)
    notes(s, "This is a real run from our failure demo. The first mistake is loud: an ambiguous column. "
             "The second is quiet: SUM next to order_id with no GROUP BY. SQLite returns one row without any error. "
             "Our number checks could not see it, so the Monitor now reads the SQL itself. Third try is correct.")


def slide_memory(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    background(s, WHITE)
    brand(s)
    kicker(s, "Unit 3  ·  Memory and RAG")
    headline(s, "It remembers, it resumes, it reads its own guide")
    diagram(s, "03_memory_rag.png", MARGIN, 1.8, 6.5, 5.0)
    proofs = [(BLUE, "55 → 63", "\"And in September?\" works with a checkpointer, and fails without one."),
              (BLUE, "Restart", "\"Premium product = above Rs 1500\" is recalled in a brand-new process."),
              (GREEN, "k = 3", "A two-part question gets both parts. \"Connect to MySQL?\" gets \"I don't know\"."),
              (SLATE, "Logged", "Every failure, its SQL and what Recovery decided, in SQLite.")]
    for i, (colour, value, line) in enumerate(proofs):
        y = 1.95 + i * 1.18
        box(s, 7.55, y, 5.08, 1.02, fill=PAPER)
        text(s, 7.8, y + 0.2, 1.55, 0.6, value, size=22, font=HEAD, colour=colour)
        text(s, 9.35, y + 0.17, 3.15, 0.8, line, size=13.5, spacing=1.1)
    footer(s, 7)
    notes(s, "Short-term memory is a SQLite checkpointer per thread_id, so follow-ups work and a crashed run resumes. "
             "Long-term memory is a SQLite store with a glossary per user. "
             "RAG is Chroma over our own 15-section error guide; it answers only from the guide and says I don't know otherwise.")


def slide_containment(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    background(s, WHITE)
    brand(s)
    kicker(s, "Error propagation  ·  10 runs each")
    headline(s, "With the Monitor on, a bad number stops spreading")
    diagram(s, "04_containment.png", MARGIN, 1.85, 7.3, 4.95)
    stat(s, 8.3, 1.95, 4.33, 2.15, "10 / 10", "Monitor off: the wrong number was carried into the follow-up answer.", RED, RED_L, value_size=60)
    stat(s, 8.3, 4.3, 4.33, 2.15, "0 / 10", "Monitor on: quarantined before any agent could read it.", GREEN, GREEN_L, value_size=60)
    text(s, 8.3, 6.55, 4.33, 0.3, [[("Cost: ", {"font": BODY_BOLD, "colour": AMBER}), ("2 extra model calls, about 2 seconds.", {})]], size=14)
    footer(s, 8)
    notes(s, "One corrupted result in turn 1, then the follow-up: how does that compare with August? "
             "Monitor off: the bad value sits in the shared history, and 10 out of 10 follow-ups repeat it. "
             "Monitor on: it swaps the bad message for a quarantine note with the same message id, so no agent ever sees it. 0 out of 10.")


def slide_numbers(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    background(s, WHITE)
    brand(s)
    kicker(s, "Same 5 cases  ·  PBA 1 vs PBA 2  ·  3 runs each")
    headline(s, "Fewer wrong answers, more model calls. Both are true.")
    tiles = [("4 → 0", "wrong answers given to the user, out of 15 runs", GREEN, GREEN_L),
             ("0 → 3", "silent corruptions caught, out of 3", GREEN, GREEN_L),
             ("2 → 6", "model calls when the data is corrupt", AMBER, AMBER_L),
             ("+1", "model call even on a clean question", AMBER, AMBER_L)]
    w, gap = 2.83, 0.2
    for i, (value, caption, colour, fill) in enumerate(tiles):
        stat(s, MARGIN + i * (w + gap), 2.0, w, 2.5, value, caption, colour, fill, value_size=50)
    text(s, MARGIN, 4.68, 6, 0.3, "GOOD NEWS", size=11, font=BODY_BOLD, colour=GREEN, spc=120)
    text(s, MARGIN + 6.06, 4.68, 6, 0.3, "THE PRICE", size=11, font=BODY_BOLD, colour=AMBER, spc=120)
    box(s, MARGIN, 5.15, 11.93, 1.55, fill=NAVY)
    text(s, MARGIN + 0.4, 5.33, 11.2, 0.4, "FOUND BY OUR OWN TESTS", size=11, font=BODY_BOLD, colour=AMBER_D, spc=120)
    text(s, MARGIN + 0.4, 5.68, 11.2, 0.9, "A missing GROUP BY slipped past every number check. The Monitor now reads the SQL too. "
                                            "Clean question: 3/3 wrong became 3/3 correct.", size=17, colour=WHITE, spacing=1.15)
    footer(s, 9)
    notes(s, "Numbers, not opinions. PBA 1 gave a wrong number in 4 of 15 runs, PBA 2 in none. "
             "The price is real: up to three times the model calls when something needs fixing, and one extra call on the clean question. "
             "The evaluation also found a bug in our own Monitor, and we fixed the checks, not the test.")


def slide_demo(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    background(s, WHITE)
    brand(s)
    kicker(s, "Live demo")
    headline(s, "Four runs, about three minutes")
    steps = [(RED, "One corrupted result, Monitor off, then on", "python ta2/run.py containment"),
             (AMBER, "Five injected failure types and the evidence table", "python ta2/run.py failures"),
             (PURPLE, "Personal data paused for a person: approve, then reject", "python ta2/run.py privacy"),
             (BLUE, "Your question, or your edge case", "every run is also saved in traces/ as a backup")]
    for i, (colour, title, command) in enumerate(steps):
        y = 2.05 + i * 1.12
        circle = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(MARGIN), Inches(y), Inches(0.62), Inches(0.62))
        circle.shadow.inherit = False
        circle.fill.solid()
        circle.fill.fore_color.rgb = rgb(colour)
        circle.line.fill.background()
        text(s, MARGIN, y, 0.62, 0.62, str(i + 1), size=22, font=HEAD, colour=WHITE, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        text(s, MARGIN + 0.95, y - 0.02, 10.5, 0.4, title, size=21, font=BODY_BOLD)
        text(s, MARGIN + 0.95, y + 0.42, 10.5, 0.4, command, size=15, font=MONO if command.startswith("python") else BODY, colour=SLATE)
    footer(s, 10)
    notes(s, "Warm the model first (about 45 seconds cold). Run containment, then failures, then privacy. "
             "Then hand over to the jury for their own question or edge case. If anything misbehaves, open the saved trace.")


def slide_next(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    background(s, NAVY)
    brand(s)
    kicker(s, "What's next", colour=AMBER_D)
    headline(s, "From a class project to a tool a shop owner can trust", colour=WHITE)
    items = [(BLUE, "Owners set their own limits", "\"An order is never above Rs 50,000\""),
             (AMBER, "Real databases, read-only", "Postgres or MySQL through a read-only user, same checks"),
             (GREEN, "Show the work", "The answer, the SQL behind it, and a note whenever the Monitor stepped in")]
    for i, (colour, title, line) in enumerate(items):
        y = 2.25 + i * 1.05
        box(s, MARGIN, y + 0.08, 0.12, 0.72, fill=colour, rounded=False)
        text(s, MARGIN + 0.4, y, 11, 0.45, title, size=22, font=HEAD, colour=WHITE)
        text(s, MARGIN + 0.4, y + 0.45, 11, 0.4, line, size=15, colour=MUTED)
    text(s, MARGIN, 5.6, 11, 0.8, "Thank you. Questions?", size=40, font=HEAD, colour=WHITE)
    text(s, MARGIN, 6.4, 11, 0.4, "github.com/Swaraj-Mandre/FailureDetectionAgent", size=14, font=MONO, colour=AMBER_D)
    footer(s, 11, dark=True)
    notes(s, "Close on the product idea: owner-defined limits, real databases through a read-only user, "
             "and an interface that shows the SQL and every time the Monitor stepped in. Then invite questions.")


def export_pdf(pptx_path, pdf_path):
    """Ask PowerPoint itself for the PDF, so it matches what the jury sees."""
    script = (f"$app = New-Object -ComObject PowerPoint.Application; "
              f"$deck = $app.Presentations.Open('{pptx_path}', $true, $false, $false); "
              f"$deck.SaveAs('{pdf_path}', 32); $deck.Close(); $app.Quit()")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, timeout=180)


def main():
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(W), Inches(H)
    for build in (slide_hook, slide_problem, slide_intro, slide_pba1, slide_team, slide_real_run,
                  slide_memory, slide_containment, slide_numbers, slide_demo, slide_next):
        build(prs)
    prs.save(OUT)
    print(f"{OUT.relative_to(REPO_DIR)}  ({len(prs.slides)} slides)")
    missing = [n for n in ("01_pba1_agent.png", "02_pba2_team.png", "03_memory_rag.png", "04_containment.png",
                           "05_one_recovery.png") if not (DIAGRAMS / n).exists()]
    if missing:
        print("waiting for diagrams:", ", ".join(missing))
    if "--pdf" in sys.argv:
        export_pdf(OUT, OUT.with_suffix(".pdf"))
        print(f"{OUT.with_suffix('.pdf').relative_to(REPO_DIR)}")


if __name__ == "__main__":
    main()
