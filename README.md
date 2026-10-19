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

## Sample data

`data/shop.db` is a made-up retail shop (Sahyadri Stores): 40 customers, 25 products, 300 orders from April to September 2026. See [data/schema.md](data/schema.md).

## Team

| Name | Enrollment |
|---|---|
| Swaraj Mandre | ADT23SOCB1192 |
| Saumya Joshi | ADT23SOCB0997 |
| Siddhant Pawar | ADT23SOCB1117 |
