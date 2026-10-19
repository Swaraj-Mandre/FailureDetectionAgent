# QueryMend

**Ask your database a question in plain English. If the agent gets it wrong, it notices, fixes it, and tells you when it can't.**

Most talk-to-your-data tools fail quietly: a wrong join, a made-up column, a confident wrong number. QueryMend is built around catching those failures instead of hiding them. Everything runs offline on your own machine.

Agentic AI course project, MIT School of Computing, Pune. Group 17, AIA_05.

| Part | What it is | Folder |
|---|---|---|
| PBA 1 | One agent, one SQL tool, detects and recovers from its own tool failures | `ta1/` |
| PBA 2 | Executor, Monitor and Recovery agents with memory, RAG and a retry budget | `ta2/` |

> Work in progress. Setup and run commands are added as each part is finished.

## Requirements

- Python 3.12
- [Ollama](https://ollama.com) with two models pulled: `qwen2.5:3b` and `nomic-embed-text`

## Setup

```bash
pip install -r requirements.txt
python data/make_shop_db.py
```

## Sample data

`data/shop.db` is a made-up retail shop (Sahyadri Stores): 40 customers, 25 products, 300 orders from April to September 2026. See [data/schema.md](data/schema.md).

## Team

| Name | Enrollment |
|---|---|
| Swaraj Mandre | ADT23SOCB1192 |
| Saumya Joshi | ADT23SOCB0997 |
| Siddhant Pawar | ADT23SOCB1117 |
