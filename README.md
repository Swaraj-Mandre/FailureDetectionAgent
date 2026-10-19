# QueryMend

**Ask your database a question in plain English. If the agent gets it wrong, it notices, fixes it, and tells you when it can't.**

Most talk-to-your-data tools fail quietly: a wrong join, a made-up column, a confident wrong number. QueryMend is built around catching those failures instead of hiding them. Everything runs offline on your own machine.

Agentic AI course project, MIT School of Computing, Pune. Group 17, AIA_05.

| Part | What it is | Folder |
|---|---|---|
| PBA 1 | One agent, one SQL tool, detects and recovers from its own tool failures | `ta1/` |
| PBA 2 | Executor, Monitor and Recovery agents with memory, RAG and a retry budget | `ta2/` |

## Requirements

- Python 3.12
- [Ollama](https://ollama.com) running, with two models pulled: `qwen2.5:3b` and `nomic-embed-text`

## Setup

```bash
pip install -r requirements.txt
python data/make_shop_db.py
```

## PBA 1: an agent that catches its own tool failures

One agent, one tool (`run_sql`), and a hand-built LangGraph loop that notices when the tool fails and decides what to do next.

```
START -> agent --(tool call?)--> tools --(route_after_tools)--> agent
           |                                  |
           '--(no tool call)--> END           '--> handle_failure --> agent
```

Run every test case (traces are saved to `traces/ta1/`):

```bash
python ta1/run_cases.py
```

Ask your own question:

```bash
python ta1/agent.py "Which city had the most delivered orders in August?"
```

| File | What it is |
|---|---|
| `ta1/sql_tool.py` | the tool: read-only, one SELECT at a time, clear error messages |
| `ta1/agent.py` | the refined agent |
| `ta1/agent_v1_slide.py` | the slide version, kept for comparison (`run_cases.py --v1`) |
| `ta1/REFINEMENT_NOTES.md` | what broke in the slide version and what we changed |
| `traces/ta1/annotated/` | two failure-and-recovery traces, explained step by step |

## PBA 2: memory, RAG and an agent team

```
START -> executor --(tool call)--> tools -> monitor_result --(ok)--------------> executor
            |                                     |--(personal data)--> privacy_gate -> executor
            |                                     '--(failure)--> recovery
            '--(final answer)--> monitor_answer --(ok)--> END
                                      '--(failure)--> recovery

recovery --(retries left)--> executor
         '--(not retryable, or budget used up)--> stop -> END
```

- **Executor** writes SQL and runs it. **Monitor** checks every result and every answer with plain rules, and quarantines impossible numbers and queries that are missing a GROUP BY. **Recovery** retries with advice from our error guide, or stops safely. Roles in [ta2/ROLES.md](ta2/ROLES.md).
- **Retry budget:** 3 retries per question, then the team stops and says what went wrong instead of guessing.

Seed the long-term memory once, then run every demo (each one saves its output to `traces/ta2/`):

```bash
python ta2/seed_store.py
python ta2/run.py all
```

| Demo | Shows |
|---|---|
| `amnesia` | the same follow-up question without and with a checkpointer |
| `restart` | a conversation that continues in a brand-new process |
| `glossary` | a business word saved in one thread and used in another, after a restart |
| `rag` | k=1 vs k=3 on our error guide, and an honest "I don't know" |
| `advice` | a real SQL failure fixed with advice retrieved from the guide, then logged |
| `resume` | a run that crashes mid-way and continues from its last checkpoint |
| `team` | a normal question through Executor and Monitor |
| `failures` | five injected failure types and the evidence table |
| `budget` | a busy database that clears inside the budget, and one that never does |
| `containment` | one corrupted result with the Monitor off, then on |
| `privacy` | personal data paused for a person: approve and reject |
| `faillog` | what the failure log recorded |

| File | What it is |
|---|---|
| `ta2/config.py` | models, file paths, retry budget |
| `ta2/memory.py` | SQLite checkpointer, SQLite store (glossary), failure log |
| `ta2/rag.py` | Chroma index over `ta2/corpus/error_guide.md` (15 sections) |
| `ta2/single_agent.py` | the PBA 1 agent with memory, guide advice and logging (Unit 3 milestone) |
| `ta2/agents.py` | Executor rules, Monitor checks, Recovery decisions, privacy masking |
| `ta2/graph.py` | the team graph and the retry budget |
| `ta2/evaluate.py` | the evaluation: TA1 vs TA2, failure injection, containment. Results and analysis in [ta2/EVALUATION.md](ta2/EVALUATION.md) |
| `traces/ta2/annotated/` | a routing trace explained step by step |

## Report and slides

- [report/report.pdf](report/report.pdf): the 4-page report (architecture, roles, checklist, TA1 vs TA2, error propagation, limits, contribution)
- [presentation/QueryMend_slides.pptx](presentation/QueryMend_slides.pptx): 11 slides for the jury, with speaker notes ([PDF copy](presentation/QueryMend_slides.pdf))
- [report/diagrams/mermaid/](report/diagrams/mermaid/): the diagrams as Mermaid code; draw.io exports go in `report/diagrams/drawio/`

`python report/build.py` prints the report HTML to PDF with headless Microsoft Edge. `python presentation/build_deck.py --pdf` builds the deck with python-pptx (`pip install python-pptx`, only needed for the slides) and exports the PDF through PowerPoint.

## Sample data

`data/shop.db` is a made-up retail shop (Sahyadri Stores): 40 customers, 25 products, 300 orders from April to September 2026. See [data/schema.md](data/schema.md).

## Team

| Name | Enrollment |
|---|---|
| Swaraj Mandre | ADT23SOCB1192 |
| Saumya Joshi | ADT23SOCB0997 |
| Siddhant Pawar | ADT23SOCB1117 |
