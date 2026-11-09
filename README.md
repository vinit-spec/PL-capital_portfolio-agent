# PL Capital — Portfolio Analytics Agent

An AI agent that answers natural language questions about portfolio data using **Gemini function-calling**, **SQLite**, and Python.

---

## Architecture

```
User question
      │
      ▼
PortfolioAgent
  │  Gemini (manual function-calling, turn 1)
  │  decides which tool to invoke
  ├──► sql_query            → SQLQueryTool
  │       Gemini generates SQL → validate → execute (query_only)
  │       one self-repair retry on SQL error
  │
  └──► exposure_calculator  → ExposureCalculatorTool
           fuzzy portfolio match → equity-only sector %
      │
  Gemini (turn 2) — formats final natural-language answer
```

**Key design choices:**
- Automatic Gemini function-calling is **disabled**; dispatch is manual so we can log the tool, validate before executing, and score routing in the evaluator.
- SQL tool uses `PRAGMA query_only = ON` — write operations are impossible even if validation misses something.
- Schema + all categorical values are injected into the SQL generation prompt so the model never guesses spellings.
- One self-repair retry: on SQL error the error message is fed back to the model for a corrected query.
- Exposure tool resolves portfolio names fuzzily (substring → token fallback) so "international equity" maps to "International Equity Fund".
- 429 / rate-limit errors are handled with exponential backoff.

---

## Quick Start

### 1 — Prerequisites

```bash
# Python 3.11+
pip install -r requirements.txt
```

### 2 — API key

```bash
cp .env.example .env
# Edit .env and set GEMINI_API_KEY=...
# Get a free key at https://ai.google.dev/gemini-api/docs
```

Optional: override the model name via `GEMINI_MODEL` (default: `gemini-1.5-flash`).

### 3 — Build the database

```bash
python setup_db.py
```

Expected output:
```
INFO: Schema applied.
INFO:   sectors           10 inserted
INFO:   securities        30 inserted
...
INFO: Database ready: 603 rows inserted, 0 rejected → portfolio.db
```

The database is **auto-built** on first agent/evaluator run if `portfolio.db` is missing, so this step is optional.

### 4 — Run the CLI agent

```bash
# Interactive REPL
python -m agent.portfolio_agent

# Single question
python -m agent.portfolio_agent -q "How many portfolios do we have?"
python -m agent.portfolio_agent -q "What are the sector exposures for the Tech Innovation Fund?" --verbose
```

### 5 — Run the evaluator

```bash
python -m agent.evaluator                    # results → eval_results.json
python -m agent.evaluator --verbose          # print agent answers & SQL
python -m agent.evaluator -o my_report.json  # custom output path
```

Sample output:
```
======================================================================
  PL Capital Portfolio Agent — Evaluation Report
======================================================================

[Q01] [EASY  ] [text2sql]
  How many portfolios do we have in total?
  Route ✅ | Answer ✅ | 2.1s | ✓ (unordered, 1 row(s) match)
...

  Routing accuracy : 10/10 (100.0%)
  Answer  accuracy :  9/10  (90.0%)
    Easy    : route 3/3  answer 3/3
    Medium  : route 5/5  answer 4/5
    Hard    : route 2/2  answer 2/2
======================================================================
```

### 6 — Optional Streamlit UI

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`. Features: chat interface, generated SQL display, sector exposure bar chart.

---

## Project Structure

```
portfolio_agent/
├── .env.example                 ← copy to .env, fill GEMINI_API_KEY
├── .gitignore
├── requirements.txt
├── setup_db.py                  ← builds portfolio.db from bundled data
├── database_schema.sql          ← SQLite schema
├── ground_truth_dataset.json    ← 10 evaluation Q&As
├── data/                        ← CSV source files (9 tables)
├── agent/
│   ├── portfolio_agent.py       ← orchestrator (manual function-calling)
│   ├── evaluator.py             ← evaluation script
│   └── tools/
│       ├── sql_tool.py          ← Text-to-SQL: generate → validate → execute → repair
│       └── exposure_tool.py     ← Sector exposure: fuzzy match → equity % → renorm
└── app.py                       ← Streamlit UI (optional)
```

---

## Evaluation Methodology

| Question | Type | Difficulty | Grading |
|----------|------|-----------|---------|
| Q1–Q3 | text2sql | easy | GT SQL run on same DB; rows compared (set equality, float tolerance 1%) |
| Q4–Q6 | text2sql | medium | As above |
| Q7–Q8 | text2sql | hard | As above; Q7 order-sensitive (ORDER BY + LIMIT) |
| Q9–Q10 | exposure_calculator | medium | Correct tool + correct portfolio resolved + equity sum ≈ 100% + no bonds |

Two scores are reported separately:
- **Routing accuracy** — did the agent pick the right tool?
- **Answer accuracy** — does the result match ground truth?
